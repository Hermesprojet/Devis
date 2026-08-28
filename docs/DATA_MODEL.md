# Modèle de données

19 tables implémentées, créées par une migration Alembic unique
(`apps/api/alembic/versions/`). Le test `test_migrations_reproduce_the_models_exactly`
échoue si un modèle change sans migration.

## Conventions

| Convention | Application |
| --- | --- |
| Identifiants | UUID v4 en `String(36)`, générés par l'application |
| Multi-tenant | Toute table métier porte `organization_id` ; les contraintes d'unicité l'incluent quand une collision entre tenants est possible |
| Horodatage | `created_at` / `updated_at` en UTC, posés par l'application (comportement identique SQLite et PostgreSQL) |
| Suppression | Logique (`deleted_at`) pour tout ce qu'une estimation peut référencer ; le journal d'audit ne se supprime jamais |
| Montants | Type `Amount` — `NUMERIC(28,10)` sur PostgreSQL, texte exact sur SQLite. Jamais de flottant |
| JSON | Utilisé là où la forme dépend du pays ou du type (packs régionaux, instantanés, charges utiles d'audit) |

## Cartographie

```text
Organization ─1:1─ OrganizationSettings           règles de calcul de l'entreprise
      │
      ├─* TaxRateRow                              taux datés, hors prix HT
      ├─* Membership *─ User                      rôle par organisation
      ├─* Project
      │      ├─* BillOfQuantities ──* BoqItem     bordereau et postes
      │      └─* Estimate
      │             └─* EstimateVersion           brouillon → gelée (instantané)
      ├─* PriceBook
      │      └─* PriceBookVersion                 brouillon → publiée (immuable)
      │             ├─* PriceItem                 prix unitaires
      │             ├─* CompositePriceRow ──* CompositeComponentRow   sous-détails
      │             └─* ImportBatch ──* ImportBatchRow                zone de préparation
      └─* AuditEvent                              journal chaîné, append-only

RegionProfile                                     global, non tenant : packs pays/région
```

## Tables et raisons d'être

### Identité et organisation

| Table | Ce qu'elle porte | Point à connaître |
| --- | --- | --- |
| `organizations` | Entreprise cliente, pays, région, langue, devise | Racine de l'isolation |
| `organization_settings` | Arrondis, chaîne de marges, politique « poste sans prix » | Copiée dans chaque version gelée : la modifier ne réécrit aucun passé |
| `tax_rates` | Code, libellé, taux, dates d'application | Les taxes restent **hors** du prix HT |
| `users` | Compte, e-mail unique global | Un utilisateur peut appartenir à plusieurs organisations |
| `memberships` | Lien utilisateur ↔ organisation ↔ rôle | Relu à chaque requête : révoquer prend effet immédiatement, pas à l'expiration du jeton |

### Packs pays/région

`region_profiles` est **globale** : un pack décrit des règles publiques, une
organisation ne fait que le sélectionner. `terminology`, `rules` et `sources`
sont du JSON parce que ce qu'un pays doit déclarer diffère d'un pays à l'autre —
ajouter la France ne doit pas demander une migration.

Quatre packs sont semés : `BE-WAL`, `BE-VLG`, `BE-BRU` en `draft`, `FR` en
`planned`. Tous portent un `disclaimer` : **aucune règle n'est validée
juridiquement**.

### Bibliothèque de prix

| Table | Rôle |
| --- | --- |
| `price_books` | Une bibliothèque par entreprise (interne, fournisseur, marché-cadre…) |
| `price_book_versions` | `draft` modifiable, `published` immuable. Une estimation gelée référence une version précise |
| `price_items` | Code, libellé, unité, prix, fournisseur, zone, validité, source, confiance, `is_demo_data` |
| `composite_prices` | Sous-détail (sous-détail de prix) réutilisable |
| `composite_components` | Une table large à colonnes optionnelles, discriminée par `component_type` (`consumption`, `output_rate`, `rotation`, `lump_sum`) et bornée par une contrainte `CHECK`. Un seul `SELECT` suffit à lire un sous-détail complet |
| `import_batches` / `import_batch_rows` | Zone de préparation d'un import : lignes brutes, lignes normalisées, erreurs, doublons. **Rien n'atteint `price_items` avant confirmation** |

`is_demo_data` n'est pas cosmétique : l'aperçu de devis imprime un avertissement
dès qu'une version de bibliothèque contient une ligne fictive.

### Métré

`boq_items` porte `position` (unique dans un bordereau), `designation`,
`unit_code`, `quantity`, `kind` et `status`, plus `formula` (le métré lisible,
`290 m × 5,00 m = 1450 m²`) et `client_quantity` (la quantité du bordereau
client, à côté de la quantité interne, pour objectiver l'écart).

`kind` ∈ `section | item | option | variant | provisional`. Seuls `item` et
`provisional` entrent dans le total de base ; options et variantes sont chiffrées
et totalisées séparément.

`status` ∈ `proposed | verified | approved | rejected`. Modifier la quantité
d'une ligne `approved` exige `override_approved` **et** un motif, et repasse la
ligne en `verified`.

Une ligne peut pointer vers `price_item_id` (prix de bibliothèque) ou
`composite_price_id` (sous-détail). Le sous-détail gagne s'il est présent.

### Estimation

`estimate_versions` est le cœur de la traçabilité :

| Colonne | Rôle |
| --- | --- |
| `price_book_version_id` | La bibliothèque employée, figée |
| `markup`, `taxes`, `rounding`, `missing_price_policy` | Copies des règles au moment du gel |
| `snapshot` | Les **entrées** (lignes, quantités, prix résolus, composants) et le **résultat** |
| `snapshot_sha256` | Empreinte du contenu de l'instantané |
| `total_selling_price_ht`, `total_ttc` | Totaux **non arrondis** ; l'API expose aussi `..._display` arrondi selon la politique de la version |
| `frozen_at`, `frozen_by` | Qui a gelé, quand |

Une version gelée se relit depuis son instantané. Modifier un prix de référence
après coup ne la touche pas — c'est vérifié par
`test_a_later_price_change_does_not_move_a_frozen_total`.

### Audit

`audit_events` est append-only, numérotée par organisation
(`uq_audit_org_sequence`) et chaînée : `hash = SHA-256(contenu + previous_hash)`.
`GET /api/v1/audit/verify` rejoue toute la chaîne et signale la première
incohérence — un trou de séquence comme une modification de contenu.

C'est de la **détection** d'altération, pas une impossibilité d'altérer : un
administrateur de base reste capable de réécrire toute la chaîne. Le stockage
en écriture unique est un chantier de phase 5.

## Tables prévues, non implémentées

Le cahier des charges en liste davantage. Elles arriveront avec leur phase, pas
avant :

- Phase 2 — `Document`, `DocumentRevision`, `DocumentPage`, `ProcessingJob`,
  `ExtractionProposal`, `SourceCitation`, `ValidationDecision`
- Phase 3 — `PlanSheet`, `PlanObject`, `QuantityMeasurement`, `MeasurementFormula`
- Phase 4 — `Supplier`, `SupplierContact`, `SupplierQualification`, `ServiceArea`,
  `RFQ`, `RFQPackage`, `RFQRecipient`, `RFQMessage`, `SupplierOffer`,
  `SupplierOfferLine`, `Connector`, `ConnectorCredentialReference`, `ConnectorRun`
- Transverse — `Risk`, `Assumption`, `OpenQuestion`, `Quote`, `QuoteVersion`,
  `Approval`, `ExportArtifact`, `Notification`

Deux exigences structurantes les concernent déjà :

1. **Une citation est un objet de première classe**, pas du texte libre : fichier,
   révision, page, plage de caractères, boîte englobante normalisée, et pour un
   plan feuille/calque/objet.
2. **L'IA écrit dans `ExtractionProposal`, jamais dans une table approuvée.** Le
   passage de l'une à l'autre est une `ValidationDecision` humaine.

## Index et contraintes notables

| Contrainte | Raison |
| --- | --- |
| `uq_project_org_reference` | Deux entreprises peuvent utiliser la référence « 2026-014 » |
| `uq_priceitem_version_code` | Un code est unique dans une version de bibliothèque, pas au-delà |
| `uq_boqitem_boq_position` | Un poste par position dans un bordereau |
| `uq_estimateversion_number` | Numérotation continue par estimation |
| `uq_audit_org_sequence` | Interdit deux événements de même rang, donc une insertion « entre » deux maillons |
| `ck_boq_item_kind`, `ck_boq_item_status`, `ck_composite_component_type`, `ck_estimate_version_status` | Les énumérations sont tenues par la base, pas seulement par l'application |
| `ix_projects_org_status`, `ix_price_items_org_family`, `ix_audit_org_occurred` | Filtres de liste, toujours préfixés par l'organisation |
