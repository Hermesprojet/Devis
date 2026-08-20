"""Request and response contracts.

Pydantic models are the API surface: FastAPI validates against them and derives
the OpenAPI document from them. Decimal fields stay :class:`~decimal.Decimal`
and are serialised as JSON strings so no client ever reads a rounded float.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_serializer,
)

from metreo_domain import bounds
from metreo_domain.money import canonical_text


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
    rounding_scale: int | None = Field(default=None, ge=0, le=6)
    rounding_mode: Literal["half_up", "half_even"] | None = None
    unit_price_scale: int | None = Field(default=None, ge=0, le=6)
    site_overheads_rate: Decimal | None = Field(default=None, ge=0, le=10)
    site_overheads_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = None
    general_overheads_rate: Decimal | None = Field(default=None, ge=0, le=10)
    general_overheads_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = (
        None
    )
    contingency_rate: Decimal | None = Field(default=None, ge=0, le=10)
    contingency_base: Literal["direct_cost", "direct_plus_site", "running_total"] | None = None
    margin_rate: Decimal | None = Field(default=None, ge=0, lt=10)
    margin_method: Literal["on_cost", "on_price"] | None = None
    missing_price_policy: Literal["block", "warn"] | None = None
    quote_number_pattern: str | None = Field(default=None, max_length=60)
    show_internal_costs_in_client_pdf: bool | None = None


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


class ProjectCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=255)
    client_reference: str | None = Field(default=None, max_length=120)
    description: str | None = None
    client_name: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=120)
    country_code: str = Field(default="BE", min_length=2, max_length=2)
    region_code: str = Field(default="BE-WAL", max_length=10)
    market_type: str | None = Field(default=None, max_length=60)
    work_categories: list[str] = Field(default_factory=list)
    submission_deadline: datetime | None = None
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    locale: str = Field(default="fr-BE", max_length=10)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    client_reference: str | None = None
    description: str | None = None
    client_name: str | None = None
    address: str | None = None
    postal_code: str | None = None
    city: str | None = None
    region_code: str | None = None
    market_type: str | None = None
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


class PriceItemCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=255)
    unit_code: str = Field(min_length=1, max_length=12)
    unit_price: Decimal = _bounded(bounds.UNIT_PRICE)
    family: str | None = None
    resource_kind: Literal[
        "material", "labor", "equipment", "transport", "disposal", "subcontract", "other"
    ] = "material"
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    supplier_name: str | None = None
    region_code: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source: str | None = None
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
    strategy: str
    details: list[dict[str, Any]]


class ComponentSpecIn(BaseModel):
    component_type: Literal["consumption", "output_rate", "rotation", "lump_sum"]
    label: str = Field(min_length=1, max_length=255)
    resource_kind: Literal[
        "material", "labor", "equipment", "transport", "disposal", "subcontract", "other"
    ] = "other"
    consumption: Decimal | None = _bounded_opt(bounds.COEFFICIENT)
    resource_unit_code: str | None = None
    unit_price: Decimal | None = _bounded_opt(bounds.UNIT_PRICE)
    loss_ratio: Decimal | None = _bounded_opt(bounds.COEFFICIENT)
    convert_boq_quantity: bool = False
    density_value: Decimal | None = _bounded_opt(bounds.DENSITY)
    density_source: str | None = None
    output_rate: Decimal | None = _bounded_opt(bounds.OUTPUT_RATE)
    hourly_rate: Decimal | None = _bounded_opt(bounds.UNIT_PRICE)
    crew_size: Decimal | None = _bounded_opt(bounds.COEFFICIENT)
    payload_value: Decimal | None = _bounded_opt(bounds.COEFFICIENT)
    payload_unit_code: str | None = None
    cost_per_rotation: Decimal | None = _bounded_opt(bounds.UNIT_PRICE)
    round_up: bool = True
    distance_km: Decimal | None = _bounded_opt(bounds.DISTANCE_KM)
    rate_per_km: Decimal | None = _bounded_opt(bounds.UNIT_PRICE)
    lump_sum_amount: Decimal | None = _bounded_opt(bounds.TOTAL)


class CompositePriceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=255)
    unit_code: str = Field(min_length=1, max_length=12)
    notes: str | None = None
    components: list[ComponentSpecIn] = Field(min_length=1)


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
    """A version and its totals.

    ``total_selling_price_ht`` is the **stored, unrounded** amount — the value
    the engine produced. ``*_display`` applies the version's own rounding policy
    and is what a screen or a document should print. Exposing both keeps the
    rounding a single, explicit step instead of something each client redoes.
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
    snapshot_sha256: str | None
    frozen_at: datetime | None
    created_at: datetime

    def _quantize(self, value: Decimal | None) -> str | None:
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
    def total_selling_price_ht_display(self) -> str | None:
        return self._quantize(self.total_selling_price_ht)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ttc_display(self) -> str | None:
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


LimitQuery = Annotated[int, Field(ge=1, le=200)]
OffsetQuery = Annotated[int, Field(ge=0)]
