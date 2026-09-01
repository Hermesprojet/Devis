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
      ├─* Client                                 répertoire réutilisable
      ├─* Project
      │      ├─* BillOfQuantities ──* BoqItem     bordereau et postes
      │      └─* Estimate
      │             └─* EstimateVersion           brouillon → gelée (instantané)
      │                    └─0:1─ IssuedQuote     le devis REMIS : PDF figé + empreinte
      │                             ├─* QuoteEvent         journal append-only du cycle
      │                             ├─* QuoteShareLink     lien public, secret haché
      │                             └─* QuotePublicSession session courte du destinataire
      ├─* PriceBook
      │      └─* PriceBookVersion                 brouillon → publiée (immuable)
      │             ├─* PriceItem                 prix unitaires
      │             ├─* CompositePriceRow ──* CompositeComponentRow   sous-détails
      │             └─* ImportBatch ──* ImportBatchRow                zone de préparation
      ├─* QuoteRetentionDecision                  durée de conservation ET ce qui la fonde
      └─* AuditEvent                              journal chaîné, append-only

RegionProfile                                     global, non tenant : packs pays/région
OrganizationPurge                                 SANS clé étrangère : survit à l'organisation
```

Deux tables sortent du graphe, et pour deux raisons opposées. `RegionProfile`
est globale parce qu'un pack régional ne dépend d'aucune entreprise.
`OrganizationPurge` est détachée parce qu'elle doit **survivre** à
l'organisation qu'elle décrit : un registre de destruction rattaché à ce qu'il
enregistre disparaît avec lui et ne prouve rien.

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

### Client et devis remis

| Table | Ce qu'elle porte | Point à connaître |
| --- | --- | --- |
| `clients` | Fiche réutilisable : identité, adresse, contact | S'archive (`archived_at`), ne se supprime pas tant qu'un chantier la sert |
| `issued_quotes` | Le devis **remis** : numéro, dates, quatre instantanés, clé et empreinte du PDF | Immuable. Corriger un devis remis, c'est une nouvelle version puis une nouvelle émission — l'ancienne reste |

Un devis remis porte `organization_snapshot`, `client_snapshot`,
`project_snapshot` et `document_snapshot` : quatre copies distinctes de ce que
le document DIT, au moment où il a été produit. Modifier la fiche client
ensuite ne change ni le PDF ni son empreinte, et c'est vérifié dans le parcours
navigateur.

**Aucun parent ordinaire ne l'emporte.** Chantier, estimation, version gelée et
désormais organisation retiennent en `RESTRICT`. Reproduit sur base jetable
avant correction : quatre suppressions sur cinq faisaient disparaître le devis
sans un mot et laissaient son PDF orphelin sur le volume.

### Cycle commercial

`quote_events` est le journal du cycle : transmission, consultation,
acceptation, refus, correction. **Append-only tenu par la base** — un
déclencheur refuse `UPDATE` et `DELETE`. Une erreur de saisie interne se corrige
par un événement compensatoire (`corrects_event_id` + motif obligatoire), jamais
en réécrivant l'original.

L'état commercial — Émis, Transmis, Consulté, Accepté, Refusé, Expiré — **n'est
stocké nulle part**. C'est une fonction pure du journal et de la date du jour.
Deux conséquences voulues : « Expiré » est exact sans tâche planifiée, et aucun
état enregistré ne peut diverger de l'histoire qui le justifie.

| Table | Ce qu'elle porte | Point à connaître |
| --- | --- | --- |
| `quote_events` | Type, canal, acteur interne, auteur déclaré, commentaire borné, date effective et date d'enregistrement | Append-only en base, pas seulement en code |
| `quote_share_links` | **Empreinte** d'un secret de 256 bits, échéance, révocation | Le secret brut n'est rendu qu'une fois et n'est jamais stocké |
| `quote_public_sessions` | Session courte du destinataire, adossée à un lien | Ne porte aucun droit propre : le lien est relu à chaque requête, donc révoquer ferme immédiatement |

### Conservation et effacement

| Table | Ce qu'elle porte | Point à connaître |
| --- | --- | --- |
| `quote_retention_decisions` | Durée, juridiction, source + date de consultation, date d'effet, validateur | Versionnée : corriger ajoute une ligne, l'ancienne reste lisible. **Le dépôt n'en sème aucune** |
| `organization_purges` | Code de motif, référence opaque, fenêtre d'exécution, empreintes et chemins des PDF détruits | **Sans clé étrangère** — elle survit à l'organisation. Aucun texte libre n'y entre |

Trois règles tiennent l'ensemble, et chacune corrige une erreur mesurée :

1. **Une durée seule n'est pas une décision**, c'est une opinion sans auteur.
   Les cinq éléments tiennent ou aucun ne tient. Sans décision en vigueur, la
   destruction est refusée — et le refus conserve.
2. **Une demande n'est pas une autorisation.** `demander()` inscrit et n'ouvre
   rien ; une fenêtre d'exécution s'ouvre séparément et le déclencheur la
   compare à l'**horloge de la base** — une borne validée par l'appelant ne
   prouve rien, il suffirait de mentir sur l'heure.
3. **Aucune zone durable n'accepte un nom.** Le motif est un code pris dans une
   liste fermée ; la référence rejette blancs et ponctuation de phrase. Un
   registre censé prouver une destruction sans conserver ce qu'elle a effacé ne
   peut pas offrir une case où l'on écrit le nom d'un client.

Détail et alternatives écartées : `docs/adr/0006-conservation-et-effacement.md`.
Exécution : `scripts/purger_organisation.py` — aucune route HTTP.

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
- Transverse — `Risk`, `Assumption`, `OpenQuestion`, `Approval`,
  `ExportArtifact`, `Notification`

`Quote` et `QuoteVersion` figuraient ici ; ils n'y sont plus. Le devis remis
existe sous le nom `issued_quotes`, et son cycle commercial avec lui.

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
| `uq_issued_quote_number` | Le numéro d'un devis remis est unique DANS l'organisation, et c'est la base qui le tient |
| `uq_issued_quote_version` | Une version gelée ne produit qu'un seul devis remis |
| `uq_quote_share_link_secret`, `uq_quote_public_session_token` | Deux liens ne peuvent pas partager une empreinte |
| `ck_quote_event_kind`, `ck_quote_event_channel`, `ck_organization_purge_reason` | Énumérations tenues par la base — dont les motifs de purge, précisément pour qu'aucune phrase libre n'y entre |
| `ck_organization_purge_window` | Une autorisation n'existe pas sans sa borne, ni l'inverse |
| `trg_issued_quotes_conservation` | Rien ne détruit un devis remis sans autorisation d'exécution ouverte et non expirée |
| `trg_quote_events_append_only` (PostgreSQL) / `trg_quote_events_pas_de_modification` et `..._pas_de_suppression` (SQLite) | Rien ne réécrit le journal du cycle. Deux déclencheurs sous SQLite, qui ne sait pas conditionner un seul sur l'opération |
| `ix_projects_org_status`, `ix_price_items_org_family`, `ix_audit_org_occurred` | Filtres de liste, toujours préfixés par l'organisation |
