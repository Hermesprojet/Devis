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
    show_internal_costs_in_client_pdf: bool
    ai_enabled: bool


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


class ProjectOut(ApiModel):
    id: str
    reference: str
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


class CompositePriceOut(ApiModel):
    id: str
    code: str
    label: str
    unit_code: str
    notes: str | None
    is_demo_data: bool
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
