"""Demonstration data.

Everything here is **fictitious**: the companies, the street, the suppliers and
above all the prices. Price rows are flagged ``is_demo_data=True`` so the quote
preview prints a warning, and no figure should ever be read as a market price.

Run with::

    python -m metreo_api.seed            # create if absent
    python -m metreo_api.seed --reset    # drop and recreate the demo data
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session_factory
from .models import (
    BillOfQuantities,
    BoqItem,
    CompositeComponentRow,
    CompositePriceRow,
    Estimate,
    EstimateVersion,
    Membership,
    Organization,
    OrganizationSettings,
    PriceBook,
    PriceBookVersion,
    PriceItem,
    Project,
    RegionProfile,
    TaxRateRow,
    User,
)
from .services import audit, conservation, estimating
from .services.document_storage import StockageLocal

DEMO_SOURCE = "Donnée de démonstration fictive — ne pas utiliser comme prix de marché"

#: La conservation des devis émis : l'obligation est DÉCLARÉE, sa durée ne l'est pas.
#:
#: `years` reste `None` et le restera tant qu'une source officielle datée ne
#: l'aura pas fixée. Ce n'est pas un oubli : une durée de conservation est une
#: règle réglementaire, elle a un pays, une version, une date d'effet et une
#: source. Écrire « 7 » ici — ou dans un défaut de colonne — serait rendre un
#: avis juridique par une valeur par défaut, ce que ce dépôt ne fait nulle part
#: ailleurs et ne commencera pas à faire pour celle-ci.
#:
#: Tant que `years` est `None`, `services/conservation.py` REFUSE de détruire
#: quoi que ce soit. Le refus est la position sûre : il conserve.
CONSERVATION_DES_DEVIS: dict[str, Any] = {
    "enabled": True,
    "years": None,
    "requires_expert_validation": True,
    "note": (
        "La durée de conservation d'un devis émis et les conditions de son "
        "effacement relèvent du droit applicable. Le pack déclare l'existence "
        "de la question ; il ne fixe aucune durée tant qu'une source officielle "
        "datée ne l'a pas établie et qu'un spécialiste ne l'a pas validée. "
        "Sans durée réglée, la destruction d'une organisation est refusée."
    ),
}

REGION_PACKS: list[dict[str, Any]] = [
    {
        "country_code": "BE",
        "code": "BE-WAL",
        "name": "Wallonie",
        "default_locale": "fr-BE",
        "locales": ["fr-BE", "de-BE"],
        "terminology": {
            "boq": "métré",
            "specification": "cahier spécial des charges",
            "unit_price_schedule": "bordereau de prix unitaires",
            "detailed_quantities": "métré détaillé",
        },
        "rules": {
            "quote_retention": CONSERVATION_DES_DEVIS,
            "soil_traceability": {
                "enabled": True,
                "note": (
                    "La traçabilité des terres excavées relève d'une procédure "
                    "régionale. Aucune règle n'est codée en dur: le pack ne "
                    "déclare que l'existence de l'obligation et exige une "
                    "validation métier."
                ),
                "requires_expert_validation": True,
            },
            "identifiers": {
                "company_number": {
                    "pattern": r"^(BE)?0?\d{9}$",
                    "label": "Numéro d'entreprise (BCE)",
                }
            },
        },
        "sources": [
            {
                "label": "À compléter lors de la validation métier du pack",
                "url": None,
                "checked_on": None,
            }
        ],
        "status": "draft",
        "disclaimer": (
            "Pack régional non validé juridiquement. Les mentions réglementaires "
            "doivent être confirmées par un spécialiste avant toute mise en production."
        ),
    },
    {
        "country_code": "BE",
        "code": "BE-VLG",
        "name": "Vlaanderen",
        "default_locale": "nl-BE",
        "locales": ["nl-BE", "fr-BE"],
        "terminology": {
            "boq": "meetstaat",
            "specification": "bestek",
            "unit_price_schedule": "eenheidsprijzenlijst",
        },
        "rules": {
            "quote_retention": CONSERVATION_DES_DEVIS,
            "soil_traceability": {"enabled": True, "requires_expert_validation": True},
            "identifiers": {
                "company_number": {"pattern": r"^(BE)?0?\d{9}$", "label": "Ondernemingsnummer"}
            },
        },
        "sources": [],
        "status": "draft",
        "disclaimer": "Pack régional non validé juridiquement.",
    },
    {
        "country_code": "BE",
        "code": "BE-BRU",
        "name": "Bruxelles-Capitale / Brussel-Hoofdstad",
        "default_locale": "fr-BE",
        "locales": ["fr-BE", "nl-BE"],
        "terminology": {"boq": "métré", "specification": "cahier spécial des charges"},
        "rules": {
            "quote_retention": CONSERVATION_DES_DEVIS,
            "soil_traceability": {"enabled": True, "requires_expert_validation": True},
        },
        "sources": [],
        "status": "draft",
        "disclaimer": "Pack régional non validé juridiquement.",
    },
    {
        "country_code": "FR",
        "code": "FR",
        "name": "France (préparation)",
        "default_locale": "fr-FR",
        "locales": ["fr-FR"],
        "terminology": {
            "boq": "DPGF",
            "specification": "CCTP",
            "unit_price_schedule": "BPU",
            "detailed_quantities": "DQE",
        },
        "rules": {
            "quote_retention": CONSERVATION_DES_DEVIS,
            "identifiers": {"company_number": {"pattern": r"^\d{9}$", "label": "SIREN"}},
        },
        "sources": [],
        "status": "planned",
        "disclaimer": (
            "Pack France non implémenté: seuls la terminologie et les identifiants "
            "sont préparés dans le modèle de données."
        ),
    },
]

DEMO_PRICES = [
    ("MAT-EMP-001", "Empierrement 0/32 concassé calcaire", "Matériaux", "material", "t", "18.40"),
    ("MAT-BET-010", "Béton maigre C12/15 livré", "Matériaux", "material", "m3", "104.00"),
    (
        "MAT-TUY-160",
        "Tuyau béton non armé DN 400 (barre 2,4 m)",
        "Matériaux",
        "material",
        "m",
        "48.75",
    ),
    (
        "MAT-CHV-001",
        "Chambre de visite préfabriquée DN 800, h ≤ 2 m",
        "Matériaux",
        "material",
        "pce",
        "615.00",
    ),
    ("MAT-HYD-020", "Enrobé hydrocarboné BB-4C, fourniture", "Matériaux", "material", "t", "92.30"),
    (
        "MO-OUV-001",
        "Ouvrier qualifié voirie (coût horaire chargé)",
        "Main-d'œuvre",
        "labor",
        "h",
        "44.20",
    ),
    ("MO-MAN-001", "Manœuvre (coût horaire chargé)", "Main-d'œuvre", "labor", "h", "38.60"),
    ("ENG-PEL-021", "Pelle hydraulique 21 t avec opérateur", "Engins", "equipment", "h", "92.00"),
    ("ENG-COM-001", "Compacteur vibrant 8 t avec opérateur", "Engins", "equipment", "h", "68.00"),
    (
        "TRA-CAM-8X4",
        "Camion 8x4 charge utile 14 t (rotation)",
        "Transport",
        "transport",
        "pce",
        "39.50",
    ),
    (
        "EVA-TER-001",
        "Traitement terres de déblai non polluées",
        "Évacuation",
        "disposal",
        "t",
        "12.90",
    ),
    (
        "STR-MAR-001",
        "Marquage routier (sous-traitance)",
        "Sous-traitance",
        "subcontract",
        "m",
        "3.85",
    ),
]


def _get_or_create_region_packs(session: Session) -> None:
    for pack in REGION_PACKS:
        existing = session.scalars(
            select(RegionProfile).where(
                RegionProfile.code == pack["code"], RegionProfile.version == "2026.1"
            )
        ).one_or_none()
        if existing:
            continue
        session.add(
            RegionProfile(
                country_code=pack["country_code"],
                code=pack["code"],
                name=pack["name"],
                version="2026.1",
                effective_from=date(2026, 1, 1),
                default_locale=pack["default_locale"],
                locales=pack["locales"],
                default_currency="EUR",
                terminology=pack["terminology"],
                rules=pack["rules"],
                sources=pack["sources"],
                status=pack["status"],
                disclaimer=pack["disclaimer"],
            )
        )
    session.flush()


def _create_organization(
    session: Session,
    *,
    name: str,
    legal_name: str,
    company_number: str,
    region_code: str,
    locale: str,
    margin_rate: str,
) -> Organization:
    organization = Organization(
        name=name,
        legal_name=legal_name,
        company_number=company_number,
        country_code="BE",
        region_code=region_code,
        locale=locale,
        currency="EUR",
    )
    session.add(organization)
    session.flush()

    session.add(
        OrganizationSettings(
            organization_id=organization.id,
            rounding_scale=2,
            rounding_mode="half_up",
            unit_price_scale=2,
            site_overheads_rate=Decimal("0.06"),
            site_overheads_base="direct_cost",
            general_overheads_rate=Decimal("0.09"),
            general_overheads_base="direct_plus_site",
            contingency_rate=Decimal("0.03"),
            contingency_base="running_total",
            margin_rate=Decimal(margin_rate),
            margin_method="on_cost",
            missing_price_policy="block",
        )
    )
    session.add_all(
        [
            TaxRateRow(
                organization_id=organization.id,
                code="VAT-BE-21",
                label="TVA 21 %",
                rate=Decimal("0.21"),
                applies_from=date(1996, 1, 1),
                is_default=True,
                source="Taux à confirmer par le pack régional validé",
            ),
            TaxRateRow(
                organization_id=organization.id,
                code="VAT-BE-06",
                label="TVA 6 % (cas particuliers)",
                rate=Decimal("0.06"),
                applies_from=date(1996, 1, 1),
                is_default=False,
                source="Taux à confirmer par le pack régional validé",
            ),
        ]
    )
    session.flush()
    return organization


def _add_user(
    session: Session,
    organization: Organization,
    *,
    email: str,
    full_name: str,
    role: str,
    locale: str,
) -> User:
    user = session.scalars(select(User).where(User.email == email)).one_or_none()
    if user is None:
        user = User(email=email, full_name=full_name, locale=locale)
        session.add(user)
        session.flush()
    session.add(Membership(user_id=user.id, organization_id=organization.id, role=role))
    session.flush()
    return user


def _seed_price_book(session: Session, organization: Organization) -> PriceBookVersion:
    book = PriceBook(
        organization_id=organization.id,
        name="Bibliothèque interne (démonstration)",
        description=DEMO_SOURCE,
        currency="EUR",
        is_default=True,
    )
    session.add(book)
    session.flush()
    version = PriceBookVersion(
        organization_id=organization.id,
        price_book_id=book.id,
        version_number=1,
        label="Édition 2026 — données fictives",
        status="draft",
    )
    session.add(version)
    session.flush()

    for code, label, family, kind, unit, price in DEMO_PRICES:
        session.add(
            PriceItem(
                organization_id=organization.id,
                price_book_version_id=version.id,
                code=code,
                label=label,
                family=family,
                resource_kind=kind,
                unit_code=unit,
                unit_price=Decimal(price),
                currency="EUR",
                region_code=organization.region_code,
                valid_from=date(2026, 1, 1),
                source=DEMO_SOURCE,
                # `confidence` dit d'où vient le prix — déclaré, coté,
                # contractualisé, estimé. Le caractère fictif est porté par
                # `is_demo_data`, et confondre les deux fabriquait une valeur
                # « demo » qu'aucun contrat n'accepte : le jeu de démonstration
                # écrivait ce que l'API aurait refusé.
                confidence="declared",
                is_demo_data=True,
            )
        )
    session.flush()
    return version


def _seed_composites(
    session: Session, organization: Organization, version: PriceBookVersion
) -> dict[str, CompositePriceRow]:
    """Two sub-details that exercise the whole engine.

    The excavation one is acceptance scenario 3: a productivity rate, several
    resources, a truck rotation and a m3 -> t conversion backed by a sourced
    density.
    """
    density_source = "Rapport géotechnique fictif GT-2026-018, p. 12 (essai 3)"

    excavation = CompositePriceRow(
        organization_id=organization.id,
        price_book_version_id=version.id,
        code="SD-TER-EXC",
        label="Excavation en terrain meuble, chargement, évacuation et traitement",
        unit_code="m3",
        notes=DEMO_SOURCE,
        is_demo_data=True,
    )
    session.add(excavation)
    session.flush()
    session.add_all(
        [
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=excavation.id,
                sort_index=0,
                component_type="output_rate",
                label="Pelle hydraulique 21 t avec opérateur",
                resource_kind="equipment",
                output_rate=Decimal("28"),
                hourly_rate=Decimal("92.00"),
                crew_size=Decimal("1"),
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=excavation.id,
                sort_index=1,
                component_type="output_rate",
                label="Manœuvre en accompagnement",
                resource_kind="labor",
                output_rate=Decimal("28"),
                hourly_rate=Decimal("38.60"),
                crew_size=Decimal("1"),
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=excavation.id,
                sort_index=2,
                component_type="rotation",
                label="Évacuation par camion 8x4 (14 t utiles), 18 km",
                resource_kind="transport",
                payload_value=Decimal("14"),
                payload_unit_code="t",
                cost_per_rotation=Decimal("39.50"),
                round_up=True,
                distance_km=Decimal("18"),
                rate_per_km=Decimal("1.10"),
                density_value=Decimal("1800"),
                density_source=density_source,
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=excavation.id,
                sort_index=3,
                component_type="consumption",
                label="Traitement en centre agréé (terres non polluées)",
                resource_kind="disposal",
                consumption=Decimal("1"),
                resource_unit_code="t",
                unit_price=Decimal("12.90"),
                convert_boq_quantity=True,
                density_value=Decimal("1800"),
                density_source=density_source,
            ),
        ]
    )

    stripping = CompositePriceRow(
        organization_id=organization.id,
        price_book_version_id=version.id,
        code="SD-TER-DEC",
        label="Décapage de terre arable sur 25 cm, mise en dépôt sur site",
        unit_code="m2",
        notes=DEMO_SOURCE,
        is_demo_data=True,
    )
    session.add(stripping)
    session.flush()
    session.add_all(
        [
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=stripping.id,
                sort_index=0,
                component_type="output_rate",
                label="Pelle hydraulique 21 t avec opérateur",
                resource_kind="equipment",
                output_rate=Decimal("220"),
                hourly_rate=Decimal("92.00"),
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=stripping.id,
                sort_index=1,
                component_type="output_rate",
                label="Manœuvre en accompagnement",
                resource_kind="labor",
                output_rate=Decimal("220"),
                hourly_rate=Decimal("38.60"),
            ),
        ]
    )

    sub_base = CompositePriceRow(
        organization_id=organization.id,
        price_book_version_id=version.id,
        code="SD-VOI-SFO",
        label="Sous-fondation en empierrement 0/32, épaisseur 25 cm, compactée",
        unit_code="m2",
        notes=DEMO_SOURCE,
        is_demo_data=True,
    )
    session.add(sub_base)
    session.flush()
    session.add_all(
        [
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=sub_base.id,
                sort_index=0,
                component_type="consumption",
                label="Empierrement 0/32 (0,25 m × 2,05 t/m³, perte 4 %)",
                resource_kind="material",
                consumption=Decimal("0.5125"),
                resource_unit_code="t",
                unit_price=Decimal("18.40"),
                loss_ratio=Decimal("0.04"),
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=sub_base.id,
                sort_index=1,
                component_type="output_rate",
                label="Équipe de mise en œuvre (2 personnes)",
                resource_kind="labor",
                output_rate=Decimal("55"),
                hourly_rate=Decimal("44.20"),
                crew_size=Decimal("2"),
            ),
            CompositeComponentRow(
                organization_id=organization.id,
                composite_price_id=sub_base.id,
                sort_index=2,
                component_type="output_rate",
                label="Compacteur vibrant 8 t",
                resource_kind="equipment",
                output_rate=Decimal("110"),
                hourly_rate=Decimal("68.00"),
            ),
        ]
    )
    session.flush()
    return {"excavation": excavation, "sub_base": sub_base, "stripping": stripping}


def _seed_project(
    session: Session,
    organization: Organization,
    version: PriceBookVersion,
    composites: dict[str, CompositePriceRow],
    author: User,
) -> tuple[Project, BillOfQuantities, Estimate]:
    project = Project(
        organization_id=organization.id,
        reference="2026-014",
        client_reference="CSC/VOI/2026/114 (fictif)",
        name="Réfection de la rue des Ardennes (chantier fictif)",
        description=(
            "Démolition du revêtement existant, remplacement d'un tronçon "
            "d'égouttage, reprise des sous-fondations et pose d'un nouveau "
            "revêtement hydrocarboné. Dossier entièrement fictif."
        ),
        client_name="Commune fictive de Val-sur-Ourthe",
        address="Rue des Ardennes",
        postal_code="5590",
        city="Ciney",
        country_code="BE",
        region_code="BE-WAL",
        market_type="Marché public de travaux — procédure ouverte (fictif)",
        work_categories=["terrassement", "egouttage", "voirie"],
        submission_deadline=datetime.utcnow() + timedelta(days=21),
        currency="EUR",
        locale="fr-BE",
        status="studying",
        created_by=author.id,
    )
    session.add(project)
    session.flush()

    boq = BillOfQuantities(
        organization_id=organization.id,
        project_id=project.id,
        name="Métré interne — variante de base",
        source="manual",
        notes="Quantités fictives établies pour la démonstration.",
    )
    session.add(boq)
    session.flush()

    prices = {
        item.code: item
        for item in session.scalars(
            select(PriceItem).where(PriceItem.price_book_version_id == version.id)
        ).all()
    }

    rows: list[dict[str, Any]] = [
        {
            "position": "1",
            "designation": "TERRASSEMENTS ET DÉMOLITIONS",
            "kind": "section",
            "unit_code": "fft",
        },
        {
            "position": "1.1",
            "designation": "Décapage de la terre arable sur 25 cm, mise en dépôt sur site",
            "unit_code": "m2",
            "quantity": "1450",
            "composite": "stripping",
            "formula": "290 m × 5,00 m = 1450 m²",
        },
        {
            "position": "1.2",
            "designation": "Excavation en terrain meuble, évacuation et traitement en centre agréé",
            "unit_code": "m3",
            "quantity": "620",
            "composite": "excavation",
            "formula": "290 m × 5,00 m × 0,43 m ≈ 620 m³",
        },
        {"position": "2", "designation": "ÉGOUTTAGE", "kind": "section", "unit_code": "fft"},
        {
            "position": "2.1",
            "designation": "Fourniture et pose de tuyaux béton DN 400, y compris lit de pose",
            "unit_code": "m",
            "quantity": "185",
            "price_item": "MAT-TUY-160",
        },
        {
            "position": "2.2",
            "designation": "Chambre de visite préfabriquée DN 800, hauteur ≤ 2 m",
            "unit_code": "pce",
            "quantity": "6",
            "price_item": "MAT-CHV-001",
        },
        {"position": "3", "designation": "VOIRIE", "kind": "section", "unit_code": "fft"},
        {
            "position": "3.1",
            "designation": "Sous-fondation en empierrement 0/32, épaisseur 25 cm, compactée",
            "unit_code": "m2",
            "quantity": "1450",
            "composite": "sub_base",
        },
        {
            "position": "3.2",
            "designation": "Revêtement hydrocarboné BB-4C, épaisseur 5 cm (fourniture)",
            "unit_code": "t",
            "quantity": "174",
            "price_item": "MAT-HYD-020",
            "formula": "1450 m² × 0,05 m × 2,40 t/m³ = 174 t",
        },
        {
            "position": "3.3",
            "designation": "Marquage routier définitif (lot sous-traité)",
            "unit_code": "m",
            "quantity": "580",
            "price_item": "STR-MAR-001",
        },
        {
            "position": "4.1",
            "designation": "Option: revêtement drainant en remplacement du BB-4C",
            "unit_code": "t",
            "quantity": "174",
            "kind": "option",
            "price_item": "MAT-HYD-020",
        },
        {
            "position": "4.2",
            "designation": "Poste non chiffré: mise en conformité des raccordements particuliers",
            "unit_code": "pce",
            "quantity": "12",
            "kind": "provisional",
        },
    ]

    for index, row in enumerate(rows):
        session.add(
            BoqItem(
                organization_id=organization.id,
                boq_id=boq.id,
                sort_index=index * 10,
                position=row["position"],
                designation=row["designation"],
                unit_code=row["unit_code"],
                quantity=Decimal(row.get("quantity", "0")),
                kind=row.get("kind", "item"),
                status="verified",
                formula=row.get("formula"),
                price_item_id=prices[row["price_item"]].id if row.get("price_item") else None,
                composite_price_id=(
                    composites[row["composite"]].id if row.get("composite") else None
                ),
            )
        )
    session.flush()

    estimate = Estimate(
        organization_id=organization.id,
        project_id=project.id,
        boq_id=boq.id,
        price_book_version_id=version.id,
        name="Étude de prix — variante de base",
        currency="EUR",
        created_by=author.id,
    )
    session.add(estimate)
    session.flush()

    settings = session.get(OrganizationSettings, organization.id)
    assert settings is not None
    session.add(
        EstimateVersion(
            organization_id=organization.id,
            estimate_id=estimate.id,
            version_number=1,
            label="Version de travail",
            status="draft",
            price_book_version_id=version.id,
            markup=estimating.markup_to_dict(estimating.markup_from_settings(settings)),
            taxes=estimating.taxes_to_list(estimating.active_taxes(session, organization.id)),
            rounding=estimating.rounding_to_dict(estimating.rounding_from_settings(settings)),
            missing_price_policy=settings.missing_price_policy,
            created_by=author.id,
        )
    )
    session.flush()
    return project, boq, estimate


class SeedRefused(RuntimeError):
    """The demonstration dataset must not be written where it could be believed."""


#: Le nom de chaque organisation semée, écrit UNE fois.
#:
#: Il servait auparavant deux fois : dans l'appel qui crée l'organisation, et
#: dans une liste séparée que `--reset` consultait pour savoir quoi effacer. Les
#: deux avaient divergé. La liste nommait « Voiries & Égouttage Martin SPRL
#: (démo) », que rien ne crée, et ignorait « Wegenbouw Janssens NV (demo) », que
#: le seed crée bel et bien.
#:
#: Conséquence mesurée : `--reset` laissait la seconde organisation en place
#: puis en recréait une copie. Après un reset, la base portait DEUX
#: « Wegenbouw Janssens NV (demo) », et un reset de plus en aurait ajouté une
#: troisième. La garantie affichée — « n'efface que ce qu'il a semé » — était
#: fausse dans l'autre sens : il n'effaçait pas tout ce qu'il avait semé.
#:
#: Une seule source, donc, et un test qui refuse qu'une organisation soit créée
#: sous un nom absent d'ici.
ORGANIZATION_A = "Terrassements Dubois SA (démo)"
ORGANIZATION_B = "Wegenbouw Janssens NV (demo)"

#: Les organisations que ce module sème, et les seules qu'il accepte d'effacer.
SEEDED_ORGANIZATIONS = (ORGANIZATION_A, ORGANIZATION_B)


def seed(session: Session, *, reset: bool = False) -> dict[str, str]:
    """Create the demonstration dataset. Idempotent unless ``reset`` is set.

    Two rules make this the one write command that stays usable on a working
    database. It refuses an environment that says it is not a working one —
    fictional prices presented as real would be worse than no prices at all.
    And ``reset`` only removes what this module seeded: it used to delete
    **every** organisation and **every** user, which on a populated database
    would have taken real data with it.
    """
    settings = get_settings()
    if settings.environment not in {"development", "test"}:
        raise SeedRefused(
            f"environnement « {settings.environment} » : le jeu de démonstration est "
            "entièrement fictif et ne doit jamais être écrit là où il pourrait être "
            "pris pour des données réelles."
        )

    existing = session.scalars(
        select(Organization).where(Organization.name == SEEDED_ORGANIZATIONS[0])
    ).one_or_none()
    if existing is not None:
        if not reset:
            return {"status": "already_seeded", "organization_id": existing.id}
        # Uniquement les organisations que ce module sème, nommément. Un
        # `delete(Organization)` général emporterait les données réelles d'une
        # base de travail — et le nom d'une organisation réelle ne ressemble à
        # rien de particulier.
        seeded = list(
            session.scalars(
                select(Organization).where(Organization.name.in_(SEEDED_ORGANIZATIONS))
            ).all()
        )
        seeded_ids = [organization.id for organization in seeded]
        seeded_users = list(
            session.scalars(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.organization_id.in_(seeded_ids))
                .distinct()
            ).all()
        )
        for organization in seeded:
            # `issued_quotes.organization_id` retient en RESTRICT depuis
            # `a5b6c7d8e9fa` : l'organisation ne part plus tant qu'un devis
            # émis subsiste, et un déclencheur refuse de détruire ce devis sans
            # purge inscrite. C'est voulu — c'était la dernière cascade qui
            # effaçait un devis sans un mot.
            #
            # `--reset` emprunte donc la MÊME porte que tout le monde, avec le
            # seul assouplissement nommé qu'elle admet : il ne touche que les
            # organisations que ce module a semées, retrouvées par leur nom
            # exact, et la ligne de registre reste écrite. Une réinitialisation
            # de démonstration laisse ainsi la même trace qu'une destruction
            # réelle, ce qui est exactement ce qu'on veut vérifier.
            purge = conservation.demander(
                session,
                organization_id=organization.id,
                reason_code="demo_reset",
                sans_retention=True,
            )
            # Demander n'autorise rien : la fenêtre s'ouvre explicitement ici,
            # comme pour une destruction réelle. C'est ce qui rend le geste du
            # reset représentatif — s'il pouvait détruire sans autorisation, il
            # ne prouverait rien du mécanisme.
            conservation.autoriser(session, purge)
            conservation.executer(session, purge)
            # Refermée, et pas laissée en `rows_deleted` : une purge qui ne se
            # referme jamais s'accumulerait dans le registre en donnant à
            # penser qu'elle a échoué. Le jeu de démonstration n'émet aucun
            # devis, donc il n'y a rien à retirer du volume — mais le geste est
            # le même que pour une purge réelle, et c'est ce qui le rend
            # vérifiable.
            conservation.retirer_les_fichiers(
                session, purge, StockageLocal(get_settings().storage_root)
            )
        # Flush the organisation deletes first: their ON DELETE CASCADE clears
        # the rows that still reference users (projects, estimates, audit).
        session.flush()
        for user in seeded_users:
            session.delete(user)
        session.flush()

    _get_or_create_region_packs(session)

    org_a = _create_organization(
        session,
        name=ORGANIZATION_A,
        legal_name="Terrassements Dubois SA",
        company_number="BE0123456789",
        region_code="BE-WAL",
        locale="fr-BE",
        margin_rate="0.08",
    )
    org_b = _create_organization(
        session,
        name=ORGANIZATION_B,
        legal_name="Wegenbouw Janssens NV",
        company_number="BE0987654321",
        region_code="BE-VLG",
        locale="nl-BE",
        margin_rate="0.11",
    )

    admin_a = _add_user(
        session,
        org_a,
        email="admin@dubois.demo",
        full_name="Claire Dubois",
        role="org_admin",
        locale="fr-BE",
    )
    _add_user(
        session,
        org_a,
        email="metreur@dubois.demo",
        full_name="Samir Lambert",
        role="estimator",
        locale="fr-BE",
    )
    _add_user(
        session,
        org_a,
        email="lecteur@dubois.demo",
        full_name="Ana Peeters",
        role="viewer",
        locale="fr-BE",
    )
    admin_b = _add_user(
        session,
        org_b,
        email="admin@janssens.demo",
        full_name="Tom Janssens",
        role="org_admin",
        locale="nl-BE",
    )

    version_a = _seed_price_book(session, org_a)
    composites_a = _seed_composites(session, org_a, version_a)
    project_a, boq_a, estimate_a = _seed_project(session, org_a, version_a, composites_a, admin_a)

    version_b = _seed_price_book(session, org_b)
    _seed_composites(session, org_b, version_b)

    for organization, actor in ((org_a, admin_a), (org_b, admin_b)):
        audit.record(
            session,
            organization_id=organization.id,
            action="organization.seeded",
            object_type="organization",
            object_id=organization.id,
            summary="Jeu de démonstration fictif créé",
            actor_user_id=actor.id,
            actor_email=actor.email,
            payload={"demo_data": True},
        )

    session.commit()
    return {
        "status": "seeded",
        "organization_a": org_a.id,
        "organization_b": org_b.id,
        "project": project_a.id,
        "boq": boq_a.id,
        "estimate": estimate_a.id,
        "price_book_version_a": version_a.id,
        "price_book_version_b": version_b.id,
    }


def main() -> None:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="Charge le jeu de démonstration fictif.")
    parser.add_argument("--reset", action="store_true", help="Supprime puis recrée les données")
    args = parser.parse_args()
    session = get_session_factory()()
    try:
        outcome = seed(session, reset=args.reset)
    finally:
        session.close()
    for key, value in outcome.items():
        print(f"{key}: {value}")


if __name__ == "__main__":  # pragma: no cover - CLI
    main()
