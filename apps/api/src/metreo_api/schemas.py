"""Request and response contracts.

Pydantic models are the API surface: FastAPI validates against them and derives
the OpenAPI document from them. Decimal fields stay :class:`~decimal.Decimal`
and are serialised as JSON strings so no client ever reads a rounded float.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
    field_serializer,
    model_validator,
)

from metreo_domain import bounds
from metreo_domain.money import canonical_text

from .security.roles import Role
from .services.price_contract import MAX_LEAD_TIME_DAYS, sql_length


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DecimalOut(ApiModel):
    """Decimals leave the API as strings, in their canonical spelling.

    A JSON number would be read back as a double by any JavaScript client and
    lose cents. ``canonical_text`` additionally keeps the spelling independent
    of the storage backend: without it a quantity read from PostgreSQL travels
    as ``"120.0000000000"`` and the same quantity read from SQLite as ``"120"``.
    """

    @field_serializer("*", when_used="json")
    def _decimals_as_strings(self, value: Any) -> Any:
        return canonical_text(value) if isinstance(value, Decimal) else value


# -- auth ------------------------------------------------------------------


class DevLoginRequest(BaseModel):
    email: EmailStr
    organization_id: str | None = Field(
        default=None,
        description="Organisation cible. Obligatoire si l'utilisateur en a plusieurs.",
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    organization_id: str
    user_id: str
    role: str


class OidcStartOut(BaseModel):
    """Où envoyer le navigateur pour commencer la connexion."""

    authorization_url: str


class OidcExchangeRequest(BaseModel):
    """Le code opaque rendu par le retour, contre une session.

    `organization_id` n'est requis que si l'utilisateur appartient à plusieurs
    organisations : le choix doit rester explicite plutôt que subi.
    """

    login_code: str = Field(min_length=16, max_length=64)
    organization_id: str | None = None


class MembershipOut(ApiModel):
    organization_id: str
    organization_name: str
    role: str
    role_label: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    locale: str
    organization_id: str
    organization_name: str
    role: str
    role_label: str
    permissions: list[str]
    memberships: list[MembershipOut]


# -- organisation ----------------------------------------------------------


class LogoOut(ApiModel):
    """Ce que l'API dit d'un logo — jamais où il est rangé.

    `logo_storage_key` reste dans la base et n'apparaît dans aucune réponse :
    c'est un chemin interne, et le rendre inviterait à le demander. Le
    navigateur reçoit une URL de route, l'empreinte pour éviter de recharger,
    et les dimensions pour réserver la place sans attendre les octets.
    """

    sha256: str
    byte_size: int
    media_type: str
    width: int
    height: int
    updated_at: datetime | None


class OrganizationOut(ApiModel):
    id: str
    name: str
    legal_name: str | None
    company_number: str | None
    country_code: str
    region_code: str
    locale: str
    currency: str
    timezone: str
    address: str | None = None
    address_complement: str | None = None
    postal_code: str | None = None
    city: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    logo: LogoOut | None = None
    #: Les champs qui manquent pour émettre un NOUVEAU devis. Vide = on peut.
    #: L'écran s'en sert pour surligner, et pour prévenir avant l'émission
    #: plutôt qu'au moment où l'on croit avoir fini.
    missing_for_issue: list[str] = Field(default_factory=list)


class OrganizationProfileUpdate(ApiModel):
    """Le profil, modifiable champ par champ.

    Tous facultatifs : l'écran envoie ce qui a changé. Une chaîne vide vaut
    « effacer », et c'est voulu — retirer un site web qu'on n'a plus doit être
    possible sans passer par la base. Le nom fait exception : il ne peut pas
    devenir vide, une organisation sans nom ne s'imprime nulle part.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    company_number: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=255)
    address_complement: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=120)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=255)


def _bounded(bound: bounds.Bound) -> Any:
    """Champ Pydantic dérivé d'une borne métier du domaine.

    La valeur des bornes vit dans ``metreo_domain.bounds`` et nulle part
    ailleurs : redéclarer ici un maximum en dur produirait deux vérités qui
    divergeraient à la première correction.
    """
    if bound.minimum_inclusive:
        return Field(ge=bound.minimum, le=bound.maximum)
    return Field(gt=bound.minimum, le=bound.maximum)


def _bounded_opt(bound: bounds.Bound) -> Any:
    if bound.minimum_inclusive:
        return Field(default=None, ge=bound.minimum, le=bound.maximum)
    return Field(default=None, gt=bound.minimum, le=bound.maximum)


class OrganizationSettingsOut(DecimalOut):
    """Company rules.

    The commercial coefficients are ``None`` — not zero — for a caller without
    ``margin:read``. A masked value must never be mistaken for a real one, so
    ``commercial_rates_visible`` says which case the client is looking at.
    """

    rounding_scale: int
    rounding_mode: str
    unit_price_scale: int
    commercial_rates_visible: bool = True
    site_overheads_rate: Decimal | None = None
    site_overheads_base: str | None = None
    general_overheads_rate: Decimal | None = None
    general_overheads_base: str | None = None
    contingency_rate: Decimal | None = None
    contingency_base: str | None = None
    margin_rate: Decimal | None = None
    margin_method: str | None = None
    missing_price_policy: str
    quote_number_pattern: str
    #: Le numéro que ce motif produirait, rendu par le serveur. Une phrase
    #: de refus quand le motif enregistré est devenu illisible.
    quote_number_preview: str = ""
    show_internal_costs_in_client_pdf: bool
    ai_enabled: bool


class QuoteNumberPreviewOut(BaseModel):
    """Le verdict du serveur sur un motif, avant tout enregistrement."""

    valid: bool
    #: Le numéro rendu, quand le motif est utilisable.
    preview: str | None
    #: Pourquoi il est refusé, quand il l'est. Le même texte que le 422.
    message: str | None


class OrganizationSettingsUpdate(BaseModel):
    """Réglages modifiables d'une organisation.

    Les quatre taux dérivent de `bounds.RATE` par `_bounded_opt`, et ne
    réécrivent plus leurs limites à la main. Ils l'avaient fait, et les deux
    vérités avaient divergé : `margin_rate` portait un `lt=10` là où les trois
    autres portaient `le=10` et où le moteur accepte `10` pour les quatre. Un
    instantané gelé à `margin_rate = 10` se recalculait donc sans broncher,
    alors qu'il n'aurait jamais pu être saisi par l'API.

    `bounds.RATE` fait foi : maximum `10`, inclusif.
    """

    rounding_scale: int | None = Field(default=None, ge=0, le=6)
    rounding_mode: Literal["half_up", "half_even"] | None = None
    unit_price_scale: int | None = Field(default=None, ge=0, le=6)
    site_overheads_rate: Decimal | None = _bounded_opt(bounds.RATE)
    site_overheads_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = None
    general_overheads_rate: Decimal | None = _bounded_opt(bounds.RATE)
    general_overheads_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = (
        None
    )
    contingency_rate: Decimal | None = _bounded_opt(bounds.RATE)
    contingency_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = None
    margin_rate: Decimal | None = _bounded_opt(bounds.RATE)
    margin_method: Literal["on_cost", "on_price"] | None = None
    missing_price_policy: Literal["block", "warn"] | None = None
    quote_number_pattern: str | None = Field(default=None, max_length=60)
    show_internal_costs_in_client_pdf: bool | None = None


class MemberOut(ApiModel):
    """Un collaborateur de l'organisation, tel que les réglages le montrent."""

    id: str
    user_id: str
    email: str
    full_name: str
    role: str
    role_label: str
    is_active: bool


class MemberInvite(BaseModel):
    """L'ajout d'un collaborateur — sans mot de passe, comme le bootstrap.

    Rien n'est envoyé à cette adresse et aucun secret n'est créé : ce que l'on
    inscrit ici, c'est le DROIT d'entrer. La personne se connectera par le
    fournisseur d'identité, et la liaison se fera à sa première connexion sur
    son adresse vérifiée.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: NonBlank = Field(max_length=200)
    role: Role


class MemberUpdate(BaseModel):
    """Changer le rôle d'un collaborateur, ou lui retirer l'accès.

    L'accès se retire (``is_active``) et ne se supprime pas : les événements
    d'audit désignent l'utilisateur qui les a produits, et effacer la personne
    rendrait illisible l'historique qu'elle a écrit.
    """

    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None


class TaxRateCreate(BaseModel):
    """Saisie d'un taux de taxe par l'administrateur.

    Metreo n'installe AUCUN taux au départ, et n'en devine aucun. Le taux
    applicable, sa date d'effet et sa base légale sont une décision de
    l'entreprise : les inscrire à sa place reviendrait à lui faire porter une
    affirmation fiscale qu'elle n'a pas prise.

    `rate` est une PROPORTION, pas un pourcentage : 21 % s'écrit `0.21`.
    """

    model_config = ConfigDict(extra="forbid")

    code: NonBlank = Field(max_length=30, description="Identifiant court, ex. « TVA-21 »")
    label: NonBlank = Field(max_length=120, description="Libellé imprimé sur le devis")
    rate: Decimal = _bounded(bounds.RATE)
    applies_from: date | None = Field(
        default=None, description="Premier jour d'application. Vide = depuis toujours."
    )
    applies_to: date | None = Field(
        default=None, description="Dernier jour d'application. Vide = sans fin."
    )
    is_default: bool = Field(
        default=True,
        description="Appliqué aux nouvelles estimations tant qu'il est en vigueur.",
    )
    source: str | None = Field(
        default=None,
        max_length=255,
        description="D'où vient ce taux. Metreo ne le valide pas juridiquement.",
    )


class TaxRateUpdate(BaseModel):
    """Modification d'un taux. Le `code` n'est pas modifiable.

    Un instantané de devis gelé conserve le code du taux appliqué : le changer
    ferait mentir l'historique sans qu'aucune trace ne le dise.
    """

    model_config = ConfigDict(extra="forbid")

    label: NonBlank | None = Field(default=None, max_length=120)
    rate: Decimal | None = _bounded_opt(bounds.RATE)
    applies_from: date | None = None
    applies_to: date | None = None
    is_default: bool | None = None
    source: str | None = Field(default=None, max_length=255)


class TaxRateOut(DecimalOut):
    id: str
    code: str
    label: str
    rate: Decimal
    applies_from: date | None
    applies_to: date | None
    is_default: bool
    source: str | None


# -- region profiles -------------------------------------------------------


class RegionProfileOut(ApiModel):
    id: str
    country_code: str
    code: str
    name: str
    version: str
    default_locale: str
    locales: list[str]
    default_currency: str
    terminology: dict[str, Any]
    rules: dict[str, Any]
    sources: list[Any]
    status: str
    disclaimer: str | None


# -- projects --------------------------------------------------------------


def project_length(column: str) -> int:
    """Longueur d'une colonne de `Project`, lue sur le modèle."""
    from .models import Project

    length = getattr(Project.__table__.columns[column].type, "length", None)
    if length is None:  # pragma: no cover - colonne Text
        raise KeyError(f"La colonne {column} ne porte pas de longueur.")
    return int(length)


#: Chaîne obligatoire qui refuse aussi les espaces seuls. `min_length=1`
#: laissait passer « \u00a0 » ou « " " », qui produit un nom de projet vide à
#: l'écran.
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProjectCreate(BaseModel):
    """Création d'un projet.

    Les longueurs sont lues sur les colonnes de `Project`, comme celles des
    prix : les écrire à la main ici et là produit tôt ou tard deux vérités.
    """

    model_config = ConfigDict(extra="forbid")

    reference: NonBlank = Field(max_length=project_length("reference"))
    name: NonBlank = Field(max_length=project_length("name"))
    #: La fiche du répertoire. Facultative : on crée souvent le chantier avant
    #: d'avoir choisi à qui le devis sera adressé.
    client_id: str | None = None
    client_reference: str | None = Field(
        default=None, max_length=project_length("client_reference")
    )
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=project_length("client_name"))
    address: str | None = Field(default=None, max_length=project_length("address"))
    postal_code: str | None = Field(default=None, max_length=project_length("postal_code"))
    city: str | None = Field(default=None, max_length=project_length("city"))
    country_code: str = Field(default="BE", min_length=2, max_length=2)
    region_code: str = Field(default="BE-WAL", max_length=project_length("region_code"))
    market_type: str | None = Field(default=None, max_length=project_length("market_type"))
    work_categories: list[str] = Field(default_factory=list)
    submission_deadline: datetime | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    locale: str = Field(default="fr-BE", max_length=10)


class ProjectUpdate(BaseModel):
    """Mise à jour d'un projet — mêmes limites que la création.

    Elles n'y étaient pas : `PATCH` acceptait une adresse de dix mille
    caractères là où `POST` la refusait à 255. La même donnée était donc valide
    ou non selon le verbe employé.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonBlank | None = Field(default=None, max_length=project_length("name"))
    client_id: str | None = None
    client_reference: str | None = Field(
        default=None, max_length=project_length("client_reference")
    )
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=project_length("client_name"))
    address: str | None = Field(default=None, max_length=project_length("address"))
    postal_code: str | None = Field(default=None, max_length=project_length("postal_code"))
    city: str | None = Field(default=None, max_length=project_length("city"))
    region_code: str | None = Field(default=None, max_length=project_length("region_code"))
    market_type: str | None = Field(default=None, max_length=project_length("market_type"))
    work_categories: list[str] | None = None
    submission_deadline: datetime | None = None
    status: Literal["draft", "studying", "submitted", "won", "lost", "archived"] | None = None


def client_length(column: str) -> int:
    """Longueur d'une colonne de `Client`, lue sur le modèle."""
    from .models import Client

    length = getattr(Client.__table__.columns[column].type, "length", None)
    if length is None:  # pragma: no cover - colonne Text
        raise KeyError(f"La colonne {column} ne porte pas de longueur.")
    return int(length)


class ClientCreate(BaseModel):
    """Une fiche client. Seul le nom est exigé à la création.

    Adresse, contact et numéro d'entreprise peuvent venir plus tard : on saisit
    souvent un client au téléphone avant d'avoir sa lettre. Ce qui est exigé
    plus tard, et là seulement, c'est ce qu'il faut pour ADRESSER un devis —
    voir `issuance.client_suffisant`.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonBlank = Field(max_length=client_length("name"))
    company_number: str | None = Field(default=None, max_length=client_length("company_number"))
    billing_address: str | None = Field(default=None, max_length=client_length("billing_address"))
    postal_code: str | None = Field(default=None, max_length=client_length("postal_code"))
    city: str | None = Field(default=None, max_length=client_length("city"))
    country_code: str = Field(default="BE", min_length=2, max_length=2)
    contact_name: str | None = Field(default=None, max_length=client_length("contact_name"))
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=client_length("phone"))
    notes: str | None = None


class ClientUpdate(BaseModel):
    """Mise à jour d'une fiche — mêmes limites que la création.

    `status` est ici : archiver une fiche est une modification comme une autre,
    et le passage `active`/`archived` n'a pas besoin d'une route à lui.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonBlank | None = Field(default=None, max_length=client_length("name"))
    company_number: str | None = Field(default=None, max_length=client_length("company_number"))
    billing_address: str | None = Field(default=None, max_length=client_length("billing_address"))
    postal_code: str | None = Field(default=None, max_length=client_length("postal_code"))
    city: str | None = Field(default=None, max_length=client_length("city"))
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    contact_name: str | None = Field(default=None, max_length=client_length("contact_name"))
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=client_length("phone"))
    notes: str | None = None
    status: Literal["active", "archived"] | None = None


class ClientOut(ApiModel):
    id: str
    name: str
    company_number: str | None
    billing_address: str | None
    postal_code: str | None
    city: str | None
    country_code: str
    contact_name: str | None
    email: str | None
    phone: str | None
    notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class QuoteIssueRequest(BaseModel):
    """Ce que l'émetteur décide, et rien de plus.

    Le reste — numéro, date, instantanés — est établi par le serveur : ce sont
    précisément les valeurs qu'un client ne doit pas pouvoir choisir.
    """

    model_config = ConfigDict(extra="forbid")

    valid_until: date | None = None
    terms: str | None = Field(default=None, max_length=4000)
    #: Décision explicite, prise au moment d'émettre et figée dans l'instantané.
    #: Le réglage de l'organisation en donne le défaut, jamais le dernier mot.
    include_internal_costs: bool | None = None


class QuoteEventOut(ApiModel):
    """Une ligne de la chronologie, telle que l'entreprise la lit."""

    id: str
    kind: str
    kind_label: str
    channel: str | None
    actor_email: str | None
    respondent_name: str | None
    respondent_email: str | None
    comment: str | None
    effective_at: datetime
    recorded_at: datetime
    #: Barré par une correction ultérieure. L'événement reste affiché : c'est
    #: la différence entre corriger et effacer.
    corrected: bool = False
    correction_reason: str | None = None
    corrects_event_id: str | None = None


class QuoteStateOut(ApiModel):
    """L'état commercial, déduit du journal — jamais lu dans une colonne."""

    code: str
    label: str
    decision: str | None
    transmitted_at: datetime | None
    viewed_at: datetime | None
    decided_at: datetime | None
    last_activity_at: datetime | None
    expired: bool


class ShareLinkOut(ApiModel):
    """Un lien de consultation. Jamais son secret."""

    id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    active: bool


class ShareLinkCreated(ApiModel):
    """La création d'un lien — la seule réponse qui porte le secret.

    Il n'est rendu qu'ici, et une seule fois : la base n'en garde que
    l'empreinte, et aucune relecture ne pourra le redonner.
    """

    link: ShareLinkOut
    #: L'adresse complète à copier, secret compris, dans le FRAGMENT.
    url: str


class ShareLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Durée demandée. Bornée par la validité du devis, jamais au-delà.
    days: int | None = Field(default=None, ge=1, le=365)


class QuoteEventCreate(BaseModel):
    """Ce que l'entreprise enregistre elle-même, sans passer par le lien."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["transmitted", "accepted", "declined"]
    channel: Literal["public_link", "email", "phone", "meeting", "other"]
    #: Le moment où la chose a eu lieu, s'il diffère de celui où on l'écrit.
    effective_at: datetime | None = None
    respondent_name: str | None = Field(default=None, max_length=200)
    respondent_email: EmailStr | None = None
    comment: str | None = Field(default=None, max_length=2000)


class QuoteCorrectionCreate(BaseModel):
    """Corriger une saisie interne : en le disant, jamais en effaçant."""

    model_config = ConfigDict(extra="forbid")

    reason: NonBlank = Field(max_length=500)
    comment: str | None = Field(default=None, max_length=2000)


class QuoteBoardRow(ApiModel):
    """Une ligne du tableau de suivi, tous chantiers confondus."""

    id: str
    number: str
    client_name: str
    project_id: str
    project_reference: str
    project_name: str
    total_ttc: str
    currency: str
    issued_at: datetime
    valid_until: date
    state: QuoteStateOut
    has_active_link: bool


class QuoteBoardPage(ApiModel):
    items: list[QuoteBoardRow]
    page: Page


class IssuedQuoteDetail(ApiModel):
    """La fiche d'un devis remis : le document, son état, son histoire."""

    quote: IssuedQuoteOut
    state: QuoteStateOut
    events: list[QuoteEventOut]
    links: list[ShareLinkOut]
    project_reference: str
    project_name: str
    client_snapshot: dict
    total_ttc: str
    currency: str


# ---------------------------------------------------------------------------
# Le côté public : ce qu'un destinataire sans compte voit et envoie
# ---------------------------------------------------------------------------


class PublicSessionRequest(BaseModel):
    """L'échange du secret contre une session. Le secret est dans le CORPS.

    Ni dans le chemin, ni dans la chaîne de requête : les deux finissent dans
    les journaux d'accès, les référents et l'historique du navigateur.
    """

    model_config = ConfigDict(extra="forbid")

    secret: NonBlank = Field(max_length=200)


class PublicQuoteLine(ApiModel):
    position: str
    designation: str
    unit: str
    quantity: str
    unit_price_ht: str
    total_ht: str


class PublicQuoteView(ApiModel):
    """Tout ce que le destinataire voit, et rien de plus.

    Aucun coût interne n'y figure — ni déboursé, ni revient, ni marge — et le
    partage d'un devis qui en porterait est refusé en amont.
    """

    number: str
    issued_at: datetime
    valid_until: date
    organization_name: str
    organization_legal_name: str | None
    organization_company_number: str | None
    #: L'adresse et les coordonnées de l'ÉMETTEUR, telles qu'elles étaient à
    #: l'émission. Le client doit pouvoir répondre à l'entreprise sans ouvrir
    #: le PDF, et ce qu'il lit ici est ce que le PDF imprime.
    organization_address_lines: list[str] = Field(default_factory=list)
    organization_email: str | None = None
    organization_phone: str | None = None
    organization_website: str | None = None
    #: Vrai quand ce devis a figé un logo. La page le demande alors à la route
    #: publique dédiée ; elle ne reçoit jamais de chemin de stockage.
    has_logo: bool = False
    client_name: str
    client_address_lines: list[str]
    project_reference: str
    project_name: str
    lines: list[PublicQuoteLine]
    total_ht: str
    taxes: list[dict]
    total_ttc: str
    currency: str
    terms: str | None
    pdf_sha256: str
    pdf_byte_size: int
    state: QuoteStateOut
    #: Une réponse est-elle encore possible, et sinon pourquoi.
    can_respond: bool
    cannot_respond_reason: str | None


class PublicResponseRequest(BaseModel):
    """La réponse du client. L'identité est DÉCLARATIVE, et l'écran le dit."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "declined"]
    #: Exigé pour accepter, facultatif pour refuser — un client qui décline
    #: n'a pas à se nommer.
    respondent_name: str | None = Field(default=None, max_length=200)
    respondent_email: EmailStr | None = None
    comment: str | None = Field(default=None, max_length=2000)
    #: La confirmation explicite affichée à l'écran, renvoyée telle quelle.
    confirmed: bool = False


class PublicReceipt(ApiModel):
    """Le reçu remis après une réponse. Rejouer la même réponse le redonne."""

    number: str
    decision: str
    decision_label: str
    decided_at: datetime
    respondent_name: str | None
    pdf_sha256: str
    #: Faux quand la réponse était déjà enregistrée : la requête n'a rien
    #: écrit de nouveau, et le reçu est celui d'origine.
    created: bool


class IssuedQuoteOut(ApiModel):
    id: str
    number: str
    project_id: str
    estimate_id: str
    estimate_version_id: str
    client_id: str
    client_name: str
    issued_at: datetime
    valid_until: date
    terms: str | None
    include_internal_costs: bool
    pdf_sha256: str
    pdf_byte_size: int
    version_number: int
    issued_by_email: str | None = None


class ProjectOut(ApiModel):
    id: str
    reference: str
    client_id: str | None
    client_reference: str | None
    name: str
    description: str | None
    client_name: str | None
    address: str | None
    postal_code: str | None
    city: str | None
    country_code: str
    region_code: str
    market_type: str | None
    work_categories: list[str]
    submission_deadline: datetime | None
    currency: str
    locale: str
    status: str
    created_at: datetime
    updated_at: datetime


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    page: Page


# -- documents -------------------------------------------------------------


def document_length(column: str) -> int:
    """Longueur d'une colonne documentaire, lue sur le modèle."""
    from .models import Document

    length = getattr(Document.__table__.columns[column].type, "length", None)
    if length is None:
        raise KeyError(f"La colonne {column} ne porte pas de longueur.")
    return int(length)


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonBlank = Field(max_length=document_length("title"))


class DocumentOut(ApiModel):
    id: str
    project_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentStatusUpdate(BaseModel):
    """Archiver ou réactiver. Aucune suppression n'est exposée."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "archived"]


class DocumentRevisionOut(ApiModel):
    """Ce qu'une révision montre — et ce qu'elle ne montre pas.

    `storage_key` et `organization_id` restent hors de la réponse : le premier
    est un chemin interne que rien au dehors n'a à connaître, le second un
    identifiant de tenant que le porteur du jeton n'a pas à lire.

    `original_filename` et l'auteur, en revanche, sont rendus. Ils étaient
    masqués tant qu'aucun écran ne les demandait ; une liste de documents sans
    nom de fichier ni auteur n'est pas consultable, et ces deux faits
    appartiennent à l'organisation qui a déposé le fichier. Le nom est
    neutralisé au dépôt — réduit à son dernier segment, sans séparateur ni
    caractère de contrôle — il ne peut donc pas rapporter de chemin.
    """

    id: str
    document_id: str
    revision_number: int
    sha256: str
    byte_size: int
    media_type: str
    original_filename: str
    #: L'adresse de qui a déposé, résolue par le service. `None` si le compte a
    #: été retiré depuis : la révision, elle, ne disparaît pas avec lui.
    author_email: str | None = None
    status: str
    published_at: datetime | None
    created_at: datetime


# -- lecture d'un plan -----------------------------------------------------


class AnomalieDePlan(ApiModel):
    """Un fait qui empêche ou fragilise une reprise, déjà rédigé en français.

    Le message vient du serveur et part tel quel à l'écran : une interface qui
    le recomposerait depuis le code finirait par dire autre chose que ce que
    le lecteur a constaté.
    """

    code: str
    message: str


class CadreDePlan(ApiModel):
    """Où se trouve un objet dans l'image, en [0,1], origine en haut à gauche.

    Les quatre bornes sont des CHAÎNES décimales, comme toutes les valeurs
    décimales de cette API : un flottant perdrait des chiffres au transport,
    et une position sert à poser un surlignage au pixel près.
    """

    x0: str
    y0: str
    x1: str
    y1: str


class MesureDePlan(ApiModel):
    """Une mesure proposée, sa provenance, sa réserve et la décision humaine.

    `valeur_document` est la mesure DANS L'UNITÉ DU DOCUMENT, sans aucune
    conversion. `valeur_corrigee` est ce qu'un humain a retenu, s'il a
    corrigé : les deux sont rendues, parce que la proposition machine n'est
    jamais réécrite et que l'écran doit pouvoir montrer l'écart.
    """

    proposal_id: str
    citation_id: str
    valeur_document: str
    unite_document: str | None
    famille: str
    fiabilite: str
    origine_de_la_mesure: str
    texte_impose: str | None
    reserves: list[AnomalieDePlan]
    confiance: str
    calque: str | None
    feuille: str | None
    object_ref: str | None
    cadre: CadreDePlan | None
    decision: str | None
    valeur_corrigee: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valeur_lisible(self) -> str:
        """La cotation abrégée, à la belge — un angle en degrés.

        Rendue par le serveur : l'écran l'abrégeait avec un flottant et un
        point décimal, et lisait un angle en radians comme des centimètres.
        """
        from .services import lisible

        return lisible.cote_lisible(self.valeur_document, self.unite_document)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valeur_exacte_lisible(self) -> str:
        """La cotation entière, transcrite sans arrondi : celle du fichier."""
        from .services import lisible

        return lisible.cote_exacte(self.valeur_document, self.unite_document)


class PlanLu(ApiModel):
    """Le constat d'un plan, et les mesures qu'on en a tirées.

    `mesurable` faux n'est pas une erreur : un plan sans unité déclarée reste
    consultable et archivable — ni la visualisation ni l'archivage ne
    demandent d'unité. Il n'est simplement pas mesurable, et l'écran doit le
    dire au lieu de laisser une liste vide se faire interpréter.
    """

    revision_id: str
    #: `"dxf"` ou `"pdf"`. L'écran en a besoin AVANT de lire le reste : les
    #: champs d'un format absent valent `None` ou une liste vide, et un écran
    #: qui ne saurait pas lequel il lit afficherait « sans unité » pour un PDF
    #: — ce qui est vrai, mais trompeur : un PDF n'en a jamais.
    format: Literal["dxf", "pdf"] = "dxf"
    mesurable: bool
    unite_source: str | None
    insunits: int | None
    version_dxf: str | None
    feuilles: list[str]
    calques: dict[str, int]
    entites: dict[str, int]
    refuse: bool
    motif_du_refus: AnomalieDePlan | None
    anomalies: list[AnomalieDePlan]
    image_disponible: bool
    mesures: list[MesureDePlan]

    # -- ce qui n'existe que pour un PDF ------------------------------------
    #: Zéro pour un DXF : la notion n'a pas de sens pour un espace modèle.
    pages: int = 0
    #: Largeur et hauteur de chaque page, en **points PostScript** (1/72 de
    #: pouce). Ce sont des nombres, pas des chaînes décimales, et la raison est
    #: qu'ils ne servent à AUCUN calcul de métré : ils donnent le rapport de
    #: forme d'un aperçu. Une valeur qui entre dans un prix est une chaîne ;
    #: celle-ci n'y entre jamais.
    dimensions_des_pages: list[list[float]] = Field(default_factory=list)
    #: Faux pour un document scanné : l'aperçu reste utile, l'extraction non.
    porte_du_texte: bool = False
    #: Les FAITS de l'extraction, à afficher tels quels.
    #:
    #: L'écran annonçait « aucun — plan probablement scanné » dès que
    #: l'extraction rendait moins de cinquante caractères, pour un plan
    #: vectoriel qui en portait vingt-sept. Ce qui distingue un scan n'est pas
    #: la quantité de texte : c'est son absence totale sur une page qui ne
    #: porte qu'une image.
    caracteres_extraits: int = 0
    traces_vectoriels: int = 0
    images_incluses: int = 0
    probablement_scanne: bool = False
    #: Combien de fragments de texte ont été récoltés. Un COMPTE, pas le texte :
    #: les fragments se demandent à la route `…/plan/textes`, parce qu'un plan
    #: réel en porte quelques milliers.
    fragments_lus: int = 0
    #: Les pages qui ont un aperçu, dans l'ordre. Une page absente de cette
    #: liste n'est pas affichable — et `anomalies` dit alors pourquoi.
    apercus: list[int] = Field(default_factory=list)


class FragmentDeTexte(ApiModel):
    """Un morceau de texte d'un PDF, et où il se trouve.

    `texte` est rendu **tel que le document le porte** : espaces, virgules
    décimales et unités comprises, sans normalisation. Le découpage en
    fragments est celui du fichier, pas le nôtre — sur un plan réel, une cote
    peut arriver entière (« 5000 ») ou éclatée caractère par caractère.
    Regrouper relève d'une interprétation, et cette interprétation n'a pas
    lieu côté serveur.

    **Un fragment n'est pas une mesure.** C'est un texte situé. Il devient une
    mesure quand un humain a confirmé une échelle, et pas avant.
    """

    texte: str
    #: 1-indexée, comme une citation documentaire.
    page: int
    #: Dans le repère de l'aperçu PNG : [0,1], origine en haut à gauche. Se
    #: pose donc directement sur l'image, sans conversion.
    #:
    #: `null` quand la position est inconnue. Ce n'est pas un oubli : écraser
    #: une boîte hors page sur un bord inventerait un emplacement, et le
    #: propriétaire chercherait la cote là.
    cadre: CadreDePlan | None
    #: `"exacte"`, `"recadree"` ou `"inconnue"`. Un écran qui n'afficherait que
    #: le cadre présenterait un surlignage partiel comme s'il était complet.
    position: str = "exacte"


class TextesDePlan(ApiModel):
    """Les textes d'un PDF, par tranches : un plan réel en porte des milliers."""

    revision_id: str
    #: Le nombre total de fragments du document, toutes pages confondues —
    #: pas celui de la tranche rendue. C'est lui qui permet à l'écran de dire
    #: « 120 sur 4 351 » au lieu de laisser croire qu'il a tout.
    total: int
    #: Quelle page a été demandée, ou `None` pour toutes.
    page: int | None
    fragments: list[FragmentDeTexte]
    #: L'extracteur et sa version, tels qu'ils figureront dans une citation.
    extracteur: str


class PointDEcran(BaseModel):
    """Un point désigné sur l'aperçu : [0,1], origine en haut à gauche.

    Le MÊME repère que celui des cadres rendus par l'API. Demander au client de
    convertir vers les points PostScript de la page lui ferait refaire une
    transformation qui dépend de la rotation d'affichage — et la referait
    forcément autrement.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class CalibrationCreate(BaseModel):
    """Déclarer l'échelle d'une page : deux points, et ce qui les sépare."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    premier: PointDEcran
    second: PointDEcran
    #: Ce que la personne SAIT de cette distance. En chaîne décimale : un
    #: flottant perdrait des chiffres, et cette valeur multiplie tout le reste.
    distance_reelle: Decimal = Field(gt=0)
    #: Un code d'unité de LONGUEUR. Refusé s'il n'en est pas un.
    unite: str = Field(min_length=1, max_length=16)
    #: Points PostScript par pixel d'écran au moment du pointage.
    #:
    #: **C'est ce champ qui décide de la confiance accordée aux mesures.**
    #: Mesuré : sur l'aperçu pleine page d'un A0, un pixel vaut 12 à 42 mm
    #: d'ouvrage ; sur une tuile agrandie, 0,6 à 6 mm. Un client qui ne le
    #: déclare pas obtient le doute, pas le crédit.
    resolution_du_pointage: Decimal = Field(gt=0)
    #: Sur quoi la personne dit avoir calibré. Obligatoire : une calibration
    #: sans justification ne se vérifie pas, et c'est elle qu'on relira.
    motif: NonBlank = Field(max_length=500)
    #: La zone où cette échelle s'applique. Absente = toute la page.
    zone: list[float] | None = Field(default=None, min_length=4, max_length=4)


class CalibrationOut(ApiModel):
    """Une échelle déclarée, telle que l'écran la relit."""

    id: str
    page: int
    distance_reelle: str
    unite: str
    #: « 50 mm par point ». À LIRE, pas à recalculer : un écran qui en referait
    #: l'arithmétique obtiendrait un second facteur, et les deux divergeraient.
    facteur_lisible: str
    resolution_du_pointage: str
    motif: str
    zone: list[str] | None
    created_at: datetime


class MesureCreate(BaseModel):
    """Prendre une mesure : un type, des points, un libellé."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    type: Literal["segment", "surface"]
    points: list[PointDEcran] = Field(min_length=2, max_length=200)
    #: Ce que la personne mesure — « mur nord », « dalle du séjour ». Une liste
    #: de mesures sans libellé est une liste de nombres que personne ne relit.
    libelle: NonBlank = Field(max_length=200)
    #: La résolution à laquelle CES points ont été posés, en points PostScript
    #: par pixel affiché.
    #:
    #: **Facultative, et elle ne devrait pas l'être longtemps.** Le modèle
    #: d'incertitude supposait jusqu'ici que la mesure était pointée au même
    #: zoom que la calibration — vrai dans l'écran livré, faux pour tout autre
    #: client, et faux dès qu'on calibre à la loupe puis qu'on mesure sur une
    #: autre. Absente, l'ancienne hypothèse s'applique : c'est la résolution de
    #: la calibration qui sert, et le comportement ne change pas.
    resolution_du_pointage: Decimal | None = Field(default=None, gt=0)


class MesureDePdf(ApiModel):
    """Une mesure prise sur un PDF, avec de quoi la juger et la retrouver."""

    proposal_id: str
    citation_id: str
    page: int
    type: str
    libelle: str
    valeur: str
    unite: str
    #: Dans la MÊME unité que la valeur. Une incertitude en pourcentage
    #: obligerait le lecteur à faire une multiplication, et il ne la fera pas.
    incertitude: str
    incertitude_relative: str
    fiabilite: str
    reserves: list[str]
    #: Les points désignés, pour redessiner la mesure sur l'aperçu. Sans eux,
    #: « retrouver la mesure sur le plan » redevient impossible.
    points: list[PointDEcran]
    cadre: CadreDePlan | None
    #: D'où vient l'échelle qui a produit ce nombre.
    calibration: dict[str, Any]
    decision: str | None
    #: **Pourquoi** la personne a tranché ainsi — affiché à côté de la décision.
    #: Une décision sans sa raison n'est pas auditable.
    motif_de_la_decision: str | None
    valeur_corrigee: str | None
    #: Les mêmes nombres, écrits pour être LUS — « 4 181 mm », « ± 26 mm ».
    #:
    #: Rendus par le serveur et non recalculés par l'écran, comme
    #: `facteur_lisible` : deux arrondis finiraient par diverger d'un chiffre,
    #: et c'est l'écart qu'on ne voit jamais venir. La valeur exacte reste
    #: au-dessus, et c'est elle qu'on reprend pour calculer.
    valeur_lisible: str
    incertitude_lisible: str
    valeur_retenue_lisible: str | None
    unite_retenue: str | None
    #: **Ce qui compte une fois la décision prise** : la valeur retenue si la
    #: personne a corrigé, la mesure si elle a confirmé, `null` si elle a
    #: rejeté ou n'a pas encore tranché.
    valeur_retenue: str | None
    #: Vrai quand cette mesure peut alimenter un bordereau. Faux pour une
    #: mesure rejetée, et faux tant que personne n'a tranché.
    reprenable: bool


class MesuresDePdf(ApiModel):
    revision_id: str
    calibrations: list[CalibrationOut]
    mesures: list[MesureDePdf]


class ValidationDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "corrected", "rejected"]
    reason: NonBlank = Field(max_length=2000)
    before_value: dict[str, Any] | None = None
    after_value: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _correction_payload_matches_decision(self) -> ValidationDecisionCreate:
        has_before = self.before_value is not None
        has_after = self.after_value is not None
        if self.decision == "corrected":
            if not has_before or not has_after:
                raise ValueError("Une correction doit conserver les valeurs avant et après.")
        elif has_before or has_after:
            raise ValueError("Les valeurs avant/après sont réservées à une correction.")
        return self


class ValidationDecisionOut(ApiModel):
    """Accusé de décision sans répéter les valeurs documentaires sensibles."""

    id: str
    proposal_id: str
    actor_user_id: str
    decision: str
    created_at: datetime


# -- price library ---------------------------------------------------------


class PriceBookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    is_default: bool = False


class PriceBookOut(ApiModel):
    id: str
    name: str
    description: str | None
    currency: str
    is_default: bool
    created_at: datetime


class PriceBookVersionOut(ApiModel):
    id: str
    price_book_id: str
    version_number: int
    label: str | None
    status: str
    published_at: datetime | None
    created_at: datetime


class PriceItemOut(DecimalOut):
    id: str
    code: str
    label: str
    family: str | None
    resource_kind: str
    unit_code: str
    unit_price: Decimal
    currency: str
    supplier_name: str | None
    region_code: str | None
    valid_from: date | None
    valid_to: date | None
    min_quantity: Decimal | None
    lead_time_days: int | None
    source: str | None
    status: str
    confidence: str
    is_demo_data: bool
    notes: str | None


class PriceItemPage(BaseModel):
    items: list[PriceItemOut]
    page: Page


class PriceItemCreate(BaseModel):
    """Saisie manuelle d'un prix.

    Les longueurs viennent des colonnes via `sql_length()` : les écrire à la
    main ici produisait déjà une divergence — `family` acceptait 120 caractères
    pour une colonne de 60. Le contrôle métier complet (unité connue, devise,
    énumérations, plage de dates) est appliqué par `validate_price_row`, le
    même contrat que l'import, dans le handler.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=sql_length("code"))
    label: str = Field(min_length=1, max_length=sql_length("label"))
    unit_code: str = Field(min_length=1, max_length=12)
    unit_price: Decimal = _bounded(bounds.UNIT_PRICE)
    family: str | None = Field(default=None, max_length=sql_length("family"))
    resource_kind: Literal[
        "material", "labor", "equipment", "transport", "disposal", "subcontract", "other"
    ] = "material"
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    supplier_name: str | None = Field(default=None, max_length=sql_length("supplier_name"))
    region_code: str | None = Field(default=None, max_length=sql_length("region_code"))
    valid_from: date | None = None
    valid_to: date | None = None
    source: str | None = Field(default=None, max_length=sql_length("source"))
    indexation: str | None = Field(default=None, max_length=sql_length("indexation"))
    min_quantity: Decimal | None = _bounded_opt(bounds.QUANTITY)
    lead_time_days: int | None = Field(default=None, ge=0, le=MAX_LEAD_TIME_DAYS)
    status: Literal["active", "draft", "archived", "superseded"] = "active"
    confidence: Literal["declared", "quoted", "contracted", "estimated"] = "declared"
    conditions: str | None = None
    notes: str | None = None


class ImportRowOut(BaseModel):
    line_number: int
    is_valid: bool
    is_duplicate: bool
    errors: list[dict[str, Any]]
    normalized: dict[str, Any] | None
    raw: dict[str, Any]


class ImportPreviewOut(BaseModel):
    batch_id: str
    filename: str
    sha256: str
    status: str
    strategy: str
    row_count: int
    valid_count: int
    error_count: int
    duplicate_count: int
    column_mapping: dict[str, str]
    meta: dict[str, Any]
    rows: list[ImportRowOut]


class ImportCommitRequest(BaseModel):
    strategy: Literal["create", "replace", "ignore", "merge"] = "create"
    confirm: bool = Field(
        default=False,
        description="Confirmation explicite de l'écriture dans la bibliothèque.",
    )


class ImportCommitOut(BaseModel):
    batch_id: str
    created: int
    updated: int
    skipped: int
    conflicted: int
    #: Lignes jugées valides à la prévisualisation et refusées à l'écriture.
    #: Les taire ferait croire à un import complet.
    rejected_at_commit: int = 0
    strategy: str
    details: list[dict[str, Any]]


class _ComponentBase(BaseModel):
    """Ce que tout composant porte, quel que soit son type."""

    # `extra="forbid"` est le cœur de ce découpage : un composant ne peut plus
    # porter les champs d'un autre type. Le modèle unique précédent acceptait
    # `output_rate` sur un forfait ou `distance_km` sur une consommation, et
    # les ignorait en silence — l'utilisateur croyait avoir paramétré quelque
    # chose qui n'entrait dans aucun calcul.
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=255)
    resource_kind: Literal[
        "material", "labor", "equipment", "transport", "disposal", "subcontract", "other"
    ] = "other"


class ConsumptionComponentIn(_ComponentBase):
    """Ressource consommée proportionnellement à la quantité du poste."""

    component_type: Literal["consumption"]
    consumption: Decimal = _bounded(bounds.COEFFICIENT)
    resource_unit_code: str = Field(min_length=1, max_length=12)
    unit_price: Decimal = _bounded(bounds.UNIT_PRICE)
    loss_ratio: Decimal | None = _bounded_opt(bounds.COEFFICIENT)
    convert_boq_quantity: bool = False
    density_value: Decimal | None = Field(
        default=None, gt=bounds.DENSITY.minimum, le=bounds.DENSITY.maximum
    )
    #: Obligatoire dès qu'une masse volumique est fournie : une tonne facturée
    #: sans source est une tonne indéfendable devant le client.
    density_source: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _density_needs_its_source(self) -> ConsumptionComponentIn:
        if self.density_value is not None and not (self.density_source or "").strip():
            raise ValueError(
                "Une masse volumique doit indiquer sa source (rapport de sol, "
                "fiche fournisseur, essai). Sans elle, la conversion n'est pas "
                "justifiable."
            )
        if self.density_source and self.density_value is None:
            raise ValueError("Une source de masse volumique sans valeur n'a pas d'effet.")
        return self


class OutputRateComponentIn(_ComponentBase):
    """Ressource dont le coût dérive d'un rendement horaire."""

    component_type: Literal["output_rate"]
    #: Diviseur : strictement positif, sinon la ligne est incalculable.
    output_rate: Decimal = Field(gt=bounds.OUTPUT_RATE.minimum, le=bounds.OUTPUT_RATE.maximum)
    hourly_rate: Decimal = _bounded(bounds.UNIT_PRICE)
    #: Un atelier de zéro personne ne produit rien : strictement positif.
    crew_size: Decimal | None = Field(default=None, gt=Decimal(0), le=bounds.COEFFICIENT.maximum)


class RotationComponentIn(_ComponentBase):
    """Transport compté en rotations, éventuellement majoré au kilomètre."""

    component_type: Literal["rotation"]
    #: Diviseur de la quantité : strictement positif.
    payload_value: Decimal = Field(gt=Decimal(0), le=bounds.COEFFICIENT.maximum)
    payload_unit_code: str = Field(min_length=1, max_length=12)
    #: Obligatoire. Le moteur, `REQUIRED_FIELDS` et les données existantes le
    #: supposent tous présents ; le rendre facultatif avait créé deux contrats
    #: contradictoires, dont l'un produisait un `TypeError` à la
    #: reconstruction d'un instantané.
    cost_per_rotation: Decimal = _bounded(bounds.UNIT_PRICE)
    round_up: bool = True
    distance_km: Decimal | None = _bounded_opt(bounds.DISTANCE_KM)
    rate_per_km: Decimal | None = _bounded_opt(bounds.UNIT_PRICE)
    #: Le cas central du terrassement : le bordereau est en m³, le camion est
    #: chargé en tonnes. Sans masse volumique sourcée, la conversion est
    #: impossible — et les avoir omis ici refusait purement et simplement le
    #: transport de terres, que le jeu de démonstration utilise.
    density_value: Decimal | None = Field(
        default=None, gt=bounds.DENSITY.minimum, le=bounds.DENSITY.maximum
    )
    density_source: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _coupled_fields_go_together(self) -> RotationComponentIn:
        has_distance = self.distance_km is not None
        has_rate = self.rate_per_km is not None
        if has_distance != has_rate:
            # Fournir l'un sans l'autre était silencieusement ignoré : le
            # kilométrage n'entrait dans aucun calcul et personne ne le voyait.
            missing = "rate_per_km" if has_distance else "distance_km"
            raise ValueError(
                f"« {missing} » manque. La distance et le tarif kilométrique "
                "vont ensemble : l'un sans l'autre ne produit aucun coût."
            )
        if self.density_value is not None and not (self.density_source or "").strip():
            raise ValueError(
                "Une masse volumique doit indiquer sa source (rapport de sol, "
                "fiche fournisseur, essai). Sans elle, la conversion n'est pas "
                "justifiable."
            )
        if self.density_source and self.density_value is None:
            raise ValueError("Une source de masse volumique sans valeur n'a pas d'effet.")
        return self


class LumpSumComponentIn(_ComponentBase):
    """Montant fixe, indépendant de la quantité du poste."""

    component_type: Literal["lump_sum"]
    lump_sum_amount: Decimal = _bounded(bounds.TOTAL)


#: Union discriminée par `component_type`. Pydantic choisit le modèle exact et
#: refuse tout champ étranger à ce type.
ComponentSpecIn = Annotated[
    ConsumptionComponentIn | OutputRateComponentIn | RotationComponentIn | LumpSumComponentIn,
    Field(discriminator="component_type"),
]


class CompositePriceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=255)
    unit_code: str = Field(min_length=1, max_length=12)
    notes: str | None = None
    components: list[ComponentSpecIn] = Field(
        min_length=1, max_length=bounds.MAX_COMPONENTS_PER_LINE
    )


class CompositePriceUpdate(CompositePriceCreate):
    """Une modification COMPLÈTE, jamais partielle.

    Les composants sont remplacés en bloc plutôt que rapiécés un par un :
    fusionner une liste ordonnée avec des ajouts, retraits et déplacements
    demande une sémantique de rapiéçage que personne n'a écrite, et qui se
    trompe silencieusement dès que deux éditeurs la sollicitent en même temps.

    `revision` est le jeton lu au chargement. Le serveur refuse en 409 si la
    ligne a bougé depuis — c'est ce qui empêche le second éditeur d'écraser le
    premier sans le savoir.
    """

    revision: int = Field(ge=1)


class CompositeDuplicate(BaseModel):
    """Le code du duplicata, obligatoire : il est unique dans la version."""

    code: str = Field(min_length=1, max_length=60)
    label: str | None = Field(default=None, max_length=255)


class CompositePreviewIn(BaseModel):
    """De quoi calculer un coût unitaire sans rien écrire."""

    unit_code: str = Field(min_length=1, max_length=12)
    components: list[ComponentSpecIn] = Field(
        min_length=1, max_length=bounds.MAX_COMPONENTS_PER_LINE
    )


class CompositePreviewOut(ApiModel):
    """Le déboursé sec d'une unité, et sa ventilation — calculés par le moteur."""

    unit_code: str
    currency: str
    #: `false` quand un composant à rotations arrondies rend le coût NON
    #: proportionnel : le chiffre reste exact pour une unité, mais le
    #: multiplier surestime. L'écran le dit plutôt que de laisser croire à une
    #: règle de trois.
    scales_linearly: bool
    #: Le décimal EXACT, non arrondi — pour qui veut recalculer.
    unit_cost: str
    #: Le même, arrondi selon la politique de l'organisation — pour qui lit.
    #: Les deux voyagent ensemble, comme pour les totaux d'une estimation :
    #: l'écran affiche le second et n'invente aucun arrondi.
    unit_cost_display: str
    by_kind: list[dict[str, Any]]
    components: list[dict[str, Any]]


class CompositePriceOut(ApiModel):
    id: str
    code: str
    label: str
    unit_code: str
    notes: str | None
    is_demo_data: bool
    #: Le jeton de concurrence. À renvoyer tel quel dans une modification.
    revision: int
    #: `true` quand la version de bibliothèque est publiée : le sous-détail est
    #: alors en lecture seule, et l'écran ne doit proposer aucune commande
    #: d'écriture qui échouerait.
    version_published: bool
    #: Combien de postes de bordereau s'en servent. Zéro autorise la
    #: suppression ; au-delà, elle est refusée en 409.
    referenced_by: int
    components: list[dict[str, Any]]


# -- bill of quantities ----------------------------------------------------


class BoqCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source: Literal["manual", "import", "client"] = "manual"
    notes: str | None = None


class BoqOut(ApiModel):
    id: str
    project_id: str
    name: str
    source: str
    revision: int
    notes: str | None
    created_at: datetime


class BoqItemCreate(BaseModel):
    position: str = Field(min_length=1, max_length=40)
    designation: str = Field(min_length=1)
    unit_code: str = Field(default="fft", max_length=12)
    quantity: Decimal = Field(
        default=Decimal("0"), ge=bounds.QUANTITY.minimum, le=bounds.QUANTITY.maximum
    )
    kind: Literal["section", "item", "option", "variant", "provisional"] = "item"
    code: str | None = Field(default=None, max_length=60)
    sort_index: int | None = None
    formula: str | None = None
    client_quantity: Decimal | None = _bounded_opt(bounds.QUANTITY)
    notes: str | None = None
    price_item_id: str | None = None
    composite_price_id: str | None = None


class BoqItemTransition(BaseModel):
    """Changement de statut explicite, distinct d'une modification de contenu."""

    status: Literal["proposed", "verified", "approved", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


class BoqItemUpdate(BaseModel):
    # Un champ inconnu est refusé plutôt qu'ignoré. Sans cela, un
    # `PATCH {"status": "approved"}` renvoyait 200 sans rien changer : le
    # privilège n'était pas obtenu, mais l'appelant croyait l'avoir eu, et le
    # défaut restait invisible en lisant les réponses.
    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    unit_code: str | None = None
    quantity: Decimal | None = _bounded_opt(bounds.QUANTITY)
    kind: Literal["section", "item", "option", "variant", "provisional"] | None = None
    # `status` est délibérément absent. Le laisser ici rendait la matrice
    # route-permission verte tout en offrant une élévation de privilège par un
    # champ : un porteur de BOQ_WRITE refusé sur /approve obtenait le même
    # résultat par PATCH. Les changements de statut passent par
    # /boq-items/{id}/transition, qui exige BOQ_APPROVE.
    formula: str | None = None
    client_quantity: Decimal | None = _bounded_opt(bounds.QUANTITY)
    notes: str | None = None
    price_item_id: str | None = None
    composite_price_id: str | None = None
    sort_index: int | None = None
    #: Required to change the quantity of an approved item (scenario: "ne modifie
    #: jamais automatiquement une quantité approuvée").
    override_approved: bool = False
    override_reason: str | None = None


class BoqItemOut(DecimalOut):
    id: str
    boq_id: str
    position: str
    code: str | None
    designation: str
    unit_code: str
    quantity: Decimal
    kind: str
    status: str
    formula: str | None
    client_quantity: Decimal | None
    notes: str | None
    price_item_id: str | None
    composite_price_id: str | None
    sort_index: int
    #: La mesure de plan reprise, et son empreinte figée.
    #:
    #: `None` sur une ligne saisie à la main, ce qui est le cas courant. Rendus
    #: tous les deux : le lien permet de rouvrir le plan à la bonne page tant
    #: qu'il existe, et l'empreinte dit d'où vient le nombre même si ce lien
    #: est un jour dénoué.
    source_proposal_id: str | None = None
    source_mesure: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def quantity_lisible(self) -> str:
        """La quantité et son unité, écrites pour être lues.

        **Rendue par le serveur, et non par l'écran.** C'est la règle du dépôt :
        deux arrondis — un en Python, un en TypeScript — finiraient par diverger
        d'un chiffre, et c'est le genre d'écart qu'on ne voit jamais venir. La
        valeur exacte reste rendue à côté, dans `quantity`, et c'est elle qu'on
        reprend pour calculer.

        Une section n'a pas de quantité à montrer : son `0` n'est pas une
        mesure, c'est l'absence de ligne.
        """
        from .services import lisible

        if self.kind == "section":
            return ""
        return lisible.quantite_de_document_lisible(self.quantity, self.unit_code)


class ApercuDeRepriseCreate(BaseModel):
    """Ce qu'il faut pour CALCULER une reprise sans l'écrire.

    La position et la désignation n'y sont pas : elles ne changent pas la
    quantité, et les demander avant de montrer le nombre obligerait à saisir
    avant de savoir ce qu'on saisit.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    unite_cible: str | None = Field(default=None, max_length=12)


class ApercuDeReprise(DecimalOut):
    """La quantité qu'une reprise écrirait, et d'où elle vient.

    **Pourquoi cette route existe, alors que la conversion tient en un facteur
    mille.** Parce que ce facteur serait alors écrit DEUX fois : une en Python,
    une en TypeScript. Les deux finiraient par diverger — c'est la règle que le
    dépôt applique déjà au facteur d'échelle, aux valeurs lisibles et aux
    totaux d'un devis. L'écran doit pouvoir montrer le nombre avant de l'écrire
    sans le calculer lui-même.

    Et le patron est celui du dépôt depuis l'import de prix : **prévisualiser,
    puis confirmer.** Rien n'est écrit tant que la personne n'a pas vu.
    """

    #: La quantité exacte, dans `unite`. C'est elle qui serait écrite.
    quantite: Decimal
    #: Le code d'unité canonique de la quantité.
    unite: str
    #: La même quantité, écrite pour être LUE, arrondie à la décimale que
    #: l'incertitude de la mesure autorise.
    quantite_lisible: str
    #: Une phrase qui dit d'où vient le nombre : page, décision, valeur retenue.
    provenance_lisible: str


class RepriseDeMesureCreate(BaseModel):
    """Reprendre une mesure tranchée dans une ligne de bordereau.

    **La quantité n'y figure pas, et c'est tout l'objet de cette route.** Elle
    vient de la mesure et de la décision humaine qui l'a retenue ; la laisser
    déclarer ici rendrait possible une ligne qui annonce une provenance et
    porte un autre nombre, ce qui est pire que pas de provenance du tout.

    `unite_cible` est la seule conversion du parcours, et elle est explicite :
    une mesure en millimètres se reprend en mètres si la personne le demande,
    jamais d'office. Omise, l'unité de la mesure est conservée telle quelle.
    """

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    position: str = Field(min_length=1, max_length=40)
    designation: str = Field(min_length=1)
    unite_cible: str | None = Field(default=None, max_length=12)
    kind: Literal["section", "item", "option", "variant", "provisional"] = "item"
    code: str | None = Field(default=None, max_length=60)
    sort_index: int | None = None
    notes: str | None = None


class BoqItemBulkCreate(BaseModel):
    items: list[BoqItemCreate] = Field(min_length=1, max_length=2000)


# -- estimates -------------------------------------------------------------


class EstimateCreate(BaseModel):
    project_id: str
    boq_id: str
    price_book_version_id: str
    name: str = Field(min_length=1, max_length=200)


class EstimateOut(ApiModel):
    id: str
    project_id: str
    boq_id: str
    price_book_version_id: str
    name: str
    currency: str
    created_at: datetime


class EstimateVersionOut(DecimalOut):
    """Une version et ses totaux.

    Deux familles de nombres, et il ne faut pas les confondre.

    ``total_selling_price_ht`` et ``total_ttc`` sont les montants **bruts**, non
    arrondis, tels que le moteur les a produits. Ils servent aux calculs
    internes et aux comparaisons.

    ``*_display`` est le total **du document** : exactement ce que le devis
    remis au client porte. Il ne s'obtient pas en arrondissant le brut — il est
    la somme des lignes imprimées — et une version gelée le conserve tel qu'il
    était le jour du gel.

    Quand il vaut ``None`` sur une version gelée, cela veut dire une seule
    chose : *le nombre imprimé n'a pas pu être reconstruit pour cette version
    ancienne*. On ne lui substitue jamais l'arrondi du brut, qui serait faux de
    quelques centimes sans que personne ne s'en aperçoive.
    ``document_totals_available`` dit lequel des deux cas le client regarde.
    """

    id: str
    estimate_id: str
    version_number: int
    label: str | None
    status: str
    price_book_version_id: str
    rounding: dict[str, Any] = Field(default_factory=dict)
    total_selling_price_ht: Decimal | None
    total_ttc: Decimal | None
    document_total_ht: Decimal | None = None
    document_total_ttc: Decimal | None = None
    snapshot_sha256: str | None
    frozen_at: datetime | None
    created_at: datetime

    def _quantize(self, value: Decimal | None) -> str | None:
        """Met une valeur à l'échelle d'affichage de la version.

        Utilisé pour le brouillon, dont le document se recalcule à la demande
        et n'est donc pas figé. Jamais pour combler l'absence d'un total
        documentaire sur une version gelée.
        """
        if value is None:
            return None
        from metreo_domain.money import RoundingPolicy

        policy = RoundingPolicy(
            scale=int(self.rounding.get("scale", 2)),
            mode=str(self.rounding.get("mode", "half_up")),
            unit_price_scale=self.rounding.get("unit_price_scale"),
        )
        return str(policy.quantize(value))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_totals_available(self) -> bool:
        """Le total imprimé de cette version est-il connu ?

        Faux pour une version gelée avant l'introduction des totaux
        documentaires et dont l'instantané n'a pas permis la reconstruction.
        Le client doit alors afficher une absence, pas un nombre approchant.
        """
        return self.document_total_ht is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_selling_price_ht_display(self) -> str | None:
        """Le Total HT **du document**, ou rien.

        C'est la même valeur que le CSV, l'aperçu HTML et le calcul renvoient :
        la liste des versions ne doit pas annoncer un autre nombre que le devis.
        """
        # Quantifié, pas seulement canonicalisé : la valeur est déjà à
        # l'échelle d'affichage, mais la colonne la rend avec les dix décimales
        # du NUMERIC(28, 10). Sans cela la liste écrirait « 99097.0700000000 »
        # là où le devis imprime « 99097.07 » — même nombre, autre orthographe.
        if self.document_total_ht is not None:
            return self._quantize(self.document_total_ht)
        if self.status == "frozen":
            return None
        return self._quantize(self.total_selling_price_ht)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ttc_display(self) -> str | None:
        if self.document_total_ttc is not None:
            return self._quantize(self.document_total_ttc)
        if self.status == "frozen":
            return None
        return self._quantize(self.total_ttc)


class EstimateVersionCreate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    price_book_version_id: str | None = None


class FreezeRequest(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    confirm: bool = Field(
        default=False, description="Confirmation explicite: le gel est irréversible."
    )


class ComputationOut(BaseModel):
    version: EstimateVersionOut
    computed_at: datetime
    from_snapshot: bool
    includes_internal_costs: bool
    result: dict[str, Any]


class HypothesesIn(BaseModel):
    """Les écarts relatifs d'un scénario : `0.10` vaut « +10 % ».

    Aucune valeur par défaut autre que zéro. Proposer « -10 % / 0 / +10 % »
    reviendrait à souffler à l'utilisateur des hypothèses que rien ne fonde :
    la dispersion d'un chiffrage dépend du chantier, du marché et du moment,
    et personne ici n'est en position de la deviner à sa place.
    """

    model_config = ConfigDict(extra="forbid")

    #: Coûts unitaires d'entrée : prix de ressource, taux horaire, coût de
    #: rotation et coût kilométrique. PAS les forfaits.
    prix: Decimal = Decimal("0")
    #: Vide = toutes les catégories de ressource.
    prix_categories: list[str] = Field(default_factory=list)
    #: Rendement des composants qui en ont un. `+0.10` = produire 10 % de plus
    #: par heure, donc MOINS d'heures, donc un coût qui BAISSE.
    productivite: Decimal = Decimal("0")
    #: Distance des transports. Traverse le nombre ENTIER de rotations, donc
    #: son effet n'est pas proportionnel.
    distance: Decimal = Decimal("0")


class ScenariosIn(BaseModel):
    """Les trois scénarios à chiffrer.

    Les trois sont facultatifs et valent « neutre » par défaut : trois
    scénarios neutres reproduisent trois fois le calcul de référence, ce qui
    est le point de départ honnête d'une comparaison.
    """

    model_config = ConfigDict(extra="forbid")

    bas: HypothesesIn = Field(default_factory=HypothesesIn)
    probable: HypothesesIn = Field(default_factory=HypothesesIn)
    haut: HypothesesIn = Field(default_factory=HypothesesIn)


class HypothesesOut(BaseModel):
    """Les hypothèses TELLES QU'APPLIQUÉES, relues du serveur.

    Rendues plutôt que supposées identiques à l'envoi : l'écran affiche ce qui
    a servi au calcul, pas ce qu'il croit avoir demandé.
    """

    prix: str
    prix_categories: list[str]
    productivite: str
    distance: str


class EcartOut(BaseModel):
    """L'écart au scénario probable."""

    #: Le décimal EXACT, pour qui recalcule. Un écart de productivité est
    #: périodique : 750,00 ÷ 1,1 ne tombe pas juste.
    absolu: str
    #: Le même écart, arrondi par la politique de l'organisation. C'est celui
    #: que l'écran affiche — arrondir dans le navigateur ferait diverger ce
    #: chiffre du devis au premier centime.
    absolu_display: str
    #: `None` quand la référence est nulle : une division par zéro n'a pas de
    #: résultat, et rendre « 0 % » ferait passer une absence d'information pour
    #: une information.
    pourcentage: str | None


class ScenarioCalculeOut(BaseModel):
    """Un scénario qui a produit des totaux."""

    status: Literal["success"] = "success"
    nom: str
    hypotheses: HypothesesOut
    #: La même structure que `ComputationOut.result`, filtrée par les mêmes
    #: décisions `cost:read` / `margin:read`.
    totaux: dict[str, Any]
    #: Les lignes sans prix, NOMMÉES et jamais comptées pour zéro.
    lignes_sans_prix: list[str]
    bloquant: bool
    #: Absent tant que le scénario probable n'a pas lui-même abouti.
    ecart: EcartOut | None = None


class ScenarioRefuseOut(BaseModel):
    """Un scénario qui n'a pas pu être chiffré.

    Il vit dans la même liste que les autres, à sa place : un refus ne doit pas
    faire disparaître les deux scénarios voisins, et l'écran a besoin de savoir
    POUR LEQUEL il n'a rien à montrer.
    """

    status: Literal["refused"] = "refused"
    nom: str
    hypotheses: HypothesesOut
    refus: dict[str, Any]


#: Union DISCRIMINÉE : un scénario porte ses totaux ou son refus, jamais les
#: deux et jamais aucun des deux. Un `dict[str, Any]` ne promettait rien de
#: tel, et l'écran devait deviner en testant la présence d'une clé.
ScenarioOut = Annotated[ScenarioCalculeOut | ScenarioRefuseOut, Field(discriminator="status")]


class ScenariosOut(BaseModel):
    """Le résultat de la simulation. Rien n'a été écrit pour le produire."""

    version: EstimateVersionOut
    computed_at: datetime
    from_snapshot: bool
    #: `cost:read` : ressources, déboursés et coûts.
    includes_internal_costs: bool
    #: `margin:read` : étapes de markup, avec leurs taux, bases et formules.
    #: Séparé des coûts, parce que le rôle `estimator` porte l'un sans l'autre.
    includes_margin_steps: bool
    currency: str
    #: Un par scénario, dans l'ordre bas / probable / haut — jamais réordonné.
    scenarios: list[ScenarioOut]
    #: Les totaux ne suivent pas l'ordre que les libellés suggèrent. Signalé,
    #: jamais corrigé : réordonner masquerait l'information la plus utile.
    ordre_incoherent: bool
    #: Les catégories que l'écran peut proposer, avec leur libellé. Rendues par
    #: le serveur pour que l'interface n'en tienne pas une seconde liste.
    categories: dict[str, str]


# -- audit -----------------------------------------------------------------


#: Les champs de `OrganizationSettings` qui révèlent la politique commerciale.
#:
#: Une seule liste, deux lecteurs : `/organization/settings` les remplace par
#: `null` pour qui n'a pas `margin:read`, et le journal d'audit masque leurs
#: valeurs `before`/`after` pour les mêmes appelants. Les tenir séparément,
#: c'est exactement ce qui a laissé la fuite ouvrir : les réglages masquaient
#: `margin_rate`, le journal le rendait en clair au même utilisateur.
CHAMPS_COMMERCIAUX_SENSIBLES: Final[frozenset[str]] = frozenset(
    {
        "site_overheads_rate",
        "site_overheads_base",
        "general_overheads_rate",
        "general_overheads_base",
        "contingency_rate",
        "contingency_base",
        "margin_rate",
        "margin_method",
    }
)


class AuditEventOut(ApiModel):
    id: str
    sequence: int
    occurred_at: datetime
    actor_user_id: str | None
    actor_email: str | None
    action: str
    object_type: str
    object_id: str | None
    summary: str
    payload: dict[str, Any]
    #: Vrai quand des valeurs commerciales ont été retirées du payload rendu.
    #: Le payload STOCKÉ n'est jamais modifié — son empreinte reste celle qui a
    #: été scellée, et `/audit/verify` continue de la recalculer à l'identique.
    payload_redacted: bool = False
    hash: str
    previous_hash: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    page: Page


class AuditVerifyOut(BaseModel):
    valid: bool
    checked: int
    head_hash: str | None = None
    failed_at_sequence: int | None = None
    reason: str | None = None


# -- meta ------------------------------------------------------------------


class UnitOut(BaseModel):
    code: str
    dimension: str
    dimension_label: str
    label: str
    factor_to_base: str
    aliases: list[str]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    version: str
    ai_enabled: bool
    database: str
    configuration_problems: list[str]
    login_methods: list[Literal["dev", "oidc"]] = Field(
        default_factory=list,
        description=(
            "Moyens de connexion offerts à un navigateur sur ce déploiement. "
            "Vide sur un déploiement d'API pure : les jetons sont acceptés, "
            "aucun n'est émis ici."
        ),
    )


LimitQuery = Annotated[int, Field(ge=1, le=200)]
OffsetQuery = Annotated[int, Field(ge=0)]
