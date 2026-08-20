---
name: cad-bim-takeoff
description: À utiliser dès qu'un plan ou un fichier CAO/BIM entre dans Metreo ou qu'un métré assisté est évoqué — visionneuse et annotation, échelle déclarée ou calibrée, feuilles, calques, cotes, polylignes, blocs, textes, IFC, DXF, DWG, GeoJSON, PostGIS, worker de conversion, bibliothèque de lecture CAO et sa licence, extraction de longueurs/surfaces/volumes depuis un plan, mesure manuelle traçable, rapprochement plan/bordereau, écart entre quantité mesurée et quantité client, contrôle d'unité ou d'ordre de grandeur avant reprise d'une quantité — ou dès qu'on envisage de créer une table de mesures, d'écrire dans BoqItem une quantité issue d'un plan, de toucher une quantité déjà approuvée, d'annoncer un support DWG, ou d'ajouter une dépendance de lecture ou de conversion CAO.
---

# Métrés assistés, plans et CAO/BIM — phase 3, non implémenté

Section 6.5 du cahier des charges maître. **Aucune ligne de ce sous-système n'existe dans le
dépôt** : ce fichier est un cahier des charges, pas une documentation d'API. Chemins abrégés
ci-dessous : `models.py`, `config.py`, `schemas.py`, `routers/`, `services/`, `security/` vivent
sous `apps/api/src/metreo_api/`.

## 1. État réel — le dire avant de coder

| Sujet | État | Ancrage |
| --- | --- | --- |
| Lecture de plan, viewer, mesure, conversion CAO | **inexistant** | aucun fichier ; `apps/worker/`, `packages/contracts/`, `packages/config/` sont des dossiers vides |
| PostGIS | disponible, inutilisé | image `postgis/postgis:16-3.4` (`infra/docker-compose.yml`) ; aucune colonne géométrique dans `models.py` |
| Cible d'atterrissage d'une quantité | implémenté | `BoqItem` (`unit_code`, `quantity`, `formula`, `client_quantity`, `status`) dans `models.py` |
| Statuts de validation | implémenté | `proposed` / `verified` / `approved` / `rejected`, contrainte `ck_boq_item_status` |
| Verrou sur quantité approuvée | implémenté | `update_item` dans `routers/boq.py` |
| Unités et conversions | implémenté | `packages/domain/src/metreo_domain/units.py`, inventaire exposé par `GET /units` (`routers/meta.py`, `known_units()`) |
| Patron « aperçu puis validation » | implémenté pour l'import CSV de prix | `ImportBatch.sha256`, `create_preview`, `commit_batch`, `batch_report` (`services/price_import.py`) |
| Stockage de fichiers | configuration seule | `storage_root`, `max_upload_bytes` (`config.py`) ; aucun code d'upload |
| IA / conversion externe | débranché | `ai_enabled=False`, `ai_provider="null"` (`config.py`) |

Phrase à tenir telle quelle face à un utilisateur ou un PO : *aucune lecture de plan n'est
implémentée aujourd'hui*. Une maquette d'écran ou un schéma de table n'est pas une capacité
livrée. État global des phases : **btp-product-rules**.

## 2. Les cinq niveaux de maturité — jamais l'un sans le précédent

| Niveau | Contenu | Condition d'entrée | Interdit tant qu'il n'est pas atteint |
| --- | --- | --- | --- |
| 1 — visualisation | affichage du fichier, unités du document, échelle déclarée, métadonnées, liste des feuilles, annotations et mesures **manuelles** | stockage du fichier + SHA-256 + visionneuse | parler de mesure « automatique » |
| 2 — détection assistée | cotes, surfaces, longueurs, polylignes, blocs, textes, calques proposés à l'écran | niveau 1 + chaque objet détecté créé en `proposed` | écrire une quantité sans validation humaine |
| 3 — extraction structurée | parcours d'entités IFC et DXF, quantités par objet, type et calque | niveau 2 + mapping calque/type → poste, versionné et éditable | prétendre couvrir « tout » un IFC |
| 4 — DWG | conversion et extraction par un fournisseur dont la licence l'autorise | niveau 3 + licence vérifiée et consignée dans un ADR de `docs/adr/` | écrire « DWG » dans une UI, une doc ou un contrat |
| 5 — rapprochement | comparaison plan ↔ bordereau, calcul et présentation des écarts | niveau 3 (4 si DWG) + toutes les métadonnées du §4 | corriger une quantité automatiquement |

- Le niveau annoncé est le **plus bas niveau entièrement testé**, pas le plus haut niveau démontré.
- Toute fonction de niveau supérieur laissée accessible est marquée « prototype » dans l'interface
  **et** dans la documentation, sinon elle est désactivée.
- Chaque niveau se livre en tranche verticale complète (domaine → migration → API → UI → tests),
  jamais en couche horizontale inutilisable.

## 3. DWG, bibliothèques et licences

- Le DWG est un format propriétaire. **Ne jamais promettre une prise en charge fidèle** tant que le
  moteur retenu n'est pas installé, testé sur un corpus réel et sa licence lue. « Ouverture
  partielle » se dit explicitement : entités supportées, entités ignorées.
- Avant d'ajouter une dépendance de lecture ou de conversion (lecteur DXF, lecteur IFC,
  convertisseur DWG) : relever nom, version, licence exacte, restrictions d'usage commercial et
  coût **à la date d'implémentation**, puis consigner la décision dans un ADR daté de `docs/adr/`
  (0001 à 0004 existent ; le prochain numéro est 0005). Une licence copyleft forte ou
  « non commercial » sur un composant serveur est un refus, pas un détail.
- Un convertisseur en ligne = envoi d'un document client vers un tiers : soumis au consentement,
  désactivable, journalisé. `ai_enabled=False` par défaut couvre aussi ces conversions
  (**multitenant-security** pour l'envoi de fichiers hors du tenant).
- Dégradation attendue en l'absence de moteur autorisé : demander un export DXF, IFC ou PDF. Ne pas
  tenter une lecture approximative silencieuse, ne pas écrire de lecteur DWG « maison » à partir de
  spécifications rétro-ingénierées.

## 4. Métadonnées obligatoires de toute quantité issue d'un plan

Onze champs, tous requis. Une mesure incomplète n'est pas reprenable : elle reste `proposed`.

| Donnée | Identifiant proposé | Règle |
| --- | --- | --- |
| Fichier | `source_file_id` | référence au fichier stocké, jamais un nom de fichier libre |
| Version | `source_revision` + `sha256` | empreinte du binaire, sur le modèle de `ImportBatch.sha256` |
| Feuille | `sheet` | feuille, vue ou étage identifié |
| Calque ou objet | `layer`, `object_ref` | calque DXF, `GlobalId` IFC, handle d'entité |
| Unité source | `source_unit_code` | unité **du document**, avant toute conversion, résolue par `get_unit()` |
| Échelle | `scale`, `scale_origin` | ex. `1:50` + origine : `declared`, `read`, `calibrated` |
| Formule | `formula` | chaîne lisible reproduisant l'arithmétique ; atterrit dans `BoqItem.formula` |
| Géométrie de mesure | `geometry` | polyligne/polygone/points en coordonnées du document (GeoJSON), pas une capture d'écran |
| Auteur ou moteur | `produced_by` | `user:<id>` ou `engine:<nom>@<version>` — jamais vide |
| Confiance | `confidence` | qualitative, sur le modèle de `PriceItem.confidence` (défaut `declared`) |
| Statut | `status` | mêmes valeurs que `BoqItem.status` |

- Ces champs **n'existent pas** en base : les créer via une migration Alembic
  (`apps/api/alembic/versions/`, `down_revision` = tête courante, `d88792b38c2d` aujourd'hui),
  jamais à la main.
- Sérialiser le lot sous un identifiant de schéma versionné (ex. `metreo.plan.measurement/1`), sur
  le modèle de `SNAPSHOT_SCHEMA = "metreo.estimate.snapshot/1"` (`services/estimating.py`).
- La citation d'un plan est un objet de première classe, pas du texte libre : structure et
  champs `sheet` / `layer` / `object_id` définis par **document-analysis**.

## 5. Types de mesure supportés

| Type (6.5) | `Dimension` (`units.py`) | Codes d'unité disponibles |
| --- | --- | --- |
| nombre | `COUNT` | `pce` |
| longueur | `LENGTH` | `m`, `cm`, `mm`, `km` |
| surface | `AREA` | `m2`, `cm2`, `ha` |
| volume | `VOLUME` | `m3`, `l` |
| masse | `MASS` | `kg`, `t` |
| durée | `TIME` | `h`, `min`, `d` |
| forfait | `LUMP_SUM` | `fft` |
| trajet | **aucune** — n'est pas une dimension | paramètre de prix : `RotationComponent` (`payload`, `distance_km`, `rate_per_km`) |

- Un plan ne produit jamais un « trajet » : il produit une distance (`LENGTH`) ou un volume à
  évacuer (`VOLUME`). La rotation se calcule côté prix — **price-engine**.
- Aucune unité locale « plan » : toute unité passe par `get_unit()` (alias `ml`→`m`, `m²`→`m2`,
  `tonne`→`t`, `j`→`d`, `ff`→`fft`). Unité inconnue ⇒ `UnknownUnitError`, rendu en 422 avec
  `code: unknown_unit` par `_canonical_unit` (`routers/boq.py`).
- Conversions uniquement par `convert()`. **Volume → masse refusé** sans `Density` (valeur
  strictement positive et `source` non vide) ⇒ `AmbiguousConversionError` ; dimensions non
  pontables ⇒ `IncompatibleUnitsError`. Pas de densité codée en dur dans le code plan. Détails et
  formules : **price-engine**.

## 6. Contrôles unités et échelles avant toute reprise de quantité

Bloquants, dans cet ordre :

1. **Unité du document** lue et affichée telle quelle (un DXF en mm reste en mm), jamais supposée.
2. **Échelle explicite** : déclarée, lue dans le fichier ou calibrée sur une cote connue ; l'origine
   est stockée avec la mesure. Sans échelle sur un plan raster ou PDF : refuser la mesure, ne jamais
   retomber sur un 1:100 implicite.
3. **Recalage** : recalculer au moins une cote connue du plan ; au-delà d'un écart relatif
   configurable, la mesure reste `proposed` et l'écart est affiché.
4. **Contrôle de dimension** : la `Dimension` de la mesure doit être identique à celle de
   `BoqItem.unit_code` de la ligne visée ; sinon refus, sans conversion de secours.
5. **Ordre de grandeur** : rapport ≥ 1000 ou ≤ 1/1000 avec `BoqItem.client_quantity` ⇒ alerte
   « échelle probablement fausse » (facteur mm/m), présentée à l'utilisateur — jamais corrigée seule.
6. **Fichier remplacé** (nouveau `sha256`) : toutes les mesures qui en descendent repassent en
   revalidation ; aucun recalcul silencieux.

## 7. Rapprochement plan ↔ bordereau (niveau 5)

- La sortie est un **écart**, pas une correction : `boq_item_id`, `plan_quantity`, `boq_quantity`,
  `unit_code` canonique commun, `delta`, `delta_pct`, sources des deux valeurs — plus une décision
  humaine à prendre (accepter la mesure, garder le bordereau, ouvrir une question).
- Comparer à unité canonique identique, jamais deux valeurs obtenues par des chemins de conversion
  différents.
- `BoqItem.client_quantity` (quantité du client) et `BoqItem.quantity` (quantité interne) coexistent :
  ne jamais écraser l'une par l'autre — cf. `test_client_quantity_can_be_recorded_next_to_the_internal_one`
  dans `apps/api/tests/test_boq.py`.
- Une quantité `approved` ne bouge jamais toute seule. Le seul chemin autorisé est celui déjà
  implémenté par `update_item` (`routers/boq.py`). Le verrou ne se déclenche que si la charge
  utile touche `quantity` ou `unit_code` (`touches_quantity`) sur une ligne `approved` — une
  note ou une désignation passe sans dérogation :

  ```
  PATCH /boq-items/{id}  quantité, sans override                      → 409 approved_quantity_locked
  PATCH /boq-items/{id}  quantité, override_approved, sans motif      → 422 override_reason_required
  PATCH /boq-items/{id}  quantité, override_approved + override_reason → statut ramené à
                         "verified", événement "boq_item.updated" journalisé par audit.record
  ```

  Un job de rapprochement n'a pas le droit d'emprunter ce chemin sans acteur humain identifié.
- Permissions : écriture `Permission.BOQ_WRITE`, approbation `Permission.BOQ_APPROVE`
  (`security/roles.py`). Un moteur d'extraction n'est pas un rôle et n'approuve rien.

## 8. Si tu implémentes une tranche

- Ordre imposé : stockage + empreinte → visionneuse + mesure manuelle → détection assistée →
  IFC/DXF → (DWG) → rapprochement.
- Reprendre le patron `preview` → `commit` éprouvé sur le CSV (`services/price_import.py`,
  `ImportBatchRow.raw` / `normalized` / `errors` / `is_valid`) : rien n'atteint une table métier
  avant revue humaine.
- Le parsing et la géométrie ne vont **pas** dans `packages/domain/` (pur, sans I/O) : un service
  sous `services/` plus un worker sous `apps/worker/` (vide aujourd'hui). Les montants restent
  dans le domaine.
- `max_upload_bytes` vaut 25 Mio par défaut (`config.py`) : un IFC dépasse couramment ce seuil —
  décider, configurer, documenter, et parser hors de la requête HTTP.
- Fixtures anonymisées à ajouter à côté de `fixtures/imports/`, dont un fichier volontairement
  fautif (échelle absente, unité inconnue, calque vide), sur le modèle de
  `prix_5_valides_2_erreurs.csv`.
- Toute nouvelle table porte `organization_id` et se lit via `owned_query` / `get_owned`
  (`services/tenant.py`) : voir **multitenant-security**. Critères de sortie : **definition-of-done**.

## 9. Renvois

- **btp-product-rules** — constitution produit, formats ouverts, human in the loop, traçabilité, phases.
- **price-engine** — `units.py`, `convert()`, densité sourcée, rotations, calcul des montants.
- **document-analysis** — PDF, OCR, extraction textuelle et citations (phase 2).
- **multitenant-security** — isolation par organisation, rôles, audit, envoi de fichiers à un tiers.
- **definition-of-done** — tests, migrations, revue, critères de « terminé ».

## Signaux d'alerte

- Une UI, une doc, une réponse ou un devis qui laisse croire que Metreo lit déjà des plans.
- « Support DWG » écrit avant qu'un convertisseur licencié soit installé et testé, ou une
  dépendance CAO ajoutée sans licence relevée dans `docs/adr/`.
- Un fichier client envoyé à un convertisseur externe sans consentement, sans possibilité de
  désactivation et sans journalisation.
- Une quantité issue d'un plan écrite dans `BoqItem` sans fichier, version, feuille, calque, unité
  source, échelle, formule, géométrie, auteur, confiance et statut.
- Une mesure prise sur un plan raster ou PDF sans échelle, ou avec une échelle « supposée ».
- Une unité inventée côté plan au lieu de `get_unit()`, ou une valeur numérique nue traitée comme
  une quantité.
- Un m3 converti en tonnes avec une masse volumique par défaut ou codée en dur, sans `Density`
  sourcée : `AmbiguousConversionError` est la bonne réponse.
- Un écart plan/bordereau appliqué automatiquement, ou une quantité `approved` modifiée sans
  `override_reason` et sans acteur humain.
- `BoqItem.client_quantity` écrasée par la mesure interne, ou l'inverse.
- Un `confidence` élevé affiché comme une certitude, ou une mesure automatique livrée sans statut
  `proposed`.
- Un niveau 3 ou 4 branché alors que les contrôles d'unité et d'échelle du niveau 1 manquent.
- Une nouvelle table de plans ou de mesures sans `organization_id`, ou créée sans migration Alembic.
