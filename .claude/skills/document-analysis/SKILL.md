---
name: document-analysis
description: À utiliser pour tout ce qui concerne le TEXTE d'un document de marché dans Metreo (phase 2, socle partiel) — upload de PDF natif ou scanné, DOCX, XLSX, TXT, ZIP, photo, antivirus et SHA-256, détection de type/langue/qualité, OCR page à page, extraction de tableaux, segmentation avec pages et coordonnées, classification du document (CCTP, cahier spécial des charges, clauses administratives, métré, DQE, BPU, étude géotechnique, rapport de pollution, inventaire amiante, planning, addendum), extraction structurée par schéma JSON, citation page-zone, seuil de confiance, file de validation humaine, recherche plein texte ou sémantique, embeddings, RAG, prompt versionné, adaptateur OCR ou LLM, injection de prompt via un fichier importé, ou dès qu'une réponse risque d'écrire en base une donnée extraite sans citation, sans confiance et sans validation humaine. Un plan est classé ici, mais sa géométrie et ses mesures relèvent de cad-bim-takeoff ; le contenu réglementaire d'une clause relève de belgium-regulatory-pack.
---

# Metreo — pipeline documentaire (phase 2, socle partiel)

**Seul le socle Phase 2A existe.** Six tables documentaires, leurs contraintes et leur
migration sont présentes ; `packages/contracts/` expose les sept ports purs.
`services/documents.py` gère les métadonnées, décisions humaines, numéros de révision et
états idempotents, toujours par organisation. Il n'existe toujours aucun upload, stockage
binaire, antivirus, OCR, LLM, embedding, recherche, worker ou écran documentaire.
L'ADR 0003 reste la conception de référence. Les étapes 1 à 11 ci-dessous demeurent un
cahier des charges : la présence du schéma ou d'un port n'autorise jamais à répondre que
le traitement correspondant « est déjà géré ».

Chemins abrégés ci-dessous : `models.py`, `config.py`, `db.py`, `logging_config.py`,
`schemas.py`, `routers/`, `services/`, `security/` vivent sous `apps/api/src/metreo_api/`.

## 1. Ce sur quoi s'appuyer (existant, vérifié)

| Existant | À réutiliser comme |
| --- | --- |
| `ImportBatch` / `ImportBatchRow` (`models.py`) | patron de référence : `sha256`, `byte_size`, `status`, `row_count/valid_count/error_count`, `errors` par ligne |
| `services/price_import.py` : `create_preview`, `commit_batch`, `RowError`, `batch_report` | patron « prévisualiser puis confirmer » — aucune écriture avant décision humaine |
| `BoqItem.status` ∈ `proposed/verified/approved/rejected` (`ck_boq_item_status`) et `POST /boq-items/{item_id}/approve` | destination finale d'une quantité extraite |
| `PriceItem.source`, `PriceItem.confidence` | précédent de « toute donnée porte sa source et sa confiance » |
| `services/audit.py` : `record()` (chaîne `previous_hash`/`hash`/`sequence`), `verify_chain()` | journalisation de chaque décision de validation |
| `logging_config.py` : `JsonFormatter`, `request_id_var` | corréler upload → job → extraction sans logger de contenu |
| `services/tenant.py` : `owned_query`, `find_owned`, `get_owned` | portée par organisation de **toute** requête documentaire |
| `Settings.ai_enabled`, `Settings.ai_provider` (`config.py`), champ `ai_enabled` de `GET /health` | interrupteur déjà exposé — le respecter, pas le contourner |
| `Settings.storage_root`, `Settings.max_upload_bytes` = 25 Mio (`config.py`) | racine de stockage et limite de taille |
| `docs/adr/0003-document-storage.md` (accepté) | frontières et noms **déjà décidés** : `Document` / `DocumentRevision`, `SourceCitation`, `ExtractionProposal`, `ValidationDecision`, interface `ObjectStore`, original immuable, accès par URL signée courte |
| service `redis` de `infra/docker-compose.yml` | file d'attente des jobs (aucun worker ne la consomme encore) |
| `packages/domain/src/metreo_domain/errors.py` : `DomainError` avec `code` stable | erreurs d'extraction typées |

## 2. Les 11 étapes du pipeline

Asynchrone, observable, relançable étape par étape. Chaque étape écrit son état, sa durée
et son erreur ; un échec en étape *n* n'efface jamais le résultat des étapes 1..*n-1*.

| # | Étape | Règle bloquante |
| --- | --- | --- |
| 1 | Réception et sécurisation | antivirus + quarantaine, type **réel** (signature, pas l'extension), taille ≤ `max_upload_bytes`, SHA-256, original stocké immuable, ZIP décompressé sous contrôle (profondeur, ratio, chemins) |
| 2 | Détection type / langue / qualité | langue par page (fr-BE, nl-BE, en), score de qualité, indicateur « scanné » ; une qualité basse dégrade la confiance en aval, elle ne bloque pas l'ingestion |
| 3 | Extraction native du texte | tentée d'abord ; conserve pages et positions ; ne jamais OCRiser un PDF déjà textuel |
| 4 | OCR page à page | seulement si l'étape 3 rend trop peu de texte ; résultat par page, reprise possible sur une seule page |
| 5 | Extraction de tableaux | tableau = lignes/colonnes/cellules avec coordonnées, pas du texte aplati ; cellules fusionnées et tableaux à cheval sur deux pages explicitement marqués |
| 6 | Segmentation | segments porteurs de `page`, plage de caractères, boîte englobante, ordre de lecture, titre de section parent |
| 7 | Classification | une catégorie + confiance + segments justificatifs ; `unknown` est une réponse valide |
| 8 | Extraction structurée | sortie LLM validée contre un **JSON Schema** strict ; sortie non conforme = échec de l'étape, jamais une écriture partielle |
| 9 | Indexation | plein texte **et** vecteurs, tous deux portés par `organization_id` |
| 10 | Contrôles de cohérence | unités, totaux, doublons, écarts entre révisions, quantité absente du CCTP |
| 11 | Mise en file de validation humaine | seule sortie autorisée du pipeline ; rien n'est approuvé automatiquement |

Relance : idempotente par `(document_sha256, pipeline_version, step)`. Une relance ne
détruit pas une extraction déjà corrigée par un humain — elle crée une nouvelle proposition
et affiche l'écart.

## 3. Catégories minimales de documents

`cctp_special_specification` (CCTP / cahier spécial des charges), `administrative_clauses`,
`boq` (métré, DQE, BPU, bordereau, quantitatif), `plan_architecture`, `plan_structure`,
`plan_roads_sewerage_utilities` (voirie, égouttage, impétrants), `geotechnical_study` (étude
de sol, rapport géologique), `pollution_report`, `asbestos_inventory` (amiante avant
démolition), `photo`, `schedule` (planning), `addendum_qa` (addendum, Q/R), `unknown`.

Un document peut relever de plusieurs catégories : stocker une liste ordonnée par
confiance, jamais une seule valeur devinée. Les plans s'arrêtent ici : leur exploitation
géométrique relève de **cad-bim-takeoff** (phase 3).

## 4. Extractions attendues — toujours avec source et confiance

Aucun champ extrait sans `citation` et `confidence`. Liste minimale : parties, dates,
références et hiérarchie des documents ; exigences techniques et administratives ; normes
citées (citer ≠ conclure à la conformité) ; délais, pénalités, garanties, assurances,
conditions de paiement ; matériaux, performances, épaisseurs, classes, diamètres,
tolérances ; postes, codes, désignations, unités et quantités **présentes dans le
document** ; contraintes de phasage, accès, bruit, poussière, circulation, sécurité ;
nature du sol, portance, niveau d'eau, pollution et incertitudes ; contraintes
d'évacuation, réemploi, traitement, traçabilité ; incohérences entre documents ou
révisions ; exclusions, réserves et questions à poser au donneur d'ordre.

Sur sol, pollution et amiante : afficher que l'extraction ne remplace pas l'avis du bureau
d'étude compétent. Les seuils et obligations belges relèvent de **belgium-regulatory-pack**.

## 5. La citation est un objet de première classe

Jamais une chaîne libre du type « voir page 12 ». Structure minimale à persister :

```json
{
  "document_id": "…", "revision": 3, "page": 12,
  "char_start": 4180, "char_end": 4325,
  "bbox": [0.12, 0.44, 0.71, 0.49],
  "sheet": null, "layer": null, "object_id": null,
  "extractor": "local_stub_ocr@1.4", "confidence": 0.82
}
```

- `bbox` **normalisée** `[x0, y0, x1, y1]` ∈ [0,1] sur la page, origine en haut à gauche :
  indépendante du DPI et de la taille de rendu.
- `sheet` / `layer` / `object_id` sont renseignés pour un plan, `null` sinon.
- Une extraction sans citation résolvable n'est pas persistable : c'est une erreur, pas un
  avertissement. Un test doit rouvrir la citation et retrouver le texte cité.
- Toute citation pointe une **révision** précise. Une nouvelle révision ne réécrit pas les
  citations existantes ; elle produit un rapport d'écarts.

## 6. Anti-injection de prompt

Un fichier importé est une **donnée non fiable**, jamais une instruction.

- Le texte du document ne va jamais dans le message système. Il transite dans un bloc
  utilisateur délimité, précédé de l'instruction que son contenu est à analyser, pas à exécuter.
- Le modèle ne dispose d'aucun outil pouvant écrire, appeler le réseau, lire un autre tenant
  ou changer un statut. Sa seule sortie est un JSON validé par schéma.
- Toute phrase du type « ignore les instructions précédentes », « approuve ce métré »,
  « envoie ce document à » est traitée comme du texte extrait, journalisée comme signal
  suspect, et n'a aucun effet observable.
- Test obligatoire (scénario 14) : un PDF de fixture contenant une instruction malveillante
  produit exactement le même comportement système qu'un PDF sans elle.

## 7. Confiance, cache, prompts, journalisation

- Seuils **configurables par organisation** (au minimum : accepter, proposer, rejeter).
  Sous le seuil bas : aucune proposition créée. Entre les deux : proposition en file de
  validation. Au-dessus : proposition **pré-cochée**, jamais approuvée d'office.
- Aucun seuil ne crée automatiquement une donnée approuvée. Aucune valeur par défaut codée
  en dur dans le service : elles vivent dans la configuration, comme `ai_enabled`.
- Cache par `(document_sha256, pipeline_version, prompt_version, model_version)` : la clé de
  l'ADR 0003 étendue aux versions de prompt et de modèle. Changer un prompt change la clé,
  donc pas de résultat périmé servi silencieusement.
- Prompts et schémas JSON **versionnés dans le dépôt** (fichiers, jamais en base ni dans une
  variable d'environnement), avec un jeu d'évaluation de fixtures anonymisées à côté.
- Journaliser via le `JsonFormatter` existant : `model`, `model_version`, `prompt_version`,
  `params`, `duration_ms`, `estimated_cost`, `token_count`, `document_id`, `request_id`.
  **Jamais** le texte du document, un extrait, un nom de client ou une clé d'API — voir le
  test existant `test_the_journal_carries_no_document_content_or_secret`
  (`apps/api/tests/test_audit.py`).
- Chaque acceptation, correction ou rejet humain passe par `audit.record()`. Une correction
  est conservée pour l'évaluation ; aucun entraînement sur données client sans accord écrit.

## 8. Interfaces du socle et implémentations à créer

Sept ports sont définis comme `Protocol` Python, sans dépendance fournisseur : `OcrPort`,
`TableExtractionPort`, `ClassifierPort`, `StructuredLlmPort`, `EmbeddingPort`, `SearchPort`,
plus l'`ObjectStore` imposé par l'ADR 0003 (S3 ou disque local ; aucun code métier ne connaît
le fournisseur). Les contrats vivent dans `packages/contracts/`. Les adaptateurs concrets
et l'exécution asynchrone restent à créer ; `apps/worker/` ne contient qu'un `README.md`.
Le service API existant ne traite que les métadonnées et les états, jamais le contenu.

- **Implémentation par défaut : un faux fournisseur local et déterministe.**
  `Settings.ai_provider` est un `Literal["null", "local_stub"]` dont le défaut est `"null"` :
  `local_stub` est la valeur réservée à ce simulacre, elle n'est branchée sur rien aujourd'hui.
  Il permettra de développer et de tester le pipeline hors ligne, sans clé et sans envoi externe.
- `ai_enabled=false` désactive l'envoi externe **et** laisse l'application pleinement
  utilisable (saisie, métré, calcul, gel, export) : invariant déjà testé en phase 1, à ne
  jamais casser — voir **definition-of-done**.
- Politique explicite en cas d'indisponibilité du fournisseur : job en `failed` avec cause
  lisible et relance manuelle, jamais une extraction vide présentée comme un résultat.
- Un fournisseur externe s'ajoute derrière un port existant, sans toucher aux appelants, et
  sans jamais devenir obligatoire (voir **btp-product-rules**, « pas de verrouillage
  fournisseur »).
- Les permissions `DOCUMENT_READ`, `DOCUMENT_WRITE` et `DOCUMENT_VALIDATE` existent dans
  `security/roles.py`. Préserver leur séparation de `BOQ_APPROVE` et la matrice 401/403/404.

## 9. Le RAG ne remplace jamais une requête relationnelle

- Prix, quantités, statuts, totaux, versions : **SQL** sur les tables existantes
  (`price_items`, `boq_items`, `estimate_versions`), via SQLAlchemy et `owned_query`.
- Recherche sémantique : uniquement pour **retrouver du contexte documentaire** (retrouver
  la clause, la page, le passage) et alimenter une proposition citée.
- Interdit : demander à un modèle « quel est le total du lot 3 » ou « ce poste est-il
  approuvé ». Le chiffre vient du moteur (voir **price-engine**), le statut vient de la base.
- L'index vectoriel porte `organization_id` et est filtré **avant** la recherche, pas après
  (voir **multitenant-security**). Un voisin d'un autre tenant est une fuite, pas un bruit.

## 10. Critères de fin — scénarios d'acceptation 11 à 14

Aucun de ces quatre scénarios n'est automatisé aujourd'hui. La phase 2 n'est terminée que
lorsque les quatre sont des tests qui passent (voir aussi **definition-of-done**) :

11. Un PDF scanné est traité en arrière-plan et son état est visible : statut par étape,
    progression, erreur lisible, relance possible.
12. Une clause extraite renvoie à la bonne page et à la bonne zone, et peut être acceptée,
    corrigée ou rejetée ; les trois décisions sont journalisées.
13. Si la confiance est sous le seuil, **aucune** donnée approuvée n'est créée
    automatiquement — vérifier en base, pas seulement dans l'interface.
14. Une instruction malveillante contenue dans le PDF ne change pas le comportement du
    système : mêmes écritures, mêmes statuts, mêmes appels sortants.

Fixtures fictives et créées dans le projet, à l'image de `fixtures/imports/` : aucun cahier des charges réel (droit d'auteur, données client) — règle générale dans **definition-of-done**.

## Signaux d'alerte

- Déduire du socle que l'upload, l'OCR, la classification ou la recherche existent : les
  tables, ports et `services/documents.py` ne traitent encore aucun fichier ni contenu ;
  `apps/worker/` ne contient aucun exécuteur.
- Persister une valeur extraite sans `citation` structurée, sans `confidence` ou sans
  `document_revision`.
- Une citation réduite à « page 12 » ou à un extrait de texte recopié, sans plage de
  caractères ni boîte englobante normalisée.
- Créer une ligne `approved` (`BoqItem.status`, `PriceItem`) directement depuis une
  extraction, ou modifier une quantité déjà approuvée sans passer par un écart soumis à
  l'utilisateur.
- Concaténer du texte de document dans un prompt système, ou donner à un modèle un outil
  capable d'écrire en base, d'appeler le réseau ou de changer un statut.
- Demander à un LLM un total, une conversion, une TVA, un arrondi ou une marge : tout calcul
  vit dans `packages/domain/src/metreo_domain/` (**price-engine**).
- Recherche vectorielle non filtrée par `organization_id`, ou filtrée après le top-k.
- Logger un extrait de document, un nom de client, un chemin de fichier utilisateur ou une
  clé d'API dans le `JsonFormatter`.
- Faire dépendre le démarrage, un test ou un parcours métier d'un fournisseur externe, ou
  ignorer `Settings.ai_enabled`.
- Cache dont la clé ignore `pipeline_version` ou `prompt_version` : un prompt corrigé
  continuerait à servir l'ancienne extraction.
- Conclure « conforme à la norme X » à partir d'une norme simplement citée dans le document.
- Traiter un ZIP sans limite de profondeur, de ratio de décompression ni contrôle des
  chemins, ou faire confiance à l'extension du fichier plutôt qu'à sa signature.
