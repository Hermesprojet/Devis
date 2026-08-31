---
name: multitenant-security
description: À utiliser dès qu'il faut décider QUI a accès à QUOI dans Metreo (phase 1, implémenté) — isolation multi-tenant, authentification, permissions, secrets, audit. Ajouter une table portant un organization_id, écrire ou relire un handler dans apps/api/src/metreo_api/routers/, choisir entre 404 et 403 pour une ressource d'un autre tenant, poser un require(Permission.X), modifier security/auth.py, security/roles.py, services/tenant.py (get_owned, owned_query) ou services/audit.py (record, verify_chain), masquer un déboursé, une marge ou un salaire chargé selon COST_READ / MARGIN_READ, définir une contrainte d'unicité, régler METREO_AUTH_MODE, METREO_JWT_SECRET ou .env.example, instruire le modèle de menaces (fuite inter-tenant, fichier piégé, exfiltration, élévation de privilège, falsification de l'audit), ou compléter apps/api/tests/test_tenant_isolation.py. Référence unique du dépôt sur les secrets et sur la chaîne d'audit.
---

# Metreo — isolation multi-tenant, droits et audit (phase 1, implémenté)

## 1. Périmètre

| Fichier | Rôle |
| --- | --- |
| `apps/api/src/metreo_api/security/auth.py` | `TenantContext`, `current_context`, `require`, `issue_token`, `describe_role` |
| `apps/api/src/metreo_api/security/roles.py` | `Role`, `Permission`, `ROLE_PERMISSIONS`, `permissions_for`, `has_permission` |
| `apps/api/src/metreo_api/services/tenant.py` | `owned_query`, `find_owned`, `get_owned` |
| `apps/api/src/metreo_api/services/audit.py` | `record`, `compute_hash`, `verify_chain`, `count_events` |
| `apps/api/src/metreo_api/config.py` | `Settings`, `effective_jwt_secret`, `validate_startup`, `is_production` |
| `apps/api/src/metreo_api/models.py` | `organization_id`, `deleted_at`, `AuditEvent`, contraintes d'unicité |
| `apps/api/tests/test_tenant_isolation.py` | scénario d'acceptation 1, filet de non-régression |
| `apps/api/tests/test_audit.py` | chaîne d'audit, détection de falsification, droits sur le journal |

Le calcul des coûts masqués relève de **price-engine**, la TVA et les mentions légales de
**belgium-regulatory-pack**, l'envoi d'une demande de prix au bon destinataire de **supplier-rfq**,
l'ingestion de documents non fiables de **document-analysis**, les quantités de plans de
**cad-bim-takeoff**, les invariants produit de **btp-product-rules**, la checklist de sortie de
**definition-of-done**.

## 2. L'organisation vient du jeton, jamais de la requête

- Un handler ne lit **jamais** un `organization_id` depuis le chemin, la query ou le corps.
  La seule source est `context.organization_id`, où `context: TenantContext = Depends(...)`.
- Le jeton porte `sub` (utilisateur), `org` (organisation), `iat`, `exp`, `iss="metreo-api"`.
  Changer l'organisation courante impose un nouveau jeton, pas un paramètre.
- `current_context` relit la `Membership` **à chaque requête** (`is_active` vrai) : révoquer un
  accès prend effet immédiatement, sans attendre l'expiration du jeton. Ne pas mettre ce
  résultat en cache.
- Codes de refus stables, à ne pas renommer : `missing_token`, `invalid_token`, `token_expired`
  (401), `no_membership`, `inactive_account`, `permission_denied` (403).
- Si un jour un endpoint doit accepter un identifiant d'organisation (support, back-office),
  c'est un endpoint séparé, avec sa propre permission et son propre test — jamais un paramètre
  optionnel greffé sur un handler existant.

## 3. Tout accès à une ligne métier passe par get_owned()

```python
project = get_owned(session, Project, context.organization_id, project_id, label="Projet")
query = owned_query(Estimate, context.organization_id)  # pour les listes
```

- Interdit dans un router : `session.get(Model, id)` ou `select(Model).where(Model.id == id)`
  sur une table qui porte un `organization_id`. Le filtre doit être structurel, pas discipliné.
- `owned_query` ajoute aussi `deleted_at IS NULL` quand la table a la colonne : une ressource
  supprimée logiquement ne réapparaît pas par un accès direct.
- Les **identifiants reçus dans un corps de requête** sont des références potentiellement
  volées : ils passent par `get_owned` avant d'être écrits. Exemple en place :
  `_check_price_links` dans `apps/api/src/metreo_api/routers/boq.py` refuse un `price_item_id` ou un
  `composite_price_id` d'un autre tenant **à l'écriture**, pas à la lecture.
- `session.get(OrganizationSettings, context.organization_id)` est la seule exception tolérée :
  la clé primaire *est* l'organisation.

## 4. Une ressource d'un autre tenant renvoie 404, pas 403

- `get_owned` lève `404 {"code": "not_found"}`. Répondre 403 confirmerait qu'un identifiant
  existe : c'est déjà une fuite (énumération d'UUID, comptage de dossiers d'un concurrent).
- 403 est réservé au cas « la ressource est bien à vous, votre rôle ne suffit pas »
  (`permission_denied`, avec `required_permission` et `role` dans le détail).
- Conséquence à respecter dans les tests : PATCH, DELETE, export et sous-ressources d'un autre
  tenant renvoient **404**, y compris quand l'objet existe réellement.

## 5. L'unicité inclut organization_id

Toute contrainte qui pourrait entrer en collision entre deux entreprises porte
`organization_id` en première colonne. En place : `uq_project_org_reference`,
`uq_pricebook_org_name`, `uq_tax_org_code_from`, `uq_membership_user_org`,
`uq_audit_org_sequence`. Quand l'unicité est déjà portée par un parent lui-même scopé
(`uq_priceitem_version_code`, `uq_boqitem_boq_position`), ne pas ajouter `organization_id` :
la portée est transitive. Une référence de chantier `2026-001` doit rester utilisable par deux
entreprises le même jour.

## 6. L'autorisation est serveur, l'interface ne fait que masquer

- Chaque route déclare sa permission : `Depends(require(Permission.BOQ_WRITE))`. Une route
  qui n'utilise que `Depends(current_context)` est un choix explicite (lecture ouverte à tout
  membre), pas un oubli.
- Un contrôle conditionnel se fait avec `context.require(Permission.X)` dans le corps (voir
  `export_csv` : `EXPORT_CLIENT` sur la route, `EXPORT_INTERNAL` exigé en plus quand
  `include_internal=true`).
- `context.can(Permission.X)` sert à **adapter la charge utile**, jamais à décider si l'action
  a lieu.
- Le front reçoit `permissions` via `GET /auth/me` (type `Me` dans
  `apps/web/src/lib/api.ts`). S'en servir pour cacher un bouton relève de l'ergonomie, pas du
  contrôle : toute action cachée doit rester refusée quand elle est appelée à la main.

## 7. Rôles et matrice de permissions

Six rôles, définis dans `ROLE_PERMISSIONS`. `MARGIN_READ` n'est accordé qu'aux deux premiers.

| Rôle (`Role`) | Périmètre |
| --- | --- |
| `org_admin` | toutes les permissions |
| `estimating_manager` | tout sauf `USER_MANAGE` |
| `estimator` | `PROJECT_*`, `PRICEBOOK_*`, `BOQ_READ/WRITE`, `ESTIMATE_READ/WRITE`, `COST_READ`, `EXPORT_CLIENT`, `EXPORT_INTERNAL` |
| `project_manager` | `PROJECT_READ`, `PRICEBOOK_READ`, `BOQ_READ`, `ESTIMATE_READ`, `COST_READ`, `EXPORT_CLIENT` |
| `buyer` | `PROJECT_READ`, `PRICEBOOK_READ/WRITE`, `BOQ_READ`, `ESTIMATE_READ`, `COST_READ` |
| `viewer` | `PROJECT_READ`, `BOQ_READ`, `ESTIMATE_READ`, `AUDIT_READ` |

Points structurants : `estimator` ne peut ni approuver un poste (`BOQ_APPROVE`) ni geler une
version (`ESTIMATE_FREEZE`) ni lire le journal ; `project_manager` voit les coûts à l'écran mais
ne peut pas les exporter (`EXPORT_CLIENT` sans `EXPORT_INTERNAL`) ; hors `org_admin` et
`estimating_manager`, `viewer` est le seul rôle à porter `AUDIT_READ`.
Ajouter un rôle ou une permission impose de mettre à jour `ROLE_PERMISSIONS`, l'endpoint
`/roles` (`apps/api/src/metreo_api/routers/meta.py`) et un test de refus.

## 8. Coûts et marges : deux permissions distinctes des quantités

`COST_READ` (décomposition, déboursé sec, prix de revient) et `MARGIN_READ` (frais généraux,
aléas, marge, coefficients commerciaux) sont séparées de `BOQ_READ` / `ESTIMATE_READ` :
un conducteur lit des quantités sans voir le taux horaire chargé d'une équipe.

- Filtrage des montants internes : `estimating.totals_for_display(..., include_internal=...)`
  retire `total_direct_cost`, `total_cost_price` et, ligne par ligne, `components`,
  `cost_by_kind`, `direct_cost`, `cost_price`, `markup_steps`. Ne jamais filtrer côté client.
- La réponse expose `includes_internal_costs` : l'écran doit dire « masqué », pas afficher un
  trou.
- Les taux commerciaux masqués valent **`null`, jamais `0`** (`apps/api/src/metreo_api/routers/organizations.py`) :
  un zéro serait lu comme une marge nulle réelle. Le reste de l'écran de paramètres reste
  utilisable (`commercial_rates_visible=false`).
- Un nouveau champ portant un coût ou une marge doit être ajouté au masquage **et** au test
  correspondant dans `apps/api/tests/test_estimating.py`.

## 9. Journal d'audit : append-only, tamper-EVIDENT

- `audit.record(...)` ajoute un événement numéroté par organisation (`sequence` 1, 2, 3…) dont
  le `hash` SHA-256 couvre le contenu **et** le `previous_hash` de l'événement précédent de la
  même organisation. `verify_chain` recalcule tout et renvoie `sequence_gap` (suppression) ou
  `hash_mismatch` (modification) avec `failed_at_sequence`.
- Dire les choses telles qu'elles sont : c'est du **tamper-evident, pas du tamper-proof**.
  Quiconque a un accès SQL en écriture peut réécrire une ligne *et* recalculer toute la chaîne
  suivante. Rendre la falsification impossible demande un stockage WORM ou un ancrage externe :
  hors périmètre aujourd'hui.
- Une ligne d'audit ne se modifie ni ne se supprime, jamais, même pour corriger une faute de
  frappe dans un `summary`.
- `payload` ne contient ni contenu de document, ni identifiant de session, ni jeton, ni mot de
  passe, ni donnée personnelle au-delà de ce qui identifie l'objet. Un test le vérifie par
  motif (`bearer `, `password`, `access_token`, `jwt_secret`).
- Toute action qui engage l'entreprise est journalisée avec acteur, date, action, objet :
  création, approbation de poste, gel de version, export, changement de paramètres.

## 10. Configuration, secrets et environnements

- `METREO_AUTH_MODE=dev` émet des jetons sans mot de passe pour les comptes de démonstration.
  `POST /auth/dev-login` renvoie **404 `dev_login_disabled`** dès que `auth_mode != "dev"` ou
  que l'environnement est `staging`/`production`. Ne jamais ajouter d'échappatoire à ce test.
- `Settings.validate_startup()` refuse `auth_mode=dev`, un `jwt_secret` vide et une URL SQLite
  en `staging`/`production` ; `create_app` lève alors `RuntimeError`. Hors production, les mêmes
  problèmes sont journalisés et exposés par `GET /health` (`configuration_problems`).
- `effective_jwt_secret()` lève plutôt que de retomber sur un secret par défaut en production.
- Aucun secret dans le dépôt : `.env` est ignoré (`.gitignore`), seul `.env.example` est
  versionné avec des valeurs non utilisables, et le job `secrets` de
  `.github/workflows/ci.yml` fait échouer la CI sur un `.env` versionné ou un motif de clé
  privée / `sk-…` / `AKIA…`.
- Les données de démonstration (`apps/api/src/metreo_api/seed.py`, `fixtures/imports/`) sont fictives : jamais de
  fichier client réel dans le dépôt.

## 11. Modèle de menaces — état réel

| Menace | Mesure en place | Reste |
| --- | --- | --- |
| Fuite inter-tenant | `TenantContext` + `get_owned` + 404 + tests d'isolation joués aussi sur PostgreSQL en CI | RLS PostgreSQL en défense en profondeur |
| Élévation de privilège | `require(Permission.X)` sur chaque route, `Membership` relue par requête | revue de la matrice à chaque nouveau rôle |
| Prise de contrôle de compte | jeton signé HS256, TTL 8 h, `iss` vérifié, jeton lié à une seule organisation | MFA/SSO (OIDC), cookies httpOnly, limitation de débit sur `/auth` |
| Exposition des marges et coûts salariaux | `COST_READ` / `MARGIN_READ`, masquage serveur, `null` et non `0` | — |
| Falsification ou suppression de l'audit | chaîne de hachage par organisation, `/audit/verify`, `AUDIT_READ` | stockage WORM ou ancrage externe |
| Fichiers malveillants, archives piégées | un seul point d'entrée (import CSV de prix), plafond `max_upload_bytes` → 413, lu par le module `csv` sans exécution | contrôle de type réel (rien ne vérifie le MIME aujourd'hui), antivirus + quarantaine, neutralisation des formules dans les CSV exportés |
| Liens de téléchargement devinables | aucun lien anonyme : les exports passent par une route authentifiée et scopée | URL de fichier signées et courtes quand le stockage de documents arrivera |
| Injection de prompt via documents | aucune IA active (`METREO_AI_ENABLED=false`, `ai_provider=null`) | traitement du contenu comme non fiable — voir **document-analysis** |
| Exfiltration vers un connecteur | aucun connecteur, aucun envoi d'e-mail, aucun appel sortant | allowlist de destinations, journalisation des envois |
| Demande de prix au mauvais destinataire | module absent en phase 1 | confirmation du destinataire et cloisonnement — voir **supplier-rfq** |
| Dépendances vulnérables | dépendances minimales et **bornées** (`apps/api/pyproject.toml`), domaine sans dépendance runtime | épinglage strict (lockfile), analyse de vulnérabilités, mises à jour automatisées |
| Perte ou corruption de fichiers | suppression logique (`deleted_at`), migrations rejouées et vérifiées en CI | sauvegardes chiffrées et test de restauration |

Ne jamais présenter une ligne de la colonne « Reste » comme acquise, ni dans le code, ni dans la
documentation, ni face à un client.

## 12. Non-régression : le test d'isolation est obligatoire

Toute nouvelle ressource appartenant à un tenant reçoit un test « l'organisation B reçoit 404 »
dans `apps/api/tests/test_tenant_isolation.py`, sur le modèle de
`test_direct_api_call_on_another_tenant_project_returns_404` : deux organisations réelles du
jeu de démonstration (`admin@dubois.demo`, `admin@janssens.demo`), un identifiant obtenu par A,
une requête authentifiée de B, `assert response.status_code == 404`. Couvrir lecture, écriture,
suppression, sous-ressources et exports. Ce fichier est rejoué seul sur PostgreSQL par le job
`api-postgres` de la CI : un `get_owned` oublié y échoue avant la revue.

## Signaux d'alerte

- Un handler dont la signature contient `organization_id: str` en paramètre de chemin ou de
  corps, ou qui lit `payload.organization_id`.
- `session.get(Model, some_id)` ou `select(Model).where(Model.id == ...)` dans un router pour une
  table qui porte `organization_id`.
- Un `403` renvoyé parce que la ressource appartient à un autre tenant : ce doit être `404`.
- Un identifiant reçu dans un corps de requête (`price_item_id`, `boq_id`, `project_id`) écrit
  sans être passé par `get_owned`.
- Une nouvelle route sans `require(Permission.X)` ni justification explicite du `current_context`
  nu, ou une permission vérifiée uniquement dans `apps/web`.
- Une `UniqueConstraint` sur un code métier sans `organization_id` alors que deux entreprises
  peuvent employer le même code.
- Un taux de marge masqué renvoyé à `0` au lieu de `null`, ou un filtrage des coûts fait dans le
  composant React plutôt que dans `totals_for_display`.
- Un `UPDATE` ou un `DELETE` sur `audit_events`, un `record()` transportant du contenu de
  document, ou une phrase qui qualifie le journal d'« inviolable » / « tamper-proof ».
- Une exception ajoutée à `dev_login`, un `jwt_secret` par défaut, un secret en dur, un `.env`
  ajouté à l'index, ou un test d'isolation supprimé « parce qu'il est lent ».
- Une nouvelle table métier livrée sans son test « organisation B reçoit 404 ».
