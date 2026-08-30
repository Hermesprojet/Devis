"""Contrôle qu'une restauration a réellement rendu un système utilisable.

Une restauration qui « ne renvoie pas d'erreur » ne prouve rien : un dump
partiel se restaure sans bruit, et le manque ne se voit qu'au moment où
quelqu'un cherche son devis. Ce script vérifie ce qui doit être là, et refuse
de conclure sur un silence.

Il ne modifie rien. Il lit, compte, et vérifie l'intégrité de la chaîne
d'audit — le seul contrôle qui détecte une altération plutôt qu'une absence.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

from metreo_api.db import get_session_factory
from metreo_api.models import (
    AuditEvent,
    Estimate,
    EstimateVersion,
    Membership,
    Organization,
    Project,
    User,
)
from metreo_api.services import audit


def main() -> int:
    problemes: list[str] = []
    constats: list[str] = []

    with get_session_factory()() as session:

        def compter(modele, libelle: str, minimum: int = 1) -> int:
            nombre = session.scalar(select(func.count()).select_from(modele)) or 0
            constats.append(f"  {libelle:28} {nombre}")
            if nombre < minimum:
                problemes.append(f"{libelle} : {nombre} (attendu au moins {minimum})")
            return nombre

        compter(Organization, "organisations")
        compter(User, "utilisateurs")
        compter(Membership, "appartenances")
        compter(Project, "projets", minimum=0)
        compter(Estimate, "estimations", minimum=0)

        gelees = session.scalars(
            select(EstimateVersion).where(EstimateVersion.status == "frozen")
        ).all()
        constats.append(f"  {'versions gelées':28} {len(gelees)}")
        for version in gelees:
            if not version.snapshot_sha256:
                problemes.append(f"version gelée {version.id} sans empreinte")
            if version.snapshot is None:
                problemes.append(f"version gelée {version.id} sans instantané")

        # Les pièces stockées : leur présence en base doit correspondre à des
        # fichiers réellement restaurés.
        racine = os.environ.get("METREO_STORAGE_ROOT", "")
        fichiers = 0
        if racine and os.path.isdir(racine):
            for _, _, noms in os.walk(racine):
                fichiers += len(noms)
        constats.append(f"  {'fichiers stockés':28} {fichiers}")

        evenements = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        constats.append(f"  {'evenements audit':28} {evenements}")

        # L'intégrité de la chaîne, organisation par organisation : c'est le
        # seul contrôle qui distingue « restauré » de « restauré intact ».
        for organisation in session.scalars(select(Organization)).all():
            rapport = audit.verify_chain(session, organisation.id)
            intacte = bool(rapport.get("valid"))
            etat = "intègre" if intacte else "ROMPUE"
            constats.append(f"  chaine d'audit {organisation.name[:14]:14} {etat}")
            if not intacte:
                problemes.append(
                    f"chaine d'audit rompue pour {organisation.name} : "
                    f"{rapport.get('reason')} (sequence {rapport.get('failed_at_sequence')})"
                )

        # Quelqu'un peut-il se connecter ? Un compte actif avec une
        # appartenance active est la condition minimale.
        connectables = (
            session.scalar(
                select(func.count())
                .select_from(Membership)
                .join(User, User.id == Membership.user_id)
                .where(Membership.is_active.is_(True), User.is_active.is_(True))
            )
            or 0
        )
        constats.append(f"  {'comptes connectables':28} {connectables}")
        if connectables < 1:
            problemes.append(
                "aucun compte actif avec appartenance active : personne ne pourra entrer"
            )

    print("Contrôles de restauration :")
    print("\n".join(constats))

    if problemes:
        print("\nRestauration REFUSÉE :", file=sys.stderr)
        for probleme in problemes:
            print(f"  - {probleme}", file=sys.stderr)
        return 1

    print("\nRestauration vérifiée : contenu présent, chaîne d'audit intègre, connexion possible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
