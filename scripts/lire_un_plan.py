"""Lire un plan hors de toute requête HTTP, un fichier à la fois.

    python3 scripts/lire_un_plan.py --organization-id ORG --revision-id REV
    python3 scripts/lire_un_plan.py --organization-id ORG --revision-id REV --relancer
    python3 scripts/lire_un_plan.py --organization-id ORG --revision-id REV --delai 300

**Pourquoi ce script existe alors que l'API sait déjà analyser un plan.**
L'API le fait DANS la requête, et une requête ne peut pas durer. Mesuré sur
deux plans d'exécution réels, l'analyse complète prend 7,3 s et 8,8 s ; un
plan plus lourd en prendrait davantage, et `METREO_PLAN_SYNC_MAX_BYTES`
(12 Mio par défaut) le refuse pour cette raison. Ici, personne n'attend la
réponse : la seule borne est le délai qu'on arme soi-même.

**Il n'y a pas de file d'attente, et ce script n'en invente pas.** Rien dans
le dépôt ne dépile quoi que ce soit : `apps/worker/` ne contient aucun
exécuteur et aucune table de travaux n'existe. Ce script traite UNE révision,
nommée sur la ligne de commande, et s'arrête. C'est la forme exigée par
`docs/adr/0007-lecture-de-plans.md` — un processus par fichier, borné par son
appelant — et c'est la seule honnête : prétendre dépiler demanderait une file.

**Le délai est armé ici, et c'est le seul endroit où il peut l'être.** Aucune
borne interne n'interrompt une boucle de rendu déjà commencée : mesuré, une
seule entité de hachure a tenu le processus 10,8 s, et une hachure plus large
le fait tuer par le noyau sans qu'aucune exception ne soit levée. Un signal
posé par le processus lui-même est donc la seule interruption qui marche — et
elle ne marche que depuis le fil principal d'un processus, ce qu'un script
est et qu'un serveur n'est pas.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path
from types import FrameType

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "apps/api/src"))
sys.path.insert(0, str(RACINE / "packages/domain/src"))
sys.path.insert(0, str(RACINE / "packages/contracts/src"))

from metreo_api.config import get_settings  # noqa: E402
from metreo_api.logging_config import configure_logging  # noqa: E402
from metreo_api.services import documents, lecture_de_plan, mesures_de_plan  # noqa: E402
from metreo_api.services.document_storage import StockageLocal  # noqa: E402
from metreo_api.services.travail_documentaire import (  # noqa: E402
    TravailRefuse,
    executer_etape,
    ouvrir_session,
)

#: Le délai par défaut, par étape. Trois fois la plus lente des analyses
#: mesurées (8,8 s), ce qui laisse passer un plan bien plus lourd sans laisser
#: un rendu pathologique tenir la machine indéfiniment.
DELAI_PAR_DEFAUT = 180


class DelaiDepasse(Exception):
    """Le délai armé par ce script a mordu."""


def _armer_le_delai(secondes: int) -> None:
    def _interrompre(_signal: int, _pile: FrameType | None) -> None:
        raise DelaiDepasse(f"délai de {secondes} s dépassé")

    signal.signal(signal.SIGALRM, _interrompre)
    signal.alarm(secondes)


def _desarmer() -> None:
    signal.alarm(0)


def _stockage() -> StockageLocal:
    return StockageLocal(get_settings().storage_root)


def _verifier_la_configuration() -> int:
    """Un worker muet sur sa configuration est un worker qui mentira.

    Les deux pièges valent d'être dits à voix haute : une racine de stockage
    RELATIVE — et le défaut `./var/storage` en est une — désigne un dossier
    différent selon le répertoire de lancement, et le worker lirait alors dans
    un volume vide sans la moindre erreur.
    """
    reglages = get_settings()
    problemes = reglages.validate_startup()
    for probleme in problemes:
        print(f"configuration   {probleme}")

    if not Path(reglages.storage_root).is_absolute():
        print(
            "configuration   METREO_STORAGE_ROOT est RELATIF "
            f"({reglages.storage_root}) : il désigne un dossier différent "
            "selon le répertoire de lancement. Donnez un chemin absolu, "
            "identique à celui de l'API."
        )
        return 1
    return 0


def _montrer_le_constat(organization_id: str, revision_id: str) -> None:
    constat = lecture_de_plan.lire_le_constat(
        _stockage(), organization_id=organization_id, revision_id=revision_id
    )
    if constat is None:
        print("constat         absent")
        return
    contenu = constat.contenu
    print(f"unité           {contenu.get('unite_source') or 'INCONNUE'}")
    print(f"mesurable       {'oui' if contenu.get('mesurable') else 'non'}")
    print(f"cotations lues  {contenu.get('cotations_lues')}")
    print(f"calques         {len(contenu.get('calques') or {})}")
    for anomalie in contenu.get("anomalies") or []:
        print(f"    · {anomalie.get('code')}")
    with ouvrir_session() as session:
        mesures = mesures_de_plan.lister(
            session, organization_id=organization_id, revision_id=revision_id
        )
    a_verifier = sum(1 for m in mesures if m.fiabilite == "a_confirmer")
    print(f"propositions    {len(mesures)} dont {a_verifier} à vérifier")
    print(
        "image           "
        + (
            "produite"
            if lecture_de_plan.image_disponible(
                _stockage(), organization_id=organization_id, revision_id=revision_id
            )
            else "absente"
        )
    )


def _lire(organization_id: str, revision_id: str, *, relancer: bool, delai: int) -> int:
    stockage = _stockage()

    with ouvrir_session() as session:
        try:
            revision = documents.revision_par_id(
                session, organization_id=organization_id, revision_id=revision_id
            )
        except documents.RevisionRefusee:
            print("refus           révision introuvable pour cette organisation")
            return 1
        try:
            lecture_de_plan.verifier_que_cest_un_plan(revision)
        except lecture_de_plan.PlanNonLisible as refus:
            print(f"refus           {refus.code} — {refus.message}")
            return 1
        print(f"révision        {revision.revision_number}, {revision.byte_size} octets")

    etapes = (
        (lecture_de_plan.ETAPE_LECTURE, lecture_de_plan.travail_de_lecture(stockage)),
        (lecture_de_plan.ETAPE_RENDU, lecture_de_plan.travail_de_rendu(stockage)),
    )
    code_de_sortie = 0
    for etape, travail in etapes:
        _armer_le_delai(delai)
        try:
            issue = executer_etape(
                organization_id=organization_id,
                revision_id=revision_id,
                step=etape,
                pipeline_version=mesures_de_plan.PIPELINE_VERSION,
                prompt_version=mesures_de_plan.PROMPT_VERSION,
                model_version=mesures_de_plan.MODEL_VERSION,
                stockage=stockage,
                travail=travail,
                relancer=relancer,
            )
        except TravailRefuse as refus:
            print(f"{etape:15} refusée — {refus.code}")
            if refus.code != "etape_deja_reussie":
                code_de_sortie = 1
            continue
        except DelaiDepasse as expire:
            # L'étape a été laissée ouverte par l'interruption :
            # `executer_etape` l'a close en échec avant de laisser remonter.
            print(f"{etape:15} interrompue — {expire}")
            code_de_sortie = 1
            continue
        finally:
            _desarmer()
        print(f"{etape:15} {issue.statut} en {issue.duree_ms} ms (tentative {issue.tentative})")
        if issue.code_erreur is not None:
            print(f"                code {issue.code_erreur}")
            code_de_sortie = 1

    _montrer_le_constat(organization_id, revision_id)
    return code_de_sortie


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="lire_un_plan",
        description="Lit un plan DXF déposé et propose ses mesures, hors requête HTTP.",
    )
    analyseur.add_argument("--organization-id", required=True)
    analyseur.add_argument("--revision-id", required=True)
    analyseur.add_argument(
        "--relancer",
        action="store_true",
        help="reprendre une étape en échec, sans changer sa clé d'idempotence",
    )
    analyseur.add_argument(
        "--delai",
        type=int,
        default=DELAI_PAR_DEFAUT,
        help=f"délai maximal par étape, en secondes (défaut : {DELAI_PAR_DEFAUT})",
    )
    arguments = analyseur.parse_args(argv)

    configure_logging()
    if _verifier_la_configuration() != 0:
        return 1

    # Des identifiants et des compteurs, jamais un nom de fichier ni un chemin.
    print(f"organisation    {arguments.organization_id}")
    print(f"révision        {arguments.revision_id}")
    return _lire(
        arguments.organization_id,
        arguments.revision_id,
        relancer=arguments.relancer,
        delai=arguments.delai,
    )


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
