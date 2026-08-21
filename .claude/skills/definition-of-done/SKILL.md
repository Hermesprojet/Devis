---
name: definition-of-done
description: À utiliser à la FIN d'une tranche de travail Metreo, quand il faut dire si elle est finie — avant d'écrire « terminé », « livré », « prêt », « ça marche » ou « phase N close », avant de committer ou de demander une revue, pour lancer la bonne série de vérifications (pytest du domaine et de l'API, ruff format, ruff check, mypy, alembic upgrade/downgrade, seed, npm run typecheck et build), pour lire les jobs de CI, pour rédiger le compte rendu de fin d'itération (fonctionnalités réellement opérationnelles, fichiers créés ou modifiés, commandes de démarrage, résultats des tests, décisions et hypothèses, limites connues, prochaine tranche), pour trancher si un écran statique, un mock ou une fonction sans test métier peut être annoncé comme disponible, pour marquer des données de démonstration is_demo_data, pour vérifier que README.md, docs/ROADMAP.md et .env.example disent encore la vérité, ou pour décider ce qui ne doit jamais être commité. Décrit la PREUVE attendue, pas la règle métier prouvée.
---

# Metreo — critères de « terminé » (phase 1, en vigueur)

## 1. La règle

Un critère est **prouvé par une commande rejouable ou un test nommé**, sinon il n'est pas
atteint. « Je l'ai vérifié à la main », « ça compile » et « le code est écrit » ne sont pas des
preuves. Le mot « terminé » n'est employé que pour une tranche dont un tiers peut refaire la
démonstration à partir du dépôt seul, sans clé payante et sans service externe.

État de référence du dépôt (à relire avant de parler de phases) : `README.md` (tableau des
phases), `docs/ROADMAP.md` (critères de fin, phase par phase, avec preuve).

## 2. Les douze critères et leur preuve

| # | Critère | Preuve exigée |
| --- | --- | --- |
| 1 | Flux utilisateur réel de bout en bout | Un test qui parcourt le flux — modèle : `test_the_whole_flow_works_with_ai_disabled` (`apps/api/tests/test_platform.py`) — **et** la page correspondante sous `apps/web/src/app/` |
| 2 | Permissions serveur appliquées | `require(Permission.X)` côté API et un test « organisation B reçoit 404 » dans `apps/api/tests/test_tenant_isolation.py` — détail dans **multitenant-security** |
| 3 | Validations et erreurs traitées | Erreur typée (`DomainError` avec `code`, `packages/domain/src/metreo_domain/errors.py`) ou détail structuré rendu par `ErrorNotice` (`apps/web/src/components/Feedback.tsx`), plus un test du cas fautif (modèle : `fixtures/imports/prix_5_valides_2_erreurs.csv`) |
| 4 | Tests pertinents qui passent | Les compteurs du §4 ne baissent pas ; toute nouvelle règle métier a son test |
| 5 | Migrations présentes | Une révision dans `apps/api/alembic/versions/`, l'aller-retour `upgrade`/`downgrade` rejoué, et `test_migrations_reproduce_the_models_exactly` (`apps/api/tests/test_platform.py`) au vert |
| 6 | Accessibilité de base | `lang` sur `<html>` (`apps/web/src/app/layout.tsx`), `aria-label` / `aria-current` (`apps/web/src/components/Shell.tsx`), `role="alert"` sur les erreurs, focus visible (`apps/web/src/app/globals.css`), parcours au clavier essayé |
| 7 | Textes internationalisables | Toute chaîne visible passe par `t()` et sa clé existe dans `apps/web/src/lib/i18n.ts` (`SUPPORTED_LOCALES` = `fr`, `nl`, `en` ; `fr` seul est complet) |
| 8 | Audit ajouté si nécessaire | `audit.record(...)` (`apps/api/src/metreo_api/services/audit.py`) sur création, modification, gel, export, import, changement de droits, plus un test dans `apps/api/tests/test_audit.py` |
| 9 | Logs sans données sensibles | Champs passés au `JsonFormatter` (`apps/api/src/metreo_api/logging_config.py`) : identifiants, compteurs, durées. Jamais de contenu de document, de jeton, de mot de passe, d'adresse e-mail réelle ni de prix nominatif |
| 10 | Documentation et `.env.example` à jour | Toute variable `METREO_*` **nouvelle** lue par `apps/api/src/metreo_api/config.py` est ajoutée à `.env.example` avec un commentaire — `app_name`, `api_prefix` et `jwt_algorithm` y manquent encore, c'est une dette, pas un précédent ; `README.md` et `docs/ROADMAP.md` corrigés dans le même lot |
| 11 | Rien de sensible commité | Le job `secrets` de `.github/workflows/ci.yml` au vert (§5) ; règles de fond sur les secrets : **multitenant-security** |
| 12 | Limites annoncées honnêtement | Tableau des phases de `README.md`, `docs/ROADMAP.md` et, dans l'interface, la carte « Modules à venir » de `apps/web/src/app/parametres/page.tsx` — son texte est écrit en dur : le porter sur la clé `common.notImplemented` de `apps/web/src/lib/i18n.ts`, définie mais encore inutilisée |

## 3. Ce qui ne peut jamais être appelé « terminé »

- **Un écran statique** : une page qui affiche des valeurs codées en dur, ou qui n'appelle pas
  `apps/web/src/lib/api.ts`. Tant que la donnée ne vient pas de l'API, la fonction n'existe pas.
- **Un mock non signalé** : un adaptateur factice, un `return` de valeur inventée, un fournisseur
  simulé. Un simulacre est admis (aucun fournisseur IA/OCR n'est branché aujourd'hui), à condition
  qu'il soit nommé comme tel dans le code, dans l'interface et dans `docs/ROADMAP.md`.
- **Une fonction sans test métier** : un test qui vérifie seulement qu'une route répond `200` ne
  couvre pas la règle. Un calcul, une conversion, une permission, un cas d'erreur : chacun a son
  test d'intention.
- **Une phase déclarée close** alors qu'un critère de `docs/ROADMAP.md` n'a pas de preuve en face.
- **Une tranche verte en local mais pas en CI** (§5).

## 4. Commandes à exécuter avant de déclarer terminé

Aucun compteur n'est écrit ici. Une valeur recopiée dans un skill est vraie le jour où on la
tape et fausse la semaine suivante, et elle est *suivie* — un agent la lit avant de toucher au
code. La commande est donnée ; sa sortie du jour se lit dans le terminal, jamais dans ce fichier.
La référence chiffrée vit dans `docs/PHASE1_VERIFICATION.md`, rattachée à un commit précis.

`scripts/check_skills.py` refuse ces valeurs, y compris à l'intérieur d'un bloc de commandes.

```bash
source .venv/bin/activate                # l'outillage (pytest, ruff, mypy, alembic) y est installé

# 1. Domaine — déterministe, aucune I/O
python -m pytest packages/domain/tests -q

# 2. API — base SQLite créée par les vraies migrations (apps/api/tests/conftest.py)
(cd apps/api && python -m pytest -q)

# 3. Format, lint, typage (configuration : pyproject.toml à la racine)
#    scripts/ en fait partie : il porte le contrôle d'installation propre et celui des skills.
ruff format --check packages/domain apps/api/src apps/api/tests scripts
ruff check       packages/domain apps/api/src apps/api/tests scripts
mypy packages/domain/src/metreo_domain apps/api/src/metreo_api scripts

# 4. Migrations : l'aller-retour complet, jamais seulement `upgrade head`
mkdir -p var && export METREO_DATABASE_URL="sqlite+pysqlite:///$PWD/var/check.sqlite3"
(cd apps/api && PYTHONPATH=src alembic -c alembic.ini upgrade head \
             && PYTHONPATH=src alembic -c alembic.ini downgrade base \
             && PYTHONPATH=src alembic -c alembic.ini upgrade head)

# 5. Jeu de démonstration — idempotent : un second passage répond `status: already_seeded`
(cd apps/api && PYTHONPATH=src python -m metreo_api.seed)

# 6. Web
(cd apps/web && npm run typecheck && npm run build)
```

Règles d'usage :

- Une commande non lancée n'est pas citée dans le compte rendu. On colle la sortie réelle,
  pas une sortie plausible.
- Un compteur de tests qui **baisse** est un défaut à expliquer, jamais un effet de bord accepté.
- Un test supprimé, `xfail`, `skip` ou commenté se justifie dans le compte rendu, avec la
  date de reprise.
- Toucher `apps/api/src/metreo_api/models.py` sans révision Alembic fait échouer
  `test_migrations_reproduce_the_models_exactly` : produire la révision dans le même lot.

## 5. La CI décide, pas le poste local

`.github/workflows/ci.yml` — tous bloquants. Pour la liste à jour :
`python -c "import yaml;[print(j['name']) for j in yaml.safe_load(open('.github/workflows/ci.yml'))['jobs'].values()]"`

La porte de sortie, ce sont **tous** ces jobs, pas les seuls qui ressemblent au travail local.
Une tranche n'est pas finie tant qu'ils ne sont pas verts sur le dernier SHA.

| Job | Ce qu'il ajoute par rapport au local |
| --- | --- |
| `domain` | Installation propre du seul paquet `packages/domain` : une dépendance qui aurait fui vers le domaine casse ici |
| `api-sqlite` | Format, lint, typage — `scripts/` compris — et la suite API sur une machine vierge, sans service |
| `api-postgres` | PostgreSQL 16 + PostGIS : `upgrade head` → `downgrade base` → `upgrade head`, `seed`, puis **la suite complète** via `METREO_TEST_DATABASE_URL` — sans cette variable la conftest retombe sur SQLite et le job ne prouve rien |
| `web` | `npm ci` (installation verrouillée), `npm run typecheck`, `npm run build` |
| `e2e` | Les parcours Playwright contre l'API réelle, pas contre des données simulées |
| `skills` | Frontmatter, chemins cités, compteurs figés, et le fait que le dossier de vérification nomme un commit qui existe |
| `clean-install` | Un environnement vierge démarre depuis les seuls manifestes, **et sur les versions qu'ils exigent** — extras compris |
| `containers` | Les deux images se construisent, ne tournent pas en root et déclarent un point de santé |
| `audit` | `pip-audit` et `npm audit`, **bloquants** en haute et critique |
| `secrets` | Refuse un `.env` versionné et les motifs `sk-…`, `AKIA…`, `BEGIN … PRIVATE KEY` |

Une différence SQLite / PostgreSQL (type `Amount` de `apps/api/src/metreo_api/db.py`, contraintes,
tri) ne se voit que dans `api-postgres` : ne pas conclure avant lui.

## 6. Compte rendu de fin d'itération — format imposé

Sept rubriques, dans cet ordre, à chaque fin de tranche :

```text
1. Fonctionnalités réellement opérationnelles  — une ligne par fonction, avec le test qui la prouve
2. Fichiers principaux créés / modifiés        — chemins réels, groupés par paquet
3. Commandes de démarrage                      — copiées depuis README.md, réellement rejouées
4. Résultats des tests                         — sorties brutes du §4 (domaine, API, lint, typage, web)
5. Décisions et hypothèses                     — ce qui a été tranché faute de réponse → docs/ASSUMPTIONS.md
6. Limites connues                             — ce qui ne marche pas, ce qui est simulé, ce qui manque
7. Prochaine tranche recommandée               — une seule, avec son critère de fin
```

- La rubrique 1 ne contient que ce qu'un utilisateur peut faire ; une classe écrite mais non
  atteignable depuis l'interface ou l'API va en rubrique 6.
- La rubrique 6 n'est jamais vide. Une itération sans limite connue signifie qu'elles n'ont pas
  été cherchées.
- Les décisions structurantes vont aussi dans `docs/ASSUMPTIONS.md` ou un ADR sous `docs/adr/`.

## 7. Données de démonstration

Toute donnée de démonstration est **inventée** : entreprises, chantiers, fournisseurs, prix.
Aucune reprise de bordereau, de cahier des charges ou de tarif fournisseur réels.

- Les lignes semées par `apps/api/src/metreo_api/seed.py` portent `is_demo_data=True`. La colonne
  existe sur `PriceItem` et `CompositePriceRow` (`apps/api/src/metreo_api/models.py`) et dans la
  révision `apps/api/alembic/versions/20260820_1726_initial_schema.py`.
- Le drapeau est exposé par `apps/api/src/metreo_api/schemas.py`, propagé par
  `apps/api/src/metreo_api/services/price_import.py` (paramètre `is_demo_data`) et détecté par `_uses_demo_prices`
  (`apps/api/src/metreo_api/routers/estimates.py`).
- Toute sortie qui s'appuie sur ces lignes affiche l'avertissement : `quote_html(...)` reçoit
  `demo_data_warning` (`apps/api/src/metreo_api/services/exports.py`), l'interface affiche la clé
  `app.demoBanner` via `apps/web/src/components/Shell.tsx`.
- Un nouvel export, un nouveau PDF, un nouvel écran qui montre des montants doit **relayer** ce
  drapeau. Un montant fictif présenté sans mention est traité comme un prix de marché : défaut
  bloquant.
- Les fixtures d'import vivent dans `fixtures/imports/` (jeu valide, jeu volontairement fautif).
  Les tarifs qu'elles contiennent ne servent jamais de référence de prix.

## 8. Prototype, mock, fonction non certifiée

Une fonction non aboutie est autorisée **si elle est visible comme telle aux trois endroits** :
dans le code (nom explicite, docstring qui dit la phase), dans l'interface (clé
`common.notImplemented` ou libellé équivalent, jamais un bouton qui ne fait rien), dans la
documentation (`README.md` et `docs/ROADMAP.md`).

- Aucun résultat inventé : un adaptateur non branché lève une erreur ou renvoie « indisponible »,
  il ne fabrique pas une valeur crédible.
- Aucune donnée réglementaire, fiscale ou normative n'est présentée comme certifiée sans source
  datée — voir **belgium-regulatory-pack**.
- Le produit reste utilisable avec l'IA désactivée (`METREO_AI_ENABLED=false`) : c'est testé par
  `test_the_whole_flow_works_with_ai_disabled` et `test_health_reports_the_environment_and_that_ai_is_off`.

## 9. Branche, commit, et ce qui ne se commite jamais

État réel : remote `origin` = `https://github.com/Hermesprojet/Devis`, travail en cours sur la
branche `claude/new-session-jdj11s`.

- Rester sur la branche de session en cours ; ne jamais committer directement sur la branche par
  défaut du dépôt distant.
- Un commit = une intention. Le changement de modèle, sa révision Alembic et son test partent
  **ensemble** ; sinon la CI casse au commit suivant.
- Message en français, à l'impératif, sujet court puis corps expliquant le pourquoi et les limites
  connues. Citer la phase concernée quand elle est identifiable.
- Ne jamais indexer : `.env`, `var/`, `*.sqlite3`, `node_modules/`, `.venv/`, `__pycache__/`,
  `*.tsbuildinfo`, une clé, un jeton, une donnée personnelle réelle, un document client, un tarif
  fournisseur réel. `.gitignore` en couvre la plupart — il ne dispense pas de relire
  `git status --short` avant de committer.
- Avant de pousser : les commandes du §4 passent, et le compte rendu du §6 est écrit.

## 10. Renvois

Règles produit et posture d'aide à la décision : **btp-product-rules**. Calculs, arrondis, unités,
traçabilité des montants : **price-engine**. Isolation, permissions, audit, secrets :
**multitenant-security**. Critères de fin propres aux phases non commencées : **document-analysis**
(phase 2), **cad-bim-takeoff** (phase 3), **supplier-rfq** (phase 4), **belgium-regulatory-pack**
(packs régionaux).

## Signaux d'alerte

- Le mot « terminé », « livré » ou « prêt » sans sortie de test collée à côté.
- Un compte rendu qui cite une commande jamais exécutée, ou une sortie retapée de mémoire.
- Un nombre de tests qui baisse, un `skip`, un `xfail` ou un test supprimé sans justification.
- Un changement dans `apps/api/src/metreo_api/models.py` sans nouvelle révision dans
  `apps/api/alembic/versions/`.
- Une migration testée seulement en `upgrade`, jamais en `downgrade`.
- Une conclusion tirée du seul SQLite alors que le job `api-postgres` n'a pas tourné.
- Une chaîne visible écrite en dur dans un composant au lieu d'une clé de `apps/web/src/lib/i18n.ts`.
- Une nouvelle variable `METREO_*` lue par `apps/api/src/metreo_api/config.py` et absente de `.env.example`.
- Une action métier importante sans appel à `audit.record(...)`.
- Un log qui contient un jeton, une adresse e-mail réelle, un contenu de document ou un prix
  nominatif.
- Un montant de démonstration affiché sans le drapeau `is_demo_data` ni l'avertissement associé.
- Un bouton, un onglet ou une route qui laisse croire qu'une fonction de phase 2 à 6 existe.
- Un `README.md` ou un `docs/ROADMAP.md` qui annonce comme livré ce que le code ne fait pas.
- Un `.env`, une clé, un document client ou un tarif fournisseur réel apparaissant dans
  `git status --short`.
- Une rubrique « limites connues » vide.
