"""Créer la première organisation et son administrateur, sans mot de passe.

Un déploiement neuf n'a aucun compte. Le jeu de démonstration n'a rien à y
faire — il porte des données fictives d'une entreprise qui n'existe pas — et
`seed --reset` effacerait le travail réel. Il faut donc un chemin distinct,
minimal et sûr.

Cette commande ne crée **aucun mot de passe**. L'administrateur se connectera
par le fournisseur d'identité, et la liaison se fera à sa première connexion
sur son adresse vérifiée. Ce que l'on précrée ici, c'est le droit d'entrer,
pas un moyen d'entrer.

Elle est **idempotente** : relancée avec les mêmes valeurs, elle ne duplique
rien et ne modifie rien. C'est la propriété qui permet de la mettre dans un
script de démarrage sans réfléchir à la fois où elle a déjà tourné.
"""

from __future__ import annotations

import argparse
import sys

from pydantic import EmailStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session_factory
from .models import Membership, Organization, OrganizationSettings, User
from .security.roles import Role


def bootstrap(
    session: Session,
    *,
    organization_name: str,
    admin_email: str,
    admin_full_name: str,
    country_code: str = "BE",
    region_code: str = "BE-WAL",
    locale: str = "fr-BE",
) -> tuple[Organization, User, bool]:
    """Assure l'existence de l'organisation, de l'administrateur et du lien.

    Rend le triplet et un booléen disant si quelque chose a été créé — pour
    que l'appelant puisse le dire sans avoir à comparer des états.
    """
    # La MÊME validation que celle de la connexion, et pas un simple « @ ».
    #
    # Sans cela, `--admin-email admin@entreprise.invalid` était accepté, la
    # ligne écrite en base, la commande annonçait un succès — et ce compte ne
    # pouvait jamais se connecter : `.invalid` est un nom réservé que la
    # validation d'adresse de l'API refuse. Le premier administrateur d'un
    # déploiement neuf est précisément le compte qu'on ne peut pas se
    # permettre de créer inutilisable : personne d'autre ne peut entrer pour
    # le corriger.
    courriel = admin_email.strip().lower()
    try:
        courriel = TypeAdapter(EmailStr).validate_python(courriel)
    except PydanticValidationError as exc:
        motif = exc.errors()[0].get("msg", "adresse refusée") if exc.errors() else "adresse refusée"
        raise ValueError(f"Adresse d'administrateur invalide : {admin_email!r} — {motif}") from exc
    if not organization_name.strip():
        raise ValueError("Le nom de l'organisation ne peut pas être vide.")

    cree = False

    organisation = session.scalars(
        select(Organization).where(Organization.name == organization_name)
    ).one_or_none()
    if organisation is None:
        organisation = Organization(
            name=organization_name,
            country_code=country_code,
            region_code=region_code,
            locale=locale,
            currency="EUR",
            timezone="Europe/Brussels",
        )
        session.add(organisation)
        session.flush()
        # Les réglages ne sont pas facultatifs : le moteur de calcul les lit à
        # chaque chiffrage, et une organisation sans réglages échoue au premier
        # devis plutôt qu'à sa création.
        session.add(OrganizationSettings(organization_id=organisation.id))
        cree = True

    utilisateur = session.scalars(select(User).where(User.email == courriel)).one_or_none()
    if utilisateur is None:
        utilisateur = User(
            email=courriel,
            full_name=admin_full_name or courriel,
            locale=locale,
            is_active=True,
        )
        session.add(utilisateur)
        session.flush()
        cree = True

    appartenance = session.scalars(
        select(Membership).where(
            Membership.user_id == utilisateur.id,
            Membership.organization_id == organisation.id,
        )
    ).one_or_none()
    if appartenance is None:
        session.add(
            Membership(
                user_id=utilisateur.id,
                organization_id=organisation.id,
                role=Role.ORG_ADMIN.value,
                is_active=True,
            )
        )
        cree = True
    elif not appartenance.is_active:
        # Réactiver est le seul cas où l'on modifie quelque chose d'existant :
        # une appartenance désactivée par erreur bloque totalement l'accès, et
        # relancer le bootstrap est le geste naturel pour s'en sortir.
        appartenance.is_active = True
        cree = True

    return organisation, utilisateur, cree


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="python -m metreo_api.bootstrap",
        description=(
            "Crée l'organisation initiale et son administrateur. "
            "Idempotent, sans mot de passe, sans données de démonstration."
        ),
    )
    parseur.add_argument("--organization", required=True, help="Nom de l'organisation initiale")
    parseur.add_argument("--admin-email", required=True, help="Adresse du premier administrateur")
    parseur.add_argument("--admin-name", default="", help="Nom affiché de l'administrateur")
    parseur.add_argument("--country", default="BE")
    parseur.add_argument("--region", default="BE-WAL")
    parseur.add_argument("--locale", default="fr-BE")
    arguments = parseur.parse_args(argv)

    with get_session_factory()() as session:
        try:
            organisation, utilisateur, cree = bootstrap(
                session,
                organization_name=arguments.organization,
                admin_email=arguments.admin_email,
                admin_full_name=arguments.admin_name,
                country_code=arguments.country,
                region_code=arguments.region,
                locale=arguments.locale,
            )
        except ValueError as erreur:
            print(f"bootstrap refusé : {erreur}", file=sys.stderr)
            return 2
        session.commit()
        etat = "créé" if cree else "déjà en place"
        print(
            f"organisation={organisation.name!r} id={organisation.id} "
            f"admin={utilisateur.email} role=org_admin ({etat})"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
