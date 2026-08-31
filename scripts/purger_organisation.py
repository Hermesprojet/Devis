"""Décider la conservation, puis détruire une organisation — par la seule porte.

Ni la purge ni la décision de conservation ne passent par l'API, et c'est
délibéré : l'une détruit un locataire entier, l'autre engage l'entreprise sur
un droit applicable. Ouvrir une route HTTP pour l'une ou l'autre serait une
décision séparée (ADR 0006). Elles se font ici, par un outil d'exploitation
qui montre ce qu'il va faire avant de le faire.

Le script n'invente aucune règle. Il ne peut pas passer outre :
`sans_retention` n'est pas exposé, il est réservé au jeu de démonstration.

    # 1. Décider la conservation — les cinq éléments sont obligatoires
    python scripts/purger_organisation.py --decider <organization_id> \\
        --annees 7 --juridiction BE-WAL \\
        --source "…" --source-verifiee-le 2026-01-15 \\
        --effet-au 2026-01-01 [--source-url …] [--valide-par <user_id>]

    # 2. Voir ce qu'une destruction emporterait — ne détruit rien
    python scripts/purger_organisation.py <organization_id>

    # 3. Détruire
    python scripts/purger_organisation.py <organization_id> \\
        --motif contract_ended [--reference DOSSIER-2026-014] --confirmer

    # 4. Achever une purge interrompue, ou contrôler les orphelins
    python scripts/purger_organisation.py --reprendre <purge_id>
    python scripts/purger_organisation.py --orphelins

Sans `--confirmer`, le script montre et ne détruit rien.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "apps/api/src"))
sys.path.insert(0, str(RACINE / "packages/domain/src"))
sys.path.insert(0, str(RACINE / "packages/contracts/src"))

from metreo_api.config import get_settings  # noqa: E402
from metreo_api.db import get_session_factory  # noqa: E402
from metreo_api.models import MOTIFS_DE_PURGE, Organization, OrganizationPurge  # noqa: E402
from metreo_api.services import conservation  # noqa: E402
from metreo_api.services.document_storage import StockageLocal  # noqa: E402


def _stockage() -> StockageLocal:
    return StockageLocal(get_settings().storage_root)


def _montrer(purge: OrganizationPurge) -> None:
    print(f"  purge           {purge.id}")
    print(f"  organisation    {purge.organization_id}")
    print(f"  état            {purge.status}")
    print(f"  motif           {purge.reason_code}")
    if purge.reference:
        print(f"  référence       {purge.reference}")
    print(f"  durée appliquée {purge.retention_years_applied} an(s)")
    print(f"  devis           {purge.quote_count}")
    for entree in purge.documents:
        print(f"    · {entree['number']}  {entree['sha256'][:16]}…  {entree['storage_key']}")


def _decider(arguments: argparse.Namespace) -> int:
    manquants = [
        nom
        for nom, valeur in (
            ("--annees", arguments.annees),
            ("--juridiction", arguments.juridiction),
            ("--source", arguments.source),
            ("--source-verifiee-le", arguments.source_verifiee_le),
            ("--effet-au", arguments.effet_au),
        )
        if valeur is None
    ]
    if manquants:
        print(
            "une décision de conservation ne se prend pas à moitié — il manque : "
            + ", ".join(manquants),
            file=sys.stderr,
        )
        return 1

    with get_session_factory()() as session:
        if session.get(Organization, arguments.decider) is None:
            print(f"organisation introuvable : {arguments.decider}", file=sys.stderr)
            return 1
        try:
            decision = conservation.decider(
                session,
                organization_id=arguments.decider,
                years=arguments.annees,
                jurisdiction=arguments.juridiction,
                source_label=arguments.source,
                source_url=arguments.source_url,
                source_checked_on=date.fromisoformat(arguments.source_verifiee_le),
                effective_from=date.fromisoformat(arguments.effet_au),
                validated_by=arguments.valide_par,
            )
        except conservation.PurgeRefusee as refus:
            print(f"REFUSÉ [{refus.code}] {refus.message}", file=sys.stderr)
            return 1
        session.commit()
        print("DÉCISION ENREGISTRÉE")
        print(f"  décision        {decision.id}")
        print(f"  durée           {decision.years} an(s)")
        print(f"  juridiction     {decision.jurisdiction}")
        print(f"  source          {decision.source_label}")
        print(f"  vérifiée le     {decision.source_checked_on}")
        print(f"  effet au        {decision.effective_from}")
    return 0


def _previsualiser(organization_id: str) -> int:
    with get_session_factory()() as session:
        organisation = session.get(Organization, organization_id)
        if organisation is None:
            print(f"organisation introuvable : {organization_id}", file=sys.stderr)
            return 1
        print(f"organisation    {organisation.name}")

        decision = conservation.decision_active(session, organization_id)
        if decision is None:
            print("conservation    AUCUNE DÉCISION EN VIGUEUR — la destruction sera refusée")
            print()
            print("  Une durée seule n'est pas une décision. Il faut sa juridiction, sa")
            print("  source, la date où cette source a été consultée, sa date d'effet et")
            print("  son validateur. Tant qu'elle manque, le refus conserve. Enregistrez")
            print("  la décision avec --decider avant de recommencer.")
            return 1
        print(f"conservation    {decision.years} an(s) — {decision.jurisdiction}")
        print(f"                {decision.source_label}")
        print(
            f"                vérifiée le {decision.source_checked_on}, "
            f"effet au {decision.effective_from}"
        )

        retenus = conservation.devis_retenus(
            session,
            organization_id,
            annees=decision.years,
            aujourdhui=conservation.utcnow().date(),
        )
        if retenus:
            print(f"retenus         {len(retenus)} devis encore dans leur durée :")
            for devis in retenus:
                print(
                    f"    · {devis.number} émis le {devis.issued_at.date()} — "
                    f"libre le {conservation.echeance(devis.issued_at, decision.years)}"
                )
            return 1

        documents = conservation.documents_a_detruire(session, organization_id)
        print(f"à détruire      {len(documents)} devis émis et leurs PDF")
        for doc in documents:
            print(f"    · {doc.number}  {doc.sha256[:16]}…")
    return 0


def _purger(organization_id: str, motif: str, reference: str | None) -> int:
    with get_session_factory()() as session:
        try:
            purge = conservation.demander(
                session,
                organization_id=organization_id,
                reason_code=motif,
                reference=reference,
            )
        except conservation.PurgeRefusee as refus:
            print(f"REFUSÉ [{refus.code}] {refus.message}", file=sys.stderr)
            return 1
        identifiant = purge.id
        # Demander n'autorise rien : la fenêtre s'ouvre ici, juste avant de
        # détruire, et la base la vérifiera elle-même à chaque ligne.
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        session.commit()

    with get_session_factory()() as session:
        inscrite = session.get(OrganizationPurge, identifiant)
        assert inscrite is not None
        purge = inscrite
        conservation.retirer_les_fichiers(session, purge, _stockage())
        session.commit()
        print("PURGE TERMINÉE" if purge.status == "completed" else "PURGE INCOMPLÈTE")
        _montrer(purge)
        print(f"  fichiers retirés {purge.files_deleted}")
        if purge.files_failed:
            print(f"  fichiers en échec {len(purge.files_failed)} — relancez --reprendre")
            return 1
    return 0


def _reprendre(purge_id: str) -> int:
    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, purge_id)
        if purge is None:
            print(f"purge introuvable : {purge_id}", file=sys.stderr)
            return 1
        try:
            conservation.reprendre(session, purge, _stockage())
        except conservation.PurgeRefusee as refus:
            print(f"REFUSÉ [{refus.code}] {refus.message}", file=sys.stderr)
            return 1
        session.commit()
        _montrer(purge)
        return 0 if purge.status == "completed" else 1


def _orphelins() -> int:
    with get_session_factory()() as session:
        restants = conservation.orphelins(session, _stockage())
    if not restants:
        print("aucun fichier orphelin : toutes les purges inscrites sont achevées")
        return 0
    print(f"{len(restants)} fichier(s) nommé(s) par une purge et encore présent(s) :")
    for cle in restants:
        print(f"    · {cle}")
    return 1


def main() -> int:
    analyseur = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    analyseur.add_argument("organization_id", nargs="?", help="l'organisation à détruire")
    analyseur.add_argument(
        "--motif",
        default="",
        choices=list(MOTIFS_DE_PURGE),
        help="pourquoi — un code, obligatoire pour détruire",
    )
    analyseur.add_argument(
        "--reference", help="référence de dossier, opaque : ni blanc ni ponctuation de phrase"
    )
    analyseur.add_argument(
        "--confirmer", action="store_true", help="détruit réellement ; sans lui, montre seulement"
    )
    analyseur.add_argument("--reprendre", metavar="PURGE_ID", help="achève une purge interrompue")
    analyseur.add_argument(
        "--orphelins", action="store_true", help="liste les fichiers qu'une purge n'a pas retirés"
    )

    decision = analyseur.add_argument_group("décision de conservation")
    decision.add_argument("--decider", metavar="ORG_ID", help="enregistre une décision")
    decision.add_argument("--annees", type=int, help="durée, en années calendaires")
    decision.add_argument("--juridiction", help="le droit applicable, ex. BE-WAL")
    decision.add_argument("--source", help="le TEXTE dont la durée est tirée")
    decision.add_argument("--source-url", help="où le lire")
    decision.add_argument("--source-verifiee-le", help="AAAA-MM-JJ")
    decision.add_argument("--effet-au", help="AAAA-MM-JJ")
    decision.add_argument("--valide-par", help="identifiant de l'utilisateur qui valide")

    arguments = analyseur.parse_args()

    if arguments.decider:
        return _decider(arguments)
    if arguments.orphelins:
        return _orphelins()
    if arguments.reprendre:
        return _reprendre(arguments.reprendre)
    if not arguments.organization_id:
        analyseur.error("indiquez une organisation, --decider, --reprendre ou --orphelins")

    code = _previsualiser(arguments.organization_id)
    if code != 0 or not arguments.confirmer:
        if code == 0:
            print()
            print("Rien n'a été détruit. Ajoutez --confirmer et --motif pour exécuter.")
        return code
    if not arguments.motif:
        print(
            "--motif est obligatoire : une destruction sans motif inscrit est refusée. "
            f"Motifs admis : {', '.join(MOTIFS_DE_PURGE)}",
            file=sys.stderr,
        )
        return 1
    return _purger(arguments.organization_id, arguments.motif, arguments.reference)


if __name__ == "__main__":
    raise SystemExit(main())
