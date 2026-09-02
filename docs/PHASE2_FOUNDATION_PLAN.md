# Phase 2A — fondations documentaires

Cette tranche prépare l'intelligence documentaire sans accepter encore de
fichier et sans appeler de fournisseur externe. Elle est empilée sur la Phase 1
et ne change aucun calcul de prix.

## Objectif vérifiable

Créer le schéma relationnel, les contrats Python et les permissions sur lesquels
les tranches d'upload, d'OCR et de validation humaine pourront s'appuyer, sans
laisser une donnée extraite devenir une donnée métier approuvée.

## Dans cette tranche

### Modèle relationnel

- `Document` : identité logique, organisation et projet propriétaires.
- `DocumentRevision` : original immuable, numéro de révision, SHA-256,
  taille, type réel, langue/qualité éventuelles et état du pipeline.
- `DocumentStepRun` : étape, version du pipeline, statut, tentative, durée et
  erreur non sensible ; idempotence par révision/version/étape.
- `SourceCitation` : révision, page, plage de caractères et boîte englobante
  normalisée `[x0,y0,x1,y1]`, avec extracteur et confiance.
- `ExtractionProposal` : schéma/version, valeur JSON, citation obligatoire,
  confiance et état de validation ; jamais `approved`.
- `ValidationDecision` : acceptation, correction ou rejet humain append-only,
  acteur, motif et audit.

Chaque table documentaire porte `organization_id`. Les relations doivent
empêcher qu'un enfant d'une organisation référence un parent d'une autre,
y compris par SQL direct.

### Contrats fournisseur-indépendants

`packages/contracts` devient un vrai paquet Python sans dépendance à FastAPI,
SQLAlchemy ou un SDK fournisseur. Il expose des `Protocol` pour :

- `ObjectStore` ;
- `OcrPort` ;
- `TableExtractionPort` ;
- `ClassifierPort` ;
- `StructuredLlmPort` ;
- `EmbeddingPort` ;
- `SearchPort`.

Les entrées et sorties transportent des identifiants, versions, citations,
confiances et erreurs typées ; aucun contrat ne transporte un objet ORM.

### Permissions

Ajouter au minimum des permissions distinctes pour lire des documents et
valider des propositions. Elles ne doivent pas être confondues avec
`BOQ_APPROVE`. La matrice des rôles et les tests 401/403/404 doivent être mis
à jour explicitement.

## Invariants bloquants

1. Une révision publiée est immuable.
2. Une citation pointe une révision précise et résolvable.
3. `page >= 1`, `char_start >= 0`, `char_end > char_start`.
4. Les coordonnées sont des décimaux dans `[0,1]` et
   `x0 < x1`, `y0 < y1`.
5. Une proposition sans citation ou sans confiance est impossible à persister.
6. La confiance est décimale dans `[0,1]`, jamais un `float` applicatif.
7. Une décision humaine ne réécrit pas la proposition source.
8. Une correction conserve avant/après et l'acteur.
9. Aucun statut de cette tranche ne crée ni ne modifie automatiquement un
   `BoqItem`, `PriceItem` ou `EstimateVersion`.
10. Les erreurs et journaux ne contiennent ni texte documentaire, ni nom de
    client, ni clé, ni chemin utilisateur.
11. Toute requête applicative documentaire est filtrée par organisation avant
    lecture ; une ressource d'un autre tenant rend 404.
12. Les contraintes critiques existent en SQL, pas uniquement dans Pydantic.

## Tests d'acceptation de la tranche

- migrations `base -> head` et parité modèles/schéma sur SQLite et PostgreSQL ;
- contraintes de page, plage, boîte, confiance, statuts et immutabilité,
  falsifiées une par une ;
- référence croisée entre deux organisations refusée par la base ;
- matrice d'autorisations et 404 inter-tenant ;
- deux révisions concurrentes obtiennent des numéros distincts sur PostgreSQL ;
- une proposition sous tout seuil reste une proposition et ne crée aucune
  donnée métier approuvée ;
- la sérialisation des contrats ne produit aucun nombre flottant pour une
  confiance ;
- `ai_enabled=false` laisse les parcours Phase 1 inchangés ;
- suite Phase 1 entièrement verte.

## Hors périmètre explicite

Pas d'upload, de stockage binaire, d'URL signée, d'antivirus, d'OCR, de
classification, de LLM, d'embedding, de recherche, de worker ni d'écran dans
cette tranche. Leur absence doit être annoncée ; aucun faux fournisseur ne doit
être présenté comme une fonctionnalité réelle.

## Décisions différées sans bloquer cette tranche

- fournisseur OCR autorisé ;
- hébergement des données dans l'Union européenne ;
- budget et plafond par page ;
- moteur de recherche vectorielle ;
- antivirus de production.

Ces choix seront injectés derrière les ports. Aucun SDK fournisseur ne doit
entrer dans le domaine ou les modèles.

## Definition of done

Le diff est limité aux modèles, migration, contrats, permissions, tests et
documentation correspondante. Lint, types, domaine, API SQLite, API PostgreSQL,
migration round-trip et CI sont verts. La PR #6 a été fusionnée dans `main` le
28 août 2026, après les PR #1, #8 et #9 et par merge commit ; voir
`INTEGRATION_2026-08-28.md`. Rien n'a été déployé, et `PRODUCTION_READY` reste
non atteint.
