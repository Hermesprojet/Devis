"""Émettre un devis : le numéroter, le figer, l'imprimer, le ranger.

Une version gelée est un calcul reproductible. Ce n'est pas encore un document
commercial : il lui manque un numéro, une date, une validité, un destinataire,
et un fichier que l'entreprise peut transmettre.

L'émission fabrique les quatre instantanés — organisation, client, chantier,
document — et le PDF qui en découle. **Le PDF ne lit jamais les tables
vivantes.** Modifier ensuite la fiche client, les taux ou l'organisation ne
change donc pas un devis déjà remis : c'est la propriété que ce module existe
pour tenir, et elle est éprouvée par `test_devis_emis.py`.

L'ordre des gestes est choisi, comme pour le dépôt d'une pièce de chantier :

1. les refus d'abord — version gelée, client suffisant, pas déjà émise ;
2. le numéro ensuite, sous le verrou de séquence de l'organisation ;
3. les instantanés, puis le PDF, calculés à partir d'eux seuls ;
4. le fichier écrit sur le volume, avec sa compensation ;
5. la ligne, puis l'audit.

Le `commit` n'appartient pas à ce module : `RouteTransactionnelle` valide avant
que la réponse parte. Si quoi que ce soit échoue après l'écriture du fichier —
y compris la validation elle-même —, la compensation retire les octets et
aucun devis partiel ne subsiste.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..models import (
    Client,
    Estimate,
    EstimateVersion,
    IssuedQuote,
    Organization,
    OrganizationSettings,
    Project,
    new_id,
    utcnow,
)
from ..security.auth import TenantContext
from ..transactions import compenser
from . import audit, numerotation
from .document_storage import StockageLocal
from .quote_pdf import composer_le_devis
from .tenant import get_owned

#: Validité par défaut, en jours, quand l'émetteur n'en propose pas.
VALIDITE_PAR_DEFAUT = 30


class EmissionRefusee(DomainError):
    """Un refus que l'écran doit pouvoir expliquer à qui émet."""

    def __init__(self, code: str, message: str, **contexte: Any) -> None:
        super().__init__(message, **contexte)
        self.code = code


# ---------------------------------------------------------------------------
# Le numéro
# ---------------------------------------------------------------------------


def _verrouiller_la_sequence(session: Session, organization_id: str) -> None:
    """Sérialise l'allocation d'un numéro dans cette organisation.

    Le verrou porte sur la ligne `Organization`, exactement comme celui de la
    séquence d'audit, et dans le même mode : `FOR NO KEY UPDATE`. Réutiliser la
    MÊME ligne et le MÊME mode est ce qui garantit l'absence de cycle — deux
    émissions concurrentes se sérialisent, et une émission qui journalise
    ensuite retrouve un verrou qu'elle détient déjà.

    SQLite n'a pas de `SELECT ... FOR UPDATE` et sérialise ses écritures au
    niveau du fichier ; `uq_issued_quote_number` reste le dernier rempart des
    deux côtés.
    """
    if session.bind is not None and session.bind.dialect.name != "sqlite":
        session.execute(
            select(Organization.id)
            .where(Organization.id == organization_id)
            .with_for_update(key_share=True)
        )


def numeroter(
    session: Session, *, organization_id: str, motif: str | None, quand: datetime
) -> tuple[str, int, int]:
    """Rend (numéro, année, rang) — après avoir pris le verrou de séquence.

    Le motif est celui de l'organisation, `quote_number_pattern`, dont le
    défaut est `DEV-{year}-{sequence:04d}`. Il était déjà là, il est vraiment
    exploitable, et il est donc réutilisé plutôt que remplacé.

    Un motif illisible lève `MotifInvalide` : il n'existe plus de numéro de
    secours. Retomber en silence sur le format par défaut faisait partir chez
    le client un numéro qui ne ressemblait pas à celui que l'entreprise croyait
    avoir configuré.

    Le rang repart à 1 chaque année civile. C'est l'usage, et c'est ce que le
    motif par défaut laisse entendre en imprimant l'année.
    """
    _verrouiller_la_sequence(session, organization_id)
    annee = quand.year
    dernier = session.scalar(
        select(func.max(IssuedQuote.sequence_number)).where(
            IssuedQuote.organization_id == organization_id,
            IssuedQuote.sequence_year == annee,
        )
    )
    rang = int(dernier or 0) + 1
    return numerotation.rendre(motif, annee=annee, rang=rang), annee, rang


# ---------------------------------------------------------------------------
# Les instantanés
# ---------------------------------------------------------------------------


def instantane_organisation(
    organization: Organization, reglages: OrganizationSettings | None
) -> dict:
    return {
        "name": organization.name,
        "legal_name": organization.legal_name,
        "company_number": organization.company_number,
        "country_code": organization.country_code,
        "region_code": organization.region_code,
        "currency": organization.currency,
        "locale": organization.locale,
        "quote_number_pattern": getattr(reglages, "quote_number_pattern", None),
    }


def instantane_client(client: Client) -> dict:
    return {
        "id": client.id,
        "name": client.name,
        "company_number": client.company_number,
        "billing_address": client.billing_address,
        "postal_code": client.postal_code,
        "city": client.city,
        "country_code": client.country_code,
        "contact_name": client.contact_name,
        "email": client.email,
        "phone": client.phone,
    }


def instantane_projet(project: Project, estimate: Estimate, version: EstimateVersion) -> dict:
    return {
        "reference": project.reference,
        "client_reference": project.client_reference,
        "name": project.name,
        "address": project.address,
        "postal_code": project.postal_code,
        "city": project.city,
        "country_code": project.country_code,
        "estimate_name": estimate.name,
        "version_number": version.version_number,
        "version_label": version.label,
        "version_sha256": version.snapshot_sha256,
        "currency": estimate.currency,
    }


def client_suffisant(client: Client) -> list[str]:
    """Ce qui manque à une fiche pour qu'un devis lui soit adressable.

    Le minimum est celui d'un courrier : un nom et une adresse postale
    complète. Le reste — numéro d'entreprise, contact, téléphone — dépend du
    client, et l'exiger interdirait d'adresser un devis à un particulier.
    """
    manquants: list[str] = []
    if not (client.name or "").strip():
        manquants.append("name")
    if not (client.billing_address or "").strip():
        manquants.append("billing_address")
    if not (client.postal_code or "").strip():
        manquants.append("postal_code")
    if not (client.city or "").strip():
        manquants.append("city")
    return manquants


# ---------------------------------------------------------------------------
# L'émission
# ---------------------------------------------------------------------------


def emettre(
    session: Session,
    *,
    context: TenantContext,
    estimate: Estimate,
    version: EstimateVersion,
    project: Project,
    organization: Organization,
    reglages: OrganizationSettings | None,
    lignes: list[dict[str, Any]],
    totaux: dict[str, Any],
    stockage: StockageLocal,
    valid_until: date | None,
    terms: str | None,
    include_internal_costs: bool,
) -> IssuedQuote:
    """Émet le devis d'une version gelée, ou refuse en le disant."""
    if version.status != "frozen":
        raise EmissionRefusee(
            "version_not_frozen",
            "Cette version n'est pas gelée. Gelez-la avant d'émettre le devis : "
            "un devis remis doit désigner un calcul qui ne bougera plus.",
        )
    if project.client_id is None:
        raise EmissionRefusee(
            "client_required",
            "Ce chantier n'a pas de fiche client. Choisissez-en une, ou créez-la, "
            "avant d'émettre le devis.",
        )
    client = get_owned(session, Client, context.organization_id, project.client_id, label="Client")
    manquants = client_suffisant(client)
    if manquants:
        raise EmissionRefusee(
            "client_incomplete",
            "La fiche client est trop incomplète pour adresser un devis.",
            missing=manquants,
        )
    deja = session.scalars(
        select(IssuedQuote).where(IssuedQuote.estimate_version_id == version.id)
    ).first()
    if deja is not None:
        raise EmissionRefusee(
            "already_issued",
            f"Cette version porte déjà le devis {deja.number}. Un devis remis ne se "
            "corrige pas : créez une nouvelle version, puis émettez-la.",
            number=deja.number,
        )

    emis_le = utcnow()
    motif = getattr(reglages, "quote_number_pattern", None)
    try:
        numerotation.verifier(motif)
    except numerotation.MotifInvalide as refus:
        # Une configuration historique peut être illisible : elle a pu être
        # enregistrée avant que les réglages ne la contrôlent. On refuse
        # d'émettre plutôt que de servir un numéro de secours que personne
        # n'a demandé — le devis part chez un client, avec ce numéro-là.
        raise EmissionRefusee(
            "quote_number_pattern_invalid",
            f"{refus.message} Corrigez-le dans les réglages de l'entreprise avant d'émettre.",
            **refus.context,
        ) from refus
    numero, annee, rang = numeroter(
        session, organization_id=context.organization_id, motif=motif, quand=emis_le
    )
    echeance = valid_until or (emis_le.date() + timedelta(days=VALIDITE_PAR_DEFAUT))
    if echeance < emis_le.date():
        raise EmissionRefusee(
            "validity_in_the_past",
            "La date de validité précède la date d'émission.",
        )

    organisation_vue = instantane_organisation(organization, reglages)
    client_vu = instantane_client(client)
    projet_vu = instantane_projet(project, estimate, version)
    document_vu = {
        "lines": lignes,
        "totals": totaux,
        "include_internal_costs": include_internal_costs,
    }

    identifiant = new_id()
    pdf = composer_le_devis(
        numero=numero,
        emis_le=emis_le,
        valid_until=echeance,
        organisation=organisation_vue,
        client=client_vu,
        projet=projet_vu,
        document=document_vu,
        terms=terms,
        include_internal_costs=include_internal_costs,
    )
    original = stockage.ecrire_octets(
        organization_id=context.organization_id,
        dossier="devis",
        identifiant=identifiant,
        extension=".pdf",
        contenu=pdf,
    )
    compenser(
        session,
        lambda: stockage.supprimer(original.storage_key),
        f"retirer le PDF du devis {numero} du volume",
    )

    devis = IssuedQuote(
        id=identifiant,
        organization_id=context.organization_id,
        project_id=project.id,
        estimate_id=estimate.id,
        estimate_version_id=version.id,
        client_id=client.id,
        number=numero,
        sequence_year=annee,
        sequence_number=rang,
        issued_at=emis_le,
        valid_until=echeance,
        terms=terms,
        organization_snapshot=organisation_vue,
        client_snapshot=client_vu,
        project_snapshot=projet_vu,
        document_snapshot=document_vu,
        include_internal_costs=include_internal_costs,
        pdf_storage_key=original.storage_key,
        pdf_sha256=original.sha256,
        pdf_byte_size=original.byte_size,
        issued_by=context.user.id,
    )
    session.add(devis)
    session.flush()

    audit.record(
        session,
        organization_id=context.organization_id,
        action="quote.issued",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Devis {numero} émis pour {client.name}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={
            "number": numero,
            "project_id": project.id,
            "estimate_version_id": version.id,
            "client_id": client.id,
            "valid_until": echeance.isoformat(),
            "pdf_sha256": original.sha256,
            "pdf_byte_size": original.byte_size,
            "include_internal_costs": include_internal_costs,
        },
    )
    return devis


def enregistrer_le_telechargement(
    session: Session, *, context: TenantContext, devis: IssuedQuote
) -> None:
    """Qui a repris le document, et lequel. Jamais son contenu."""
    audit.record(
        session,
        organization_id=context.organization_id,
        action="quote.downloaded",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Devis {devis.number} téléchargé",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"number": devis.number, "pdf_sha256": devis.pdf_sha256},
    )


def totaux_du_document(payload: dict[str, Any], devise: str) -> dict[str, Any]:
    """Les totaux tels que le devis les imprime, extraits du calcul."""
    totaux = payload.get("totals") or {}
    taxes = []
    for taxe in totaux.get("taxes", []) or []:
        taxes.append(
            {
                "label": taxe.get("label") or taxe.get("code") or "Taxe",
                "rate": str(taxe.get("rate", "")),
                "amount": str(taxe.get("amount", "")),
            }
        )
    return {
        "currency": devise,
        "total_ht": str(totaux.get("total_selling_price_ht", "") or ""),
        "taxes": taxes,
        "total_ttc": str(totaux.get("total_ttc", "") or ""),
    }


def _decimal(valeur: Any) -> Decimal:
    try:
        return Decimal(str(valeur))
    except Exception:  # une valeur illisible ne doit pas empêcher d'imprimer
        return Decimal("0")


def maintenant() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
