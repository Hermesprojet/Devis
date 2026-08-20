# ADR 0004 — Moteur de calcul des prix

- **Statut** : accepté
- **Date** : 2026-08-20

## Contexte

C'est la décision la plus structurante du produit. Un devis engage l'entreprise
sur un montant ; un chiffre que personne ne peut refaire à la main ne se défend
pas en réunion d'attribution.

## Décisions

### 1. `Decimal`, jamais de flottant binaire

Toute la monnaie passe par `decimal.Decimal` avec une précision de travail de 28
chiffres. `to_decimal()` convertit un `float` via son `repr`, ce qui conserve le
littéral décimal saisi plutôt que l'artefact binaire. Les décimales françaises
(`1 234,56`) sont acceptées à l'entrée.

En base, le type `Amount` donne un `NUMERIC(28,10)` sur PostgreSQL et une chaîne
exacte sur SQLite : SQLite n'a pas de type décimal et convertirait en flottant.
Aucune arithmétique monétaire ne se fait en SQL, donc rien n'est perdu.

### 2. Stockage non arrondi, arrondi explicite et unique

Les montants sont stockés **non arrondis**. L'arrondi est une étape de
présentation appliquée par une `RoundingPolicy` (échelle, mode, échelle
distincte pour les prix unitaires — plusieurs maîtres d'ouvrage exigent 2
décimales sur le prix unitaire et en acceptent 4 sur un sous-détail).

L'API expose les deux : `total_selling_price_ht` (brut) et
`total_selling_price_ht_display` (arrondi selon la politique de **cette**
version). Sinon chaque client refait l'arrondi à sa façon et les totaux
divergent.

### 3. Toute quantité porte une unité, toute conversion est explicite

Un nombre nu n'est pas une quantité. `convert()` traite les conversions dans une
dimension par ratio de facteurs déclarés, et **refuse** le reste :

- volume ↔ masse sans `Density` **sourcée** → `AmbiguousConversionError` ;
- dimensions sans passerelle (m² vers heures) → `IncompatibleUnitsError`.

`Density` exige un champ `source` non vide. C'est le point où un métré se
trompe le plus silencieusement : 620 m³ de déblai facturés à la tonne dépendent
entièrement d'une masse volumique, et cette valeur vient d'un rapport de sol qui
doit être cité.

La même règle s'applique à un prix de bibliothèque : si son unité diffère de
celle du poste, la quantité est convertie (conversion tracée dans la formule) ou
la ligne est refusée. Un prix en €/h appliqué à un poste en m² est une erreur,
pas une approximation.

### 4. Quatre types de composants, une formule lisible chacun

| Type | Formule | Usage |
| --- | --- | --- |
| `ConsumptionComponent` | `quantité × consommation × (1 + perte) × prix unitaire` | Matériaux, traitement à la tonne |
| `OutputRateComponent` | `quantité ÷ rendement × effectif × coût horaire` | Main-d'œuvre, engins |
| `RotationComponent` | `⌈quantité ÷ charge utile⌉ × (coût rotation + km × tarif)` | Transport |
| `LumpSumComponent` | montant fixe | Installation de chantier, sous-traitance forfaitaire |

Un rendement nul ou négatif lève `InvalidRateError` au lieu de produire une durée
infinie. Les rotations sont arrondies **au supérieur** par défaut : un
demi-camion ne quitte pas le chantier.

Chaque composant retourne sa `formula` en texte :
`120 m3 ÷ 28 m3/h = 4,2857 h × 1 = 4,2857 h × 92,00 EUR/h = 394,28 EUR`.

### 5. Une chaîne de marge ordonnée et explicite

```text
déboursé sec
  + frais de chantier      (base configurable)
  + frais généraux         (base configurable)
  = prix de revient
  + aléas                  (base configurable)
  + marge                  (sur coût ou sur prix de vente)
  = prix de vente HT
  + taxes                  (calculées à part, jamais fondues dans le HT)
```

`OverheadBase` dit sur quoi chaque taux s'applique (`direct_cost`,
`direct_plus_site`, `running_total`) : l'ordre est une donnée, pas une
convention implicite du code.

`MarginMethod` distingue deux pratiques que 10 % ne rend pas équivalentes :

- `on_cost` — `vente = base × (1 + taux)` : 10 % sur 100 donne 110 ;
- `on_price` — `vente = base ÷ (1 − taux)` : 10 % sur 100 donne 111,11, et la
  marge représente réellement 10 % du chiffre d'affaires.

Les étapes à taux nul sont **quand même retournées**, pour que le lecteur voie
qu'elles ont été considérées.

### 6. Un poste sans prix n'est jamais valorisé à zéro

`MissingPricePolicy` vaut `block` (le gel est refusé) ou `warn` (le gel passe,
le poste reste signalé). Dans les deux cas la ligne est listée dans
`missing_price_line_ids`, affichée avec un badge, et exportée avec la mention
« PRIX MANQUANT ».

### 7. Marges appliquées par ligne

Un bordereau de prix unitaires expose un prix unitaire par poste ; le client doit
pouvoir vérifier `quantité × P.U. = total`. Les marges sont donc appliquées
ligne par ligne puis sommées. `markup_override` permet un taux différent sur une
ligne (typiquement un lot sous-traité).

### 8. Une version gelée se relit, elle ne se recalcule pas

Le gel écrit un instantané contenant les **entrées** (lignes, quantités, prix
résolus, composants sérialisés) et le **résultat**, plus un SHA-256 du tout.
`recompute_from_snapshot` rejoue le moteur sur les seules entrées : le total
stocké est donc vérifiable sans être cru sur parole. Aucune lecture des tables de
prix n'a lieu pour une version gelée.

## Conséquences

- Ajouter un type de composant se fait à trois endroits et nulle part ailleurs :
  la dataclass du domaine, la spec sérialisable de `services/composites.py`, et
  la table `composite_components` avec sa migration.
- La sérialisation des composants est un **format de compatibilité** : un
  instantané gelé il y a deux ans doit encore se relire. Une évolution
  incompatible impose une version de schéma d'instantané.
- 61 tests de domaine couvrent ces règles, dont le scénario « excavation 120 m³ »
  du cahier des charges, recalculé à la main dans le test.
