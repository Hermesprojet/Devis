"""Détruire une organisation, par la seule porte qui le permet.

La destruction d'une organisation n'est PAS exposée en HTTP, et c'est
délibéré : une route qui efface un locataire entier a un rayon d'action
considérable, personne ne l'a demandée, et l'ouvrir serait une décision
séparée (ADR 0006). Elle se fait donc ici, par un outil d'exploitation qui
montre ce qu'il va détruire et demande confirmation.

Le script n'invente aucune règle : il appelle `services/conservation.py`, qui
refuse tant que la durée de conservation n'est pas réglée ou pas échue. Il ne
peut pas passer outre — `sans_retention` n'est pas exposé ici, il est réservé
au jeu de démonstration.

    python scripts/purger_organisation.py <organization_id> --motif "..."
    python scripts/purger_organisation.py <organization_id> --motif "..." --confirmer
    python scripts/purger_organisation.py --reprendre <purge_id>
    python scripts/purger_organisation.py --orphelins

Sans `--confirmer`, le script montre et ne détruit rien.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "apps/api/src"))
sys.path.insert(0, str(RACINE / "packages/domain/src"))
sys.path.insert(0, str(RACINE / "packages/contracts/src"))

from metreo_api.config import get_settings  # noqa: E402
from metreo_api.db import get_session_factory  # noqa: E402
from metreo_api.models import Organization, OrganizationPurge  # noqa: E402
from metreo_api.services import conservation  # noqa: E402
from metreo_api.services.document_storage import StockageLocal  # noqa: E402


def _stockage() -> StockageLocal:
    return StockageLocal(get_settings().storage_root)


def _montrer(purge: OrganizationPurge) -> None:
    print(f"  purge          {purge.id}")
    print(f"  organisation   {purge.organization_id}")
    print(f"  état           {purge.status}")
    print(f"  durée appliquée {purge.retention_years_applied} an(s)")
    print(f"  devis          {purge.quote_count}")
    for entree in purge.documents:
        print(f"    · {entree['number']}  {entree['sha256'][:16]}…  {entree['storage_key']}")


def _previsualiser(organization_id: str) -> int:
    with get_session_factory()() as session:
        organisation = session.get(Organization, organization_id)
        if organisation is None:
            print(f"organisation introuvable : {organization_id}", file=sys.stderr)
            return 1
        annees = conservation.politique(session, organization_id)
        print(f"organisation   {organisation.name}")
        if annees is None:
            print("durée réglée   AUCUNE — la destruction sera refusée")
            print()
            print("  Une durée de conservation est une règle réglementaire : elle a une")
            print("  source officielle datée et demande une validation de spécialiste.")
            print("  Tant qu'elle n'est pas réglée, le refus conserve. Réglez-la sur")
            print("  l'organisation avant de recommencer.")
            return 1
        retenus = conservation.devis_retenus(
            session, organization_id, annees=annees, aujourdhui=conservation.utcnow().date()
        )
        print(f"durée réglée   {annees} an(s)")
        if retenus:
            print(f"retenus        {len(retenus)} devis encore dans leur durée :")
            for devis in retenus:
                print(
                    f"    · {devis.number} émis le {devis.issued_at.date()} — "
                    f"libre le {conservation.echeance(devis.issued_at, annees)}"
                )
            return 1
        documents = conservation.documents_a_detruire(session, organization_id)
        print(f"à détruire     {len(documents)} devis émis et leurs PDF")
        for doc in documents:
            print(f"    · {doc.number}  {doc.sha256[:16]}…")
    return 0


def _purger(organization_id: str, motif: str) -> int:
    with get_session_factory()() as session:
        try:
            purge = conservation.demander(session, organization_id=organization_id, reason=motif)
        except conservation.PurgeRefusee as refus:
            print(f"REFUSÉ [{refus.code}] {refus.message}", file=sys.stderr)
            return 1
        identifiant = purge.id
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
        conservation.reprendre(session, purge, _stockage())
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
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("organization_id", nargs="?", help="l'organisation à détruire")
    analyseur.add_argument("--motif", default="", help="pourquoi — obligatoire pour détruire")
    analyseur.add_argument(
        "--confirmer", action="store_true", help="détruit réellement ; sans lui, montre seulement"
    )
    analyseur.add_argument("--reprendre", metavar="PURGE_ID", help="achève une purge interrompue")
    analyseur.add_argument(
        "--orphelins", action="store_true", help="liste les fichiers qu'une purge n'a pas retirés"
    )
    arguments = analyseur.parse_args()

    if arguments.orphelins:
        return _orphelins()
    if arguments.reprendre:
        return _reprendre(arguments.reprendre)
    if not arguments.organization_id:
        analyseur.error("indiquez une organisation, --reprendre ou --orphelins")

    code = _previsualiser(arguments.organization_id)
    if code != 0 or not arguments.confirmer:
        if code == 0:
            print()
            print("Rien n'a été détruit. Ajoutez --confirmer et --motif pour exécuter.")
        return code
    if not arguments.motif.strip():
        print(
            "--motif est obligatoire : une destruction sans raison écrite est refusée",
            file=sys.stderr,
        )
        return 1
    return _purger(arguments.organization_id, arguments.motif)


if __name__ == "__main__":
    raise SystemExit(main())
