# Vérification de la Phase 1

> Ce document est **lié à un commit précis**. Les compteurs qu'il contient sont
> vrais pour ce commit et faux dès le suivant : c'est la raison pour laquelle ils
> vivent ici et nulle part ailleurs — ni dans les skills, ni dans le `README.md`.
> Pour le régénérer, relancer `make verify` sur le commit concerné et reprendre
> les chiffres affichés.

## Commit vérifié

| | |
| --- | --- |
| Commit | `6ccab1dcc946a79c9b42dcadc2f593f8f2f65a04` |
| Abrégé | `6ccab1d` |
| Branche | `claude/new-session-jdj11s` |
| Pull request | [#1](https://github.com/Hermesprojet/Devis/pull/1) (brouillon) |
| Tête Alembic | `d88792b38c2d` |
| Fichiers versionnés | 117 |

## Environnement de la vérification

| Outil | Version |
| --- | --- |
| Python | 3.11.15 |
| Node | 22.22.2 |
| npm | 10.9.7 |
| PostgreSQL | 16.13 (avec PostGIS en CI : image `postgis/postgis:16-3.4`) |
| Docker | 29.3.1 |
| Playwright | 1.62.1 (Chromium) |

Les versions Python sont contraintes par `packages/domain/pyproject.toml` et
`apps/api/pyproject.toml` ; les versions JavaScript sont verrouillées par
`apps/web/package-lock.json`, installé avec `npm ci`.

## Installation

```bash
make install
```

Équivalent explicite :

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e packages/domain -e "apps/api[dev,postgres]"
cd apps/web && npm ci
```

## Commandes de vérification et résultats

Tout est rejouable par `make verify`. Chaque étape ci-dessous affiche sa
commande et s'arrête au premier échec.

| Étape | Commande | Résultat | Durée |
| --- | --- | --- | --- |
| Format et lint Python | `make lint` | `All checks passed!` | < 1 s |
| Types — domaine | `mypy packages/domain/src/metreo_domain` | 6 fichiers, aucun problème | ~1 s |
| Types — API | `mypy apps/api/src/metreo_api` | 27 fichiers, aucun problème | ~2 s |
| Tests du domaine | `make test-domain` | **61 passed** | 0,1 s |
| Tests API sur SQLite | `make test-api` | **124 passed** | ~55 s |
| Tests API sur PostgreSQL 16 | `make test-api-postgres` | **124 passed** | ~50 s |
| Migrations aller-retour | `make migrations` | `upgrade head` → `downgrade base` → `upgrade head` | ~3 s |
| Jeu de démonstration | `make seed` | `status: seeded` | < 1 s |
| Contrôle des skills | `make skills` | `8 skills conformes.` | < 1 s |
| Aucun secret commité | `make secrets` | `aucun secret évident` | < 1 s |
| Composition Docker | `make compose-config` | `docker compose : valide` | ~1 s |
| Types du front | `make web-typecheck` | `tsc --noEmit` sans erreur | ~2 s |
| Build de production | `make web-build` | 9 routes compilées | ~3 s |
| Parcours navigateur | `make e2e` | **15 passed** | ~52 s |

Les tests API tournent **réellement** sur PostgreSQL lorsque
`METREO_TEST_DATABASE_URL` est défini : chaque test obtient son propre schéma.
Sans cette variable, la suite retombe sur SQLite et `make test-api-postgres`
l'annonce explicitement plutôt que de passer en silence.

```bash
make verify METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo
```

## Intégration continue

Sept jobs, `.github/workflows/ci.yml`, permissions du jeton limitées à
`contents: read` :

| Job | Ce qu'il prouve |
| --- | --- |
| Domaine (calculs déterministes) | le moteur s'installe seul, sans l'API |
| API (SQLite, sans service) | la suite passe sur une machine sans base |
| API (PostgreSQL + PostGIS) | migrations, seed et **suite complète** sur un vrai serveur |
| Web | types et build de production |
| Parcours web (Playwright) | les écrans fonctionnent contre l'API réelle |
| Skills du dépôt | frontmatter, chemins cités, absence de données volatiles |
| Aucun secret commité | pas de `.env` versionné, pas de motif de secret |

## Scénarios d'acceptation couverts

| # | Scénario | Preuve |
| --- | --- | --- |
| 1 | Isolation stricte entre deux entreprises, API comprise | `apps/api/tests/test_tenant_isolation.py`, `apps/api/tests/test_authorization_matrix.py` |
| 2 | CSV : erreurs avant écriture, seules les lignes valides créées | `apps/api/tests/test_price_import.py`, `apps/web/e2e/parcours.spec.ts` |
| 3 | Sous-détail visible et reproductible | `apps/api/tests/test_estimating.py`, `packages/domain/tests/test_pricing.py` |
| 4 | Conversion m³ → tonne refusée sans masse volumique sourcée | `packages/domain/tests/test_units.py`, `apps/api/tests/test_estimating.py` |
| 5 | Poste sans prix signalé et bloquant selon la règle | `apps/api/tests/test_estimating.py`, `apps/web/e2e/parcours.spec.ts` |
| 6 | Gel produisant une version immuable avec sa bibliothèque | `apps/api/tests/test_estimating.py` |
| 7 | Un prix modifié après gel ne bouge pas la version gelée | `apps/api/tests/test_estimating.py` |
| 8 | Export reprenant référence, version, unités, montants | `apps/api/tests/test_estimating.py`, `apps/web/e2e/parcours.spec.ts` |
| 9 | Modifications importantes présentes dans l'audit | `apps/api/tests/test_audit.py` |
| 10 | Produit utilisable avec le service IA désactivé | `apps/web/e2e/ia-desactivee.spec.ts`, `apps/api/tests/test_configuration.py` |

### Au-delà des scénarios imposés

| Garantie | Preuve |
| --- | --- |
| 401 / 403 / 404 distingués sur chaque route montée | `apps/api/tests/test_authorization_matrix.py` — 41 routes en 401, 29 en 403, 28 en 404 |
| Une route ajoutée sans décision d'autorisation casse la suite | `test_every_mounted_route_is_classified` |
| Identifiants imbriqués d'un autre tenant refusés | `test_a_child_of_another_tenant_is_not_reachable_through_an_own_parent` |
| Décimaux identiques sur SQLite et PostgreSQL | `apps/api/tests/test_price_engine_guarantees.py` |
| Empreinte de gel canonique et détectrice d'altération | `test_the_stored_digest_matches_a_recomputation_from_the_stored_snapshot` |
| Montants transportés en chaînes, jamais en nombres JSON | `test_amounts_travel_as_json_strings_not_as_numbers` |
| `.env.example` réellement acceptable par l'application | `apps/api/tests/test_configuration.py` |
| Auth de développement, secret absent et SQLite refusés en production | `apps/api/tests/test_configuration.py` |

## Scénarios **non** couverts

| # | Scénario | Raison |
| --- | --- | --- |
| 11 | PDF scanné traité en arrière-plan, état visible | Phase 2, aucun code |
| 12 | Clause extraite renvoyant à sa page et sa zone | Phase 2, aucun code |
| 13 | Confiance basse ne créant aucune donnée approuvée | Phase 2, aucun code |
| 14 | Instruction malveillante dans un PDF sans effet | Phase 2, aucun code |
| 15 | Aucun message envoyé sans confirmation humaine | Phase 4, aucun code |
| 16 | Offre en unité différente comparée après conversion tracée | Phase 4, aucun code |

## Limites connues et simulations encore présentes

- **Aucun fournisseur IA ni OCR.** `METREO_AI_ENABLED=false` et
  `METREO_AI_PROVIDER=null` par défaut. `local_stub` est une valeur réservée,
  branchée sur rien.
- **Aucune intégration externe**, aucun envoi d'e-mail, aucun connecteur
  fournisseur. Rien n'appelle le réseau pendant les tests.
- **Packs régionaux non validés juridiquement.** BE-WAL, BE-VLG, BE-BRU sont
  semés en statut `draft` et FR en `planned`. Aucune règle n'a été relue par un
  juriste ; les taux de TVA du jeu de démonstration sont fictifs.
- **Row-Level Security PostgreSQL non activée.** L'isolation repose sur la
  couche service (`owned_query`, `get_owned`) et sur les tests ci-dessus.
  Durcissement prévu en phase 5.
- **MFA et SSO préparés, non implémentés.** Le mode `dev` refuse de démarrer en
  production, mais il n'existe pas encore d'alternative complète.
- **Pas d'antivirus ni de quarantaine** — sans objet tant qu'aucun fichier
  utilisateur n'est stocké.
- **Audit détecteur d'altération, pas registre immuable.** La chaîne de hachage
  détecte une ligne modifiée ou supprimée ; un administrateur de base capable de
  recalculer toute la chaîne n'est pas dans le modèle de menace couvert.
- **Colonnes décimales à 10 décimales.** `NUMERIC(28, 10)` ; un total non
  arrondi de 28 chiffres significatifs n'y tient pas. La précision complète vit
  dans l'instantané JSON de la version gelée, qui fait foi.
- **`apps/worker/`, `packages/contracts/`, `packages/config/`, `scripts/`** ne
  contiennent qu'un `README.md` décrivant leur rôle futur — sauf `scripts/`, qui
  porte désormais `check_skills.py`.
- **Données de démonstration entièrement fictives**, marquées `is_demo_data`.
  Aucun prix n'est un prix de marché.

## Reproduire cette vérification

```bash
git checkout 6ccab1d
make install
make verify METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo
make e2e
```
