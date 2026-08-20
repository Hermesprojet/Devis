---
name: price-engine
description: À utiliser dès qu'il faut toucher au moteur de calcul déterministe de Metreo (phase 1, implémenté) — modifier packages/domain/src/metreo_domain/ (money.py, units.py, pricing.py, estimate.py, errors.py) ou apps/api/src/metreo_api/services/composites.py et estimating.py, ajouter ou corriger un composant de sous-détail (consumption, output_rate, rotation, lump_sum), calculer un déboursé sec, un prix de revient ou un prix de vente HT, régler frais de chantier, frais généraux, aléas, marge on_cost/on_price, taxes ou arrondis (RoundingPolicy), convertir des unités ou passer d'un volume à une masse via une masse volumique sourcée (AmbiguousConversionError, IncompatibleUnitsError), traiter un poste sans prix (MissingPricePolicy), geler ou recalculer un devis depuis son instantané, ou diagnostiquer un total faux, un écart de centimes ou un rendement nul. C'est le seul skill qui décrit une arithmétique, les autres n'y renvoient que le résultat.
---

# Metreo — moteur de calcul déterministe (phase 1, implémenté)

## 1. Périmètre

| Fichier | Rôle |
| --- | --- |
| `packages/domain/src/metreo_domain/money.py` | `Money`, `RoundingPolicy`, `to_decimal`, `money_sum`, `WORKING_PRECISION` |
| `packages/domain/src/metreo_domain/units.py` | `Unit`, `Dimension`, `Quantity`, `Density`, `convert`, `get_unit` |
| `packages/domain/src/metreo_domain/pricing.py` | les 4 composants, `MarkupPolicy`, `compute_line_price`, `compute_flat_line_price` |
| `packages/domain/src/metreo_domain/estimate.py` | `EstimateLineInput`, `LineKind`, `MissingPricePolicy`, `compute_estimate`, `sensitivity` |
| `packages/domain/src/metreo_domain/errors.py` | `DomainError` et sous-classes, chacune avec un `code` stable |
| `apps/api/src/metreo_api/services/composites.py` | spec JSON ↔ composant du domaine |
| `apps/api/src/metreo_api/services/estimating.py` | lignes stockées → run moteur, snapshot, gel |

Le domaine est **pur** : ni FastAPI, ni SQLAlchemy, ni `datetime.now()`. Le vocabulaire métier BTP
relève de **btp-product-rules**, la TVA belge de **belgium-regulatory-pack**, le masquage des coûts
internes de **multitenant-security**, la provenance des quantités de **document-analysis** et
**cad-bim-takeoff**, les prix d'offres de **supplier-rfq**, les critères de sortie de
**definition-of-done**.

## 2. Decimal, jamais de float binaire

- Tout montant, taux, quantité, consommation, rendement passe par `to_decimal()` : `Decimal`,
  `int`, `str` (virgule française et espaces gérés), `float` **via son `repr`** — jamais `Decimal(float)`.
- Multiplications et divisions sous `localcontext()` avec `ctx.prec = WORKING_PRECISION` (28) : ne pas changer cette valeur, elle fixe la reproductibilité bit à bit.
- `Money.__add__` / `__sub__` lèvent `CurrencyMismatchError` plutôt que de deviner un taux de change ; `money_sum([], "EUR")` renvoie un zéro typé, pas `0`.
- Interdits dans le domaine : `float(...)`, `round(...)`, `%`, `math.*` sur un montant.

## 3. Non arrondi en interne, arrondi une seule fois à la sortie

- `Money.amount` est **toujours** non arrondi. `1179.144000 EUR` est une valeur légitime.
- L'arrondi n'existe que dans les `to_dict(policy)` (`ComponentResult`, `MarkupStepResult`,
  `LinePriceResult`, `EstimateResult`) via `RoundingPolicy.quantize` / `quantize_unit_price`. Seules
  exceptions tolérées, n'en créer aucune autre : `EstimateVersionOut._quantize`
  (`apps/api/src/metreo_api/schemas.py`), qui réapplique la politique gelée aux totaux persistés,
  et `sensitivity()`, qui rend ses écarts via `DEFAULT_ROUNDING`.
- `RoundingPolicy(scale, mode, unit_price_scale)` : `mode` ∈ `half_up` | `half_even`, `scale` ∈ 0..12,
  `unit_price_scale` peut différer. Source : `OrganizationSettings`, lu par `rounding_from_settings`.
- Colonnes monétaires = type `Amount` de `apps/api/src/metreo_api/db.py` (`Numeric(28,10)` sur
  PostgreSQL, texte exact sur SQLite) : **10 décimales persistées au plus**. Ne jamais comparer
  deux bruts avec `==` ; comparer `DEFAULT_ROUNDING.quantize(a) == DEFAULT_ROUNDING.quantize(b)`.
- Les décimaux sortent en JSON sous forme de **chaînes**. `selling_price_ht_raw` et `amount_raw`
  exposent volontairement la valeur non arrondie.

## 4. Toute quantité porte une unité

- `Quantity.of(value, unit_code)` uniquement ; un nombre nu n'est jamais une quantité.
- `get_unit()` résout les alias (`ml`→`m`, `m²`→`m2`, `tonne`/`tn`→`t`, `j`→`d`, `ff`→`fft`) et
  lève `UnknownUnitError` plutôt que d'inventer une unité. Ajouter une unité = un `Unit` de plus
  dans `_UNITS` (un alias en double fait échouer `_build_index` au chargement du module).
- `convert()` dans une même `Dimension` = rapport exact de `factor_to_base`.
- **Volume ↔ masse** : refusé sans `Density`, qui exige une `source` non vide et une valeur
  strictement positive. Sans elle → `AmbiguousConversionError` (code `ambiguous_conversion`).
- **Dimensions non pontables** (m2 vers h, m vers kg…) → `IncompatibleUnitsError` (code
  `incompatible_units`). Volume ↔ masse est le seul pont ; n'en ajouter aucun sans paramètre sourcé.
- `ConversionResult.explanation` est reporté dans le `formula` du composant et `density_used.source`
  dans `ComponentResult.density_source`. Une tonne facturée sans source est un défaut.

## 5. Les quatre types de composants

Chacun implémente le `Protocol` `Component` : `compute(boq_quantity, currency) -> ComponentResult`. Exemples ci-dessous vérifiés en exécutant le moteur.

**1. `ConsumptionComponent`** — ressource proportionnelle à la quantité du poste.
```
base = boq_quantity.value  (ou convert(boq_quantity, resource_unit_code, density) si convert_boq_quantity)
qty  = base × consumption × (1 + loss_ratio)  ;  montant = qty × unit_price
```
100 m2 × 0,35 t/m2 × (1 + 0,05) = **36,7500 t** × 18 EUR/t = **661,5000 EUR**.
Avec `convert_boq_quantity=True` : 100 m3 × 1800 kg/m3 = 180 000 kg → 180 t × 12,90 EUR/t.

**2. `OutputRateComponent`** — équipe ou engin au rendement.
```
hours   = boq_quantity.value ÷ output_rate   (via _ratio → InvalidRateError si 0 ou < 0)
billed  = hours × crew_size                  (crew_size ≤ 0 → PricingConfigurationError)
montant = billed × hourly_rate
```
100 m2 ÷ 12 m2/h = 8,3333… h × 2 = 16,6666… h × 45 EUR/h = **750,0000000000000000000000002 EUR** ;
la valeur exacte 750,00 n'apparaît qu'après `quantize`.

**3. `RotationComponent`** — transport à la rotation.
```
aligned       = convert(boq_quantity, payload.unit.code, density=…)  (density requise si dimensions ≠)
raw_rotations = aligned ÷ payload.value ; rotations = ceil(raw) si round_up sinon raw
per_rotation  = cost_per_rotation + distance_km × rate_per_km  (si les deux sont fournis)
montant       = rotations × per_rotation
```
100 m3 ÷ 8 m3/rotation = 12,5 → **13 rotations** × (85 + 30 km × 1,20 = **121,00 EUR**) =
**1 573,00 EUR**. `round_up=True` par défaut : un demi-camion ne quitte pas le chantier.

**4. `LumpSumComponent`** — `montant = amount_value`, `resource_quantity = 1 fft` : 450 EUR quelle que soit la quantité du poste, y compris nulle.

`ResourceKind` (`material`, `labor`, `equipment`, `transport`, `disposal`, `subcontract`, `other`)
alimente le regroupement `LinePriceResult.cost_by_kind()`.

## 6. Chaîne déboursé sec → prix de vente HT

Ordre imposé par `compute_line_price`, jamais réordonné : `direct_cost` → frais de chantier →
frais généraux → **prix de revient** → aléas → marge → **prix de vente HT** → taxes à part.
Exemple vérifié, déboursé sec 1 000,00 EUR, taux 8 % / 6 % / 3 % / 10 % :

| Étape (`key`) | `OverheadBase` | Base | Taux | Montant | Cumul |
| --- | --- | --- | --- | --- | --- |
| `site_overheads` | `direct_cost` | 1 000 | 0,08 | 80,00 | 1 080,00 |
| `general_overheads` | `direct_plus_site` | 1 080,00 | 0,06 | 64,8000 | **1 144,8000** = prix de revient |
| `contingency` | `running_total` | 1 144,8000 | 0,03 | 34,344000 | 1 179,144000 |
| `margin` (`on_cost`) | — | 1 179,144000 | 0,10 | 117,91440000 | 1 297,05840000 → **1 297,06** |
| `margin` (`on_price`) | — | 1 179,144000 | 0,10 | 131,016000 | 1 310,160000 → **1 310,16** |

- `MarginMethod.ON_COST` : `vente = base × (1 + taux)`, la marge vaut 10 % du **coût**.
  `MarginMethod.ON_PRICE` : `vente = base ÷ (1 − taux)`, elle vaut 10 % du **prix de vente**
  (131,016 / 1 310,16 = 10 % exactement). Écart entre les deux ici : **13,10 EUR**. `margin_rate ≥ 1`
  avec `ON_PRICE` lève `PricingConfigurationError` à la construction.
- `OverheadBase.DIRECT_COST` sur les frais généraux donnerait 1 000 × 0,06 → prix de revient
  1 140,00 au lieu de 1 144,80. Au 2ᵉ rang `direct_plus_site` et `running_total` coïncident par
  construction ; leur écart n'apparaît qu'après les aléas.
- Toutes les étapes sont émises **même à taux nul**, pour montrer qu'elles ont été considérées.
- `unit_price_ht = selling_price_ht ÷ quantity` ; quantité 0 → prix unitaire 0 sans division ;
  quantité négative → `InvalidRateError`.
- Taxes : `TaxRate(code, label, rate, applies_from)` appliquées à `selling_price_ht`, jamais fondues
  dedans ; `total_ttc` est dérivé. Taux en vigueur : `active_taxes()` (`estimating.py`).

## 7. Prix de bibliothèque : l'unité doit correspondre au poste

`compute_flat_line_price(..., price_unit_code=..., density=...)` construit un
`ConsumptionComponent` à consommation 1 et n'active `convert_boq_quantity` que si le code
canonique du prix diffère de celui du poste (un prix en `ml` sur un poste en `m` ne convertit
rien). Trois issues, jamais une quatrième : (1) même dimension → conversion faite et **tracée**
dans le `formula` ; (2) volume ↔ masse **avec** `Density` sourcée → conversion faite, source
reportée ; (3) sinon refus (`ambiguous_conversion` ou `incompatible_units`).

`inputs_from_specs` (`estimating.py`) calcule chaque ligne isolément avant l'agrégat, pour
imputer le refus **au poste fautif** et joindre un `hint` réclamant un sous-détail explicite.
Il lève alors `PricingInputError` avec la liste complète : jamais d'estimation partielle.

## 8. Poste sans prix : jamais valorisé à zéro

- Résolution d'un poste (`build_line_specs`) : composite explicite, puis prix de bibliothèque lié
  **dans la version de bibliothèque gelée par la version de devis**, puis rien.
- « Rien » reste « rien » : `price=None`, `missing_price=True`, `included_in_total=False`,
  `line_id` ajouté à `missing_price_line_ids`. Le poste ne contribue à aucun total.
- `MissingPricePolicy.BLOCK` (défaut) → `EstimateResult.blocking=True` et `freeze_version` lève
  `FreezeRefused("missing_prices", …)`. `WARN` → calcul autorisé, drapeau conservé. Règle lue dans
  `OrganizationSettings.missing_price_policy`.
- `LineKind` : `SECTION` sans prix ni total ; `ITEM` et `PROVISIONAL` dans le total de base ;
  `OPTION` et `VARIANT` chiffrés mais hors total, cumulés dans `options_total_ht`.
- `MissingPriceError` existe dans `errors.py` mais n'est pas levé par `compute_estimate` : l'absence de prix se signale par le résultat, pas par une exception.

## 9. Traçabilité obligatoire de toute sortie

- Chaque `ComponentResult` porte `formula` (chaîne reproduisant l'arithmétique) et `density_source`
  si une masse volumique a servi ; chaque `MarkupStepResult` porte `base_amount`, `rate`, `amount`,
  `running_total`, `formula`. Un chemin de calcul sans `formula` est un défaut bloquant.
- `freeze_version` stocke le snapshot `metreo.estimate.snapshot/1` — les **entrées** (lignes,
  markup, taxes, arrondi, politique) à côté du résultat — plus `snapshot_sha256`.
  `recompute_from_snapshot` doit redonner les mêmes chiffres à partir du seul snapshot, sans
  lecture des tables vivantes.
- `totals_for_display(..., include_internal=False)` retire `components`, `cost_by_kind`,
  `direct_cost`, `cost_price`, `markup_steps` ; qui peut les voir relève de **multitenant-security**.

## 10. Ajouter un nouveau type de composant

Six emplacements, dans cet ordre — le docstring de `composites.py` (« ici et nulle part ailleurs »)
est inexact :

1. `pricing.py` : `@dataclass(frozen=True, slots=True)` avec `label`, `kind` et
   `compute(self, boq_quantity: Quantity, currency: str) -> ComponentResult` ; toute division via
   `_ratio`, toute multiplication sous `localcontext`/`WORKING_PRECISION`, `formula` renseignée.
2. `packages/domain/src/metreo_domain/__init__.py` : import + entrée dans `__all__`.
3. `apps/api/src/metreo_api/services/composites.py` : nom ajouté à `COMPONENT_TYPES`, champs à
   `REQUIRED_FIELDS`, branche dans `spec_from_row` (valeurs sérialisées en `str`) et dans
   `component_from_spec`.
4. `apps/api/src/metreo_api/models.py` : colonnes `Amount`/`String` **nullables** sur
   `CompositeComponentRow` + extension du `CheckConstraint` `ck_composite_component_type`.
5. `apps/api/src/metreo_api/schemas.py` : élargir le `Literal` de `ComponentSpecIn`, ajouter les
   champs, puis le mapping explicite dans `create_composite`, fichier
   `apps/api/src/metreo_api/routers/pricebooks.py`.
6. Migration Alembic dans `apps/api/alembic/versions/` : `down_revision` = la tête courante,
   que donne `alembic -c apps/api/alembic.ini heads`. En `batch_alter_table`, pour rester
   compatible SQLite.

Tests exigés : formule + cas limite (diviseur nul, unité croisée) dans
`packages/domain/tests/test_pricing.py` ; aller-retour spec ↔ composant et gel dans
`apps/api/tests/test_estimating.py`. Le reste des critères relève de **definition-of-done**.

## 11. Tests

Suite du domaine : `packages/domain/tests` ; commandes complètes : **definition-of-done**.
Tout nouveau calcul se prouve par un test comparant à une valeur **calculée à la main** et écrite
en dur, pas au résultat du code. `sensitivity()` existe et est testé, mais c'est un utilitaire
**générique** : l'appelant fournit le `recompute` et nomme ses variations ; les axes du cahier des
charges (rendement, prix, distance, densité, aléa, marge) ne sont pas précâblés et rien n'est
exposé par l'API. Scénarios bas/probable/haut, indexation et comparaison prix interne / offre /
coût réel ne sont pas implémentés (phases ultérieures).

## Signaux d'alerte

- Un `float`, un `round()` ou un `%` appliqué à un montant, ou `Decimal(0.1)` au lieu de
  `Decimal("0.1")`.
- Un `quantize` au milieu de la chaîne, ou un total recalculé depuis des valeurs déjà arrondies
  (double arrondi, écarts de centimes sur un gros bordereau).
- Deux montants bruts comparés avec `==` : `750,0000000000000000000000002 != 750`.
- Une conversion m3 → t faite avec une masse volumique codée en dur, par défaut ou sans `source`.
- Un `except DomainError: pass`, ou une exception remplacée par un montant nul, un prix « 0 » ou
  une quantité devinée.
- `site_overheads_base=direct_plus_site` : la première étape n'a pas de prédécesseur et retombe
  silencieusement sur `direct_cost`. Ne configurer que `direct_cost` au 1ᵉʳ rang.
- Confondre `on_cost` et `on_price` : à 10 %, 13,10 EUR d'écart pour un prix de revient de 1 144,80 EUR.
- Une taxe ajoutée dans `selling_price_ht` au lieu de rester dans `tax_amounts`.
- Un poste sans prix compté comme 0, ou un gel accepté alors que `blocking=True`.
- Un `formula` vide, générique ou décorrélé du calcul réellement effectué.
- Un devis gelé qui bouge parce qu'un `PriceItem` ou un `OrganizationSettings` a changé : le recalcul
  doit partir du snapshot seul.
- Un composant ajouté à `composites.py` sans `CheckConstraint`, sans migration ou sans mapping dans
  `create_composite` : la ligne est acceptée par l'API puis perdue.
- Un import de `sqlalchemy`, `fastapi` ou `datetime.now()` dans `packages/domain/`.
