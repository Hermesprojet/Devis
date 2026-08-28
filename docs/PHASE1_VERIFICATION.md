# Vérification de la Phase 1

> **Quatre choses distinctes, souvent confondues.** Ce document décrit une
> *procédure* reproductible ; il nomme le *dernier commit contrôlé depuis un
> clone propre* ; la *tête de la PR* est indiquée sur la PR elle-même et peut
> être plus récente ; et c'est la *CI de cette tête* qui fait foi. Un hash
> inscrit dans un fichier versionné ne prouve rien par lui-même — un document
> ne peut pas citer sa propre empreinte, et il vieillit à chaque commit.
> Les compteurs ci-dessous sont vrais pour le commit nommé et faux dès le
> suivant : c'est pourquoi ils vivent ici, et ni dans les skills ni dans le
> `README.md`.

## Trois statuts, trois jeux de critères mécaniques

Ils ne se remplacent jamais l'un l'autre. Chacun se vérifie, aucun ne se
décrète.

### `FUNCTIONALLY_COMPLETE` — atteint

| Critère | État |
| --- | --- |
| Les dix scénarios fonctionnels de la Phase 1 sont couverts | ✅ voir « Scénarios d'acceptation » |
| Les migrations sont cohérentes avec les modèles | ✅ `test_migrations_reproduce_the_models_exactly` |
| La concurrence est couverte | ✅ modules listés dans `test_postgres_only_inventory.py` |
| Aucune anomalie bloquante connue | ✅ à ce commit |
| `release-gate` verte depuis un clone vide | ✅ voir « Ce qui fait foi » |

### `DEPLOYABLE` — atteint au commit `070afe7`

| Critère | État |
| --- | --- |
| `FUNCTIONALLY_COMPLETE` | ✅ |
| Aucune vulnérabilité bloquante connue dans les dépendances | ✅ **Next.js 15.5.24 installé**, les deux RCE critiques du 25 août 2026 couvertes |
| Configuration de déploiement viable | ✅ images Docker non-root, points de santé |
| Installation et construction reproductibles | ✅ verrou et manifestes confrontés aux versions posées |

Ce statut porte sur ce qui se vérifie mécaniquement : rien de connu ne manque
dans les dépendances, l'application se construit et se lance. Il ne dit **pas**
que le produit doit partir en production — c'est `PRODUCTION_READY` qui le
dirait, et il reste non atteint.

### `PRODUCTION_READY` — **non atteint**

| Critère | État |
| --- | --- |
| `DEPLOYABLE` | ✅ |
| Authentification réelle | ❌ mode développement uniquement |
| Gestion des secrets | ❌ variables d'environnement seules |
| Sauvegardes et restauration **testées** | ❌ aucune configurée |
| Supervision et alertes | ❌ |
| Politique d'incidents | ❌ |
| Validation juridique des packs régionaux | ❌ tous en `draft` ou `planned` |

Ce document atteste les deux premiers, pas le troisième.

## Ce qui fait foi

| | |
| --- | --- |
| Règle | les contrôles requis doivent être verts sur le **dernier SHA de la PR** |
| Tête de la PR | [#1](https://github.com/Hermesprojet/Devis/pull/1) — voir l'onglet Checks |
| Dernier commit de **code** contrôlé | `070afe7efc4b18f72cdf209b37d73531ab637c7f` — montée de Next.js à 15.5.24 |
| CI indépendante de ce code | [push 33012064509](https://github.com/Hermesprojet/Devis/actions/runs/33012064509) et [pull_request 33012069125](https://github.com/Hermesprojet/Devis/actions/runs/33012069125) — **10 jobs sur 10 verts, sur les deux déclencheurs** |
| Dernier commit contrôlé depuis un clone propre | `070afe7` — `release-gate` complète, base de porte créée vide juste avant |
| Procédure | `make install` puis les onze étapes ci-dessous, depuis un clone vide |
| Branche | `claude/new-session-jdj11s` |
| Tête Alembic | `e2be18fcac1b` — quatre révisions à ce jour, la dernière imposant une source de prix unique par poste |

## Contrôle indépendant de la tête de code `6d05eb4`

Le dernier changement de code ferme le défaut P1 du helper de base témoin. La
preuve retenue n'est pas le compte rendu de son auteur, mais le workflow CI
exécuté sur le SHA exact
[`6d05eb449ed520baacdfdf0b45f15f5a176537ae`](https://github.com/Hermesprojet/Devis/commit/6d05eb449ed520baacdfdf0b45f15f5a176537ae).

| Contrôle | Résultat vérifié dans les journaux |
| --- | --- |
| Workflow | [32664182873](https://github.com/Hermesprojet/Devis/actions/runs/32664182873), **10/10 jobs verts** |
| Domaine | **127 passed** |
| API SQLite | **480 passed, 25 skipped** — les 25 cas PostgreSQL-only sont inventoriés |
| API PostgreSQL 16 + PostGIS | **505 passed**, aucun skip |
| Inventaire PostgreSQL-only rejoué | **71 passed**, aucun skip |
| Parcours Playwright | **15 passed** |
| Skills du dépôt | **8 skills conformes** |
| Installation depuis les manifestes | 34 chemins, 51 schémas, 35 distributions, 52 exigences |
| Tête Alembic réellement appliquée | `e2be18fcac1b` |

Le correctif P1 repose sur une seule implémentation, dans
`scripts/_url_safety.py`. `owned_witness()` refuse tout paramètre capable de
rediriger la connexion **avant** le premier `create_engine`, puis
`safe_target_url()` retire ces paramètres et demande au dialecte quelle base
sera effectivement ouverte. Les preuves sans serveur vérifient l'ordre du
refus ; les preuves PostgreSQL vérifient qu'une victime reste intacte, qu'une
base vide ne reçoit aucune table témoin, que la cible ouverte est celle créée
par le helper et qu'aucune base résiduelle ne subsiste.

Aucune nouvelle falsification manuelle n'a été exécutée sur ce SHA. Elle n'est
donc pas présentée comme une preuve de fermeture. Les preuves qui font foi ici
sont les tests discriminants lus dans le diff et leur exécution verte sur
PostgreSQL réel.

### Contrôle du canal Claude — preuve distincte

Le [run 32667562716](https://github.com/Hermesprojet/Devis/actions/runs/32667562716)
a vérifié que Claude peut désormais exécuter les commandes explicitement
autorisées : lint et typage verts, puis **127 tests domaine passés**. Dans ce
runner, l'action remplace les chemins sensibles de la PR par ceux de `main`
avant d'exécuter Claude ; comme les skills appartiennent encore à cette PR,
`.claude/skills` y est absent. `make test-api` y donne donc **476 passés,
4 échecs et 25 ignorés**, les quatre échecs étant exclusivement les contrôles
qui lisent ce répertoire.

Ce run Claude prouve le canal et les permissions Bash, **pas** la suite API.
La preuve API reste le workflow CI normal ci-dessus, dont le checkout contient
les huit skills et qui rend 480/25 sur SQLite puis 505/0 sur PostgreSQL.

`DEPLOYABLE` est **atteint** depuis `070afe7` : le correctif Next.js a été
publié, installé, et toute la porte de validation rejouée sur la tête qui le
porte — clone vide compris.

## Comment chaque chiffre de ce document est obtenu

Aucun n'est écrit de mémoire. Chacun se rejoue.

| Valeur | Commande |
| --- | --- |
| SHA | `git rev-parse HEAD` |
| Commits depuis `d8f2b34` | `git log --oneline d8f2b34..HEAD \| wc -l` |
| Fichiers versionnés | `git ls-files \| wc -l` |
| Révisions Alembic | `ls apps/api/alembic/versions/*.py \| wc -l` |
| Routes montées | `tests/routes_inventory.py:mounted_routes(create_app())` |
| Tests du domaine | `make test-domain` |
| Tests API SQLite | `make test-api` |
| Tests API PostgreSQL | `make test-api-postgres METREO_TEST_DATABASE_URL=…` |
| Ignorés et leur identité | `pytest -rs` et `test_postgres_only_inventory.py` |
| Distributions et exigences | `make clean-install` |
| Parcours navigateur | `make e2e` |

## Commit contrôlé depuis un clone propre

| | |
| --- | --- |
| Commit | `eaf60902eb0d08cd188356f4d5bca478d6124562` |
| Abrégé | `eaf6090` |
| Fichiers versionnés | 163 |
| Exécution CI correspondante | [push 32634256412](https://github.com/Hermesprojet/Devis/actions/runs/32634256412) et [pull_request 32634258016](https://github.com/Hermesprojet/Devis/actions/runs/32634258016) — dix jobs verts sur dix, sur les deux déclencheurs |

Les commits postérieurs à celui-ci sont couverts par la CI de la tête, pas par
ce contrôle manuel. Quand l'écart ne porte que sur de la documentation, la CI
suffit ; quand il touche au code, aux dépendances, aux tests ou à la CI
elle-même, refaire le clone propre.

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
.venv/bin/pip install -c constraints/api.txt -e packages/domain -e "apps/api[dev,postgres]"
cd apps/web && npm ci
```

`-c constraints/api.txt` n'est pas facultatif : sans lui l'installation résout
librement et n'est plus celle que la CI vérifie. Une « équivalence » qui omet
le verrou n'en est pas une.

## Commandes de vérification et résultats

Les compteurs et durées des suites ci-dessous sont ceux de la CI du code `6d05eb4`. Le contrôle depuis un clone propre reste rattaché séparément à `eaf6090` ; les confondre créerait une preuve qui n'a pas été exécutée.

`make verify` rejoue les étapes de lint, de typage, de tests, d'installation
propre et de construction. Il ne lance **ni** les migrations, **ni** le seed,
**ni** les parcours navigateur, et `make test-api-postgres` sort en succès
quand `METREO_TEST_DATABASE_URL` manque — un silence qui ressemble à un
succès.

Pour la porte complète, sans rien d'ignoré :

```bash
make release-gate METREO_TEST_DATABASE_URL=postgresql+psycopg://…/metreo_gate
```

`release-gate` refuse de démarrer sans base PostgreSQL, refuse une base dont
**le nom** ne la désigne pas comme jetable, puis enchaîne `verify`,
`migrations`, `seed` et `e2e` — en transmettant cette base à chacun.

Deux précisions qui ont manqué et coûtaient cher. Le contrôle porte sur le nom
de la base **seul** : le chercher dans l'URL entière acceptait une base de
production dont l'hôte contient « ci », dont l'utilisateur s'appelle `tester`
ou dont le mot de passe contient « tmp ». Et `migrations` et `seed` lisent
`METREO_DATABASE_URL`, pas `METREO_TEST_DATABASE_URL` : sans transmission
explicite, `release-gate` validait une URL jetable irréprochable puis lançait
`alembic downgrade base` sur la base configurée du développeur, la vidant,
sans jamais toucher la base jetable. La cible destructive publique a depuis
été **retirée** : l'aller-retour crée sa propre base et ne détruit que
celle-là, ce qui ferme la classe entière plutôt que de la garder.

Chaque étape ci-dessous affiche sa commande et s'arrête au premier échec.

| Étape | Commande | Résultat | Durée |
| --- | --- | --- | --- |
| Format et lint Python | `make lint` | `All checks passed!` | < 1 s |
| Types — domaine | `mypy packages/domain/src/metreo_domain` | 7 fichiers, aucun problème | ~1 s |
| Types — API | `mypy apps/api/src/metreo_api` | 30 fichiers, aucun problème | ~2 s |
| Types — scripts | `mypy scripts` | 6 fichiers, aucun problème | < 1 s |
| Tests du domaine | `make test-domain` | **127 passed** | < 1 s |
| Tests API sur SQLite | `make test-api` | **480 passed, 25 ignorés volontaires** | ~89 s |
| Tests API sur PostgreSQL 16 | `make test-api-postgres` | **505 passed** | ~121 s |
| Aller-retour des migrations | `make migration-roundtrip-test` | base créée par le run, 20 tables, base supprimée | ~6 s |
| Jeu de démonstration | `make seed` | `status: seeded` | < 1 s |
| Installation depuis les manifestes | `make clean-install` | 34 chemins, 51 schémas, 35 distributions, 52 exigences honorées | ~30 s |
| Contrôle des skills | `make skills` | `8 skills conformes.` | < 1 s |
| Aucun secret commité | `make secrets` | `aucun secret évident` | < 1 s |
| Composition Docker | `make compose-config` | `docker compose : valide` | ~1 s |
| Types du front | `make web-typecheck` | `tsc --noEmit` sans erreur | ~2 s |
| Build de production | `make web-build` | 9 routes compilées | ~3 s |
| Parcours navigateur | `make e2e` | **15 passed** | ~40 s |

Les tests API tournent **réellement** sur PostgreSQL lorsque
`METREO_TEST_DATABASE_URL` est défini : chaque test obtient son propre schéma.
Sans cette variable, la suite retombe sur SQLite et `make test-api-postgres`
l'annonce explicitement plutôt que de passer en silence.

`make test-api` retire délibérément `METREO_TEST_DATABASE_URL` de son
environnement. Sans cela, la variable passée à `make verify` valait aussi pour
lui : les deux étapes API tournaient sur PostgreSQL, le chemin SQLite n'était
jamais vérifié, et la même suite était jouée deux fois sur le même moteur.

### Ce que « rien n'a été ignoré » veut dire, et ce qu'il ne veut pas dire

La formule de sortie de `release-gate` porte sur les **étapes** : toutes les
étapes obligatoires de la porte ont été exécutées, aucune n'a été sautée pour
cause de variable absente.

C'est autre chose que les tests ignorés dans la suite SQLite. Ceux-là sont
**volontairement** ignorés : ils exigent un vrai PostgreSQL, et ils sont
exécutés dans la suite PostgreSQL. Les deux notions ne doivent jamais partager
un mot.

Leur identité est contrôlée, pas seulement leur nombre — un compte juste peut
recouvrir treize skips différents. `apps/api/tests/test_postgres_only_inventory.py`
nomme les modules concernés avec leur raison, et refuse aussi bien qu'un
module déclaré perde sa garde qu'un module ordinaire en gagne une. En CI, une
étape distincte exécute ces modules sous PostgreSQL et refuse un résumé
portant `skipped`, `failed` ou `error`.

```bash
make verify METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo
```

## Reproductibilité depuis un clone propre

Vérifié réellement, pas supposé : `git clone` depuis GitHub dans un répertoire
vide, puis `make install`, `make verify` et `make e2e`. Tout passe.

Ce contrôle a trouvé un défaut que rien d'autre ne pouvait voir :
`apps/api/pyproject.toml` déclarait `pydantic>=2.7` alors que `schemas.py`
utilise `EmailStr`, qui exige l'extra `email`. Les trois jobs de CI
installaient une liste de paquets écrite à la main — contenant
`pydantic[email]` — au lieu d'installer le paquet lui-même : ils validaient un
jeu de dépendances qui n'était pas celui du dépôt. Les deux sont corrigés, et
la CI installe désormais `./apps/api` avec ses extras, si bien qu'un manque
dans `pyproject.toml` casse la CI plutôt que le premier `git clone`.

Le contrôle rejoué au commit ci-dessus en a trouvé un second : `make
migrations` échouait sur un clone neuf. L'URL par défaut désigne
`./var/metreo.sqlite3`, `var/` n'est pas versionné, et SQLite ne crée pas le
répertoire — il signale « unable to open database file », un message qui ne
nomme ni le chemin ni ce qui manque. L'application le crée désormais
elle-même, à l'ouverture du moteur comme au démarrage d'Alembic.

## Versions réellement installées

Démarrer ne prouve rien sur les versions : une application démarre très bien
sur une version antérieure à la borne qu'elle déclare, rien n'obligeant un
import à traverser le code qui a besoin de cette borne. `make clean-install`
ne s'arrête donc plus au démarrage.

| Contrôle | Ce qu'il attrape |
| --- | --- |
| Chaque épingle du verrou est installée à la version épinglée | un verrou périmé, citant un paquet que plus rien n'installe |
| Chaque distribution installée figure au verrou | une dépendance transitive non figée, qui flotte au gré des publications amont |
| Chaque exigence des manifestes est satisfaite par la version posée | une borne déclarée mais non respectée |
| Le parcours suit les extras | `pydantic[email]` doit mener à `email-validator` — c'est là que le défaut d'origine se logeait |

Les deux modes d'échec du verrou ont été falsifiés sur une exécution réelle,
et non seulement raisonnés : une épingle sans installation correspondante et
une dépendance transitive retirée du verrou sont toutes deux refusées. Le
parcours de clôture reçoit un résolveur plutôt que d'interroger
l'environnement, ce qui le rend vérifiable sur un graphe construit —
`apps/api/tests/test_dependency_closure.py` le met en défaut sur une version
trop ancienne, une version au-delà du plafond, une exigence absente, un
extra dont la dépendance manque, un extra de racine, un cycle et deux noms
qui ne diffèrent que par leur normalisation.

`packaging` n'est installé qu'après le relevé, pour ne pas fausser la clôture
mesurée ; ce n'est pas une dépendance du produit.

## Vulnérabilités des dépendances

`npm audit` et `pip-audit` sont exécutés à chaque construction par le job
« Vulnérabilités des dépendances », et le rapport est consigné dans ses
journaux.

**Les audits automatisés actuellement publiés sont verts.** Ce n'est pas la
même chose qu'« aucune vulnérabilité connue » : un correctif critique annoncé
pour le 26 août reste en attente, et un avis sous embargo n'apparaît dans
aucun audit tant qu'il n'est pas publié. Le déploiement est bloqué jusque-là.

L'état de départ était : une critique et deux hautes. Traitement :

| Paquet | Avant | Après | Avis levés |
| --- | --- | --- | --- |
| `next` | 15.5.4 | **15.5.24** | tous les avis propres à Next : la RCE critique du protocole flight, puis GHSA-2xp9-vwfh-vxw4 et GHSA-p293-qw3h-jr36 |
| `postcss` | 8.4.31 | **8.5.26** | GHSA-6g55-p6wh-862q, GHSA-r28c-9q8g-f849, GHSA-fxqj-rqcc-2cmp, GHSA-qx2v-qp2m-jg93 |
| `sharp` | 0.34.5 | **0.35.3** | GHSA-f88m-g3jw-g9cj (CVE-2026-33327, -33328, -35590, -35591 dans libvips) |

Montée sur la branche de maintenance 15.5, pas de migration vers Next 16 :
le tag npm `backport` pointe 15.5.24. React reste en 19.1.0, que cette
version accepte (`peer ^19.0.0`). `postcss` et `sharp` sont des dépendances
transitives de Next, relevées par des `overrides` ciblés dans
`apps/web/package.json`.

Une migration majeure vers Next 16 reste une tranche à part, avec analyse des
changements incompatibles — elle n'est plus imposée par la sécurité.

**Blocage levé au commit `070afe7`.** `next@15.5.24` est publié : `npm view
next dist-tags` donne `backport: 15.5.24`, et la version existe au registre.
Deux avis critiques du 25 août 2026 sont couverts — GHSA-2xp9-vwfh-vxw4,
exécution de code à distance non authentifiée dans l'API d'optimisation
d'images sur fichiers AVIF, et GHSA-p293-qw3h-jr36, exécution de code à
distance non authentifiée sur les serveurs hébergés sous Windows.

Les onze étapes de la procédure ont été exécutées dans l'ordre, sans raccourci :

| | Étape | Résultat |
| --- | --- | --- |
| 1 | avis officiels lus, versions et fonctionnalités affectées vérifiées | deux avis critiques ; leurs surfaces sont confrontées au code sous « Exposition réellement mesurée » |
| 2 | existence de `next@15.5.24` au registre | `version = '15.5.24'` |
| 3 | manifeste et verrou régénérés par l'outil du projet | `npm --prefix apps/web install --package-lock-only --ignore-scripts --save-exact next@15.5.24` |
| 4 | `make install`, puis `npm audit --audit-level=high` | `found 0 vulnerabilities`, code de sortie 0 |
| 5 | diff des dépendances transitives et des `overrides` | seuls `next` et `@next/*` bougent ; aucun paquet ajouté ni retiré ; `overrides` inchangés |
| 6 | typage, construction, parcours Playwright | `tsc --noEmit` propre, 9 routes construites, **15 passed** |
| 7 | images Docker reconstruites | job « Images Docker » vert en CI |
| 8 | `release-gate` complète | `tout est passé, rien n'a été ignoré.` |
| 9 | clone vide sur le SHA portant la dépendance | `070afe7`, `next` posé en 15.5.24 sur disque |
| 10 | CI push et pull request | [33012064509](https://github.com/Hermesprojet/Devis/actions/runs/33012064509) et [33012069125](https://github.com/Hermesprojet/Devis/actions/runs/33012069125), 10/10 |
| 11 | requalification de `DEPLOYABLE` | faite, ci-dessus |

Aucun passage à Next 16, à une version canary ou à une autre branche.

**Ce que l'audit ne prouve pas.** `npm audit` rendait déjà zéro **avant** la
montée de version : ces deux avis n'étaient pas encore dans sa base. Un audit
vert n'est donc pas ici la preuve du correctif — la preuve est la version
réellement posée sur disque, `next 15.5.24`, relevée par `npm ls` et par la
lecture directe du `package.json` installé. C'était la réserve que ce document
posait avant la publication ; elle valait, et elle vaut encore pour l'avis
suivant, quel qu'il soit.

### Exposition réellement mesurée

Vérifiée dans le code avant de trancher, et non supposée : App Router, mais
9 des 11 composants portent `'use client'` (les deux autres sont
`layout.tsx` et `icon.tsx`) ; ni Server Actions, ni route handler, ni
middleware, ni `next/image` ; `output: 'standalone'`. `postcss` ne traite que
la feuille de style du dépôt, à la construction. L'exposition était donc
faible — pas nulle — et elle est close plutôt que documentée.

Confrontée aux deux avis critiques du 25 août 2026, la même lecture donne :
GHSA-2xp9-vwfh-vxw4 vise l'API d'optimisation d'images, que ce front n'utilise
pas — `next/image` est absent et aucune route d'image n'est montée ;
GHSA-p293-qw3h-jr36 vise les serveurs hébergés sous Windows, alors que les
images Docker du dépôt sont Linux. L'exposition mesurée est donc nulle sur ces
deux avis précis.

Cela ne change rien à la décision : la version est montée quand même. Une
exposition nulle se mesure sur la configuration d'aujourd'hui, pas sur celle
de la phase 2 — qui accepte des fichiers, donc des images — et une lecture de
code n'est pas un audit du correctif. Rester sur une version portant deux RCE
critiques connues parce qu'on croit ne pas toucher la surface concernée est un
pari, pas une analyse.

## Concurrence

Trois séquences lisaient un état puis écrivaient une décision prise à partir
de cette lecture. Rien ne retenait une seconde transaction entre les deux.

La course a été **reproduite avant correction**, sur PostgreSQL réel, avec
l'algorithme d'origine — lire tous les numéros, prendre le maximum, ajouter
un — deux fils synchronisés par une barrière, cinq tours :

```text
tour 1 : COURSE REPRODUITE — UniqueViolation sur uq_pbv_book_number
tour 2 : COURSE REPRODUITE — UniqueViolation sur uq_pbv_book_number
tour 3 : numéros [4, 5]
tour 4 : numéros [6, 7]
tour 5 : numéros [8, 9]
```

Deux tours sur cinq. C'est bien le propre d'une course : elle ne se manifeste
pas à chaque fois, et une exécution qui passe ne prouve rien. Les données
restaient justes — la contrainte d'unicité fait son travail — mais
l'`IntegrityError` n'était interceptée dans aucune route : le service rendu
était un HTTP 500 pour une demande légitime.

`services/locking.py` porte le geste et l'ordre d'acquisition, pour ne pas
remplacer les courses par des interblocages :

```text
ImportBatch → BoqItem → PriceBook → PriceBookVersion → Estimate → EstimateVersion → Organization
```

L'organisation vient en **dernier**, parce que c'est `audit.record` qui la
verrouille pour allouer la séquence d'audit, et qu'on enregistre après l'acte
qu'on consigne. Un seul appelant prend deux lignes métier — la validation d'un
import verrouille le lot qu'elle consomme puis la version où elle écrit — et
il suit cet ordre. La règle pour la suite : ne jamais verrouiller
`Organization` avant une ligne métier, et prendre deux lignes métier dans
l'ordre ci-dessus. L'inverser contre les requêtes existantes remplacerait la
course par un interblocage, ce qui est pire puisqu'il échoue même sans
contention nuisible.

Le **mode** de verrou compte autant que l'ordre, et le premier choix était le
mauvais. `audit.record` prenait un `FOR UPDATE` sur la ligne `organizations`.
Or toute insertion d'une ligne portant `organization_id` fait vérifier la clé
étrangère, et PostgreSQL prend pour cela un `FOR KEY SHARE` sur cette même
ligne — un verrou faible, que deux transactions obtiennent ensemble. Chacune
demandait ensuite le `FOR UPDATE`, incompatible avec le `FOR KEY SHARE` de
l'autre : montée de verrou croisée, cycle, `40P01 deadlock detected`, HTTP 500.
Deux écritures **sans aucun rapport** dans la même organisation suffisaient.

Isolé au niveau SQL, le mécanisme est déterministe : trois essais sur trois
s'interbloquent en `FOR UPDATE`, zéro sur trois en `FOR NO KEY UPDATE`. Ce
dernier s'oppose à lui-même — les allocateurs de séquence restent donc
sérialisés — mais pas au `FOR KEY SHARE` des clés étrangères. C'est
exactement la distinction pour laquelle ce mode existe. Le mode est encadré
des deux côtés par les tests : le remettre à `FOR UPDATE` fait tomber le test
d'interblocage, l'affaiblir jusqu'à `FOR KEY SHARE` fait tomber ceux de la
séquence.

Cet ordre a d'abord été documenté **à l'envers**, avec une justification
fausse à l'appui. Un commentaire ne se vérifie pas :
`apps/api/tests/test_lock_order.py` lit l'AST des routes et exige, dans chaque
fonction qui prend un verrou métier, que ce verrou précède son appel à
`audit.record`. L'inverser dans `publish_version` fait tomber le test, qui
nomme la fonction fautive.

| Séquence | Ligne verrouillée |
| --- | --- |
| Numéro de version de bibliothèque | la `PriceBook` parente |
| Numéro de version d'estimation | l'`Estimate` parente |
| Publication d'une version, écriture d'un prix | la `PriceBookVersion` elle-même |
| Gel d'une version | l'`EstimateVersion` elle-même |

Le filtre par tenant fait partie de la requête de verrouillage : on ne peut
pas verrouiller une ligne qu'on n'a pas le droit de voir. Les contraintes
d'unicité restent le dernier rempart — un verrou est une convention entre
écrivains bien élevés, une contrainte est ce que la base impose.

Falsification, verrou neutralisé puis rétabli :

```text
verrou neutralisé : 3 failed / 2 failed / 3 failed / 3 failed   (4 exécutions)
verrou rétabli    : 4 passed / 4 passed / 4 passed
```

Les tests s'ignorent hors PostgreSQL. SQLite sérialise ses écritures : ils y
passeraient sans rien prouver, ce qui est pire qu'une absence de test.

Mais rien ne distinguait, dans le job PostgreSQL, « quatre tests verts » de
« quatre tests ignorés » : une variable mal orthographiée les aurait ignorés
là aussi, en silence. Une étape de CI porte donc sur le fait qu'ils ont
**tourné**, pas seulement qu'ils n'ont pas échoué — vérifiée dans les deux
sens, elle lit `4 passed` avec la base et refuse sur `4 skipped` sans elle.

Deux courses voisines sont couvertes au même endroit. Publication contre
écriture d'un prix : le résultat est séquentiel dans un sens ou dans l'autre,
la version finit publiée, et un prix n'apparaît jamais après la publication.
Deux gels simultanés de la même version : un seul réussit, le second reçoit
`409 already_frozen`, et **un seul** événement `estimate_version.frozen` est
écrit.

La prévisualisation d'import garde délibérément une lecture sans verrou : elle
enchaîne sur `await file.read()`, et tenir une ligne verrouillée pendant la
lecture d'un fichier bloquerait toute publication. Elle n'écrit aucun prix ;
c'est le commit qui verrouille, refuse, et fait foi.

## Retour arrière

`alembic downgrade base` **n'est pas une procédure de retour arrière**. Sur
une base peuplée, elle supprime tout le schéma applicatif et les données avec.
Elle n'apparaît ici que comme test destructif, dans une base que le run vient
de créer. Aucune URL fournie par un appelant n'est acceptée comme cible : les
paramètres de requête qui déplaceraient la connexion — `dbname`, `database`,
`host`, `hostaddr`, `port`, `user`, `service`, `passfile` — sont refusés avant
toute connexion, l'URL éphémère est confrontée à `create_connect_args()`, et
rien n'est supprimé sans la preuve d'une création réussie.

Quatre choses distinctes, souvent confondues :

| | Quand | Sur quoi |
| --- | --- | --- |
| Test `head → base → head` | en CI et dans `make release-gate` | une base **créée par le run lui-même**, jamais une base préexistante — il détruit tout |
| Retour d'une révision précise | après une migration fautive, si son `downgrade` est réversible sans perte | `alembic downgrade <révision précédente>` |
| Restauration depuis une sauvegarde | quand la migration n'est pas réversible sans perte | une sauvegarde **vérifiée**, restaurée d'abord ailleurs |
| Retour arrière applicatif | quand le schéma peut rester en avance sur le code | redéployer la version précédente de l'application, schéma inchangé |

Le troisième cas est le défaut à supposer, pas l'exception : une migration
qui supprime une colonne ou change un type ne rend pas ce qu'elle a écarté.
Aucune sauvegarde n'est aujourd'hui configurée — c'est l'un des points qui
séparent « fonctionnellement complet » de « prêt pour la production ».

## Conteneurs

Construits et contrôlés en CI, le proxy de la session de développement
bloquant le CDN de Docker Hub.

| Contrôle | Résultat |
| --- | --- |
| `docker compose config` | valide |
| Construction `infra/api.Dockerfile` | réussie |
| Construction `infra/web.Dockerfile` | réussie |
| UID effectif de l'API | **10001** |
| UID effectif du front | **1000** |
| Point de santé déclaré | sur les deux images |

Les deux images sont en plusieurs étapes : les outils de construction ne
survivent pas à l'étape finale. Le code appartient à `root` alors que le
processus tourne sous un compte non privilégié — l'application ne peut pas
réécrire ses propres fichiers. Un seul répertoire lui est ouvert en écriture,
`/var/lib/metreo`, en 700, monté depuis un volume : c'est là qu'atterriront
les documents de la phase 2.

Être non-root réduit le risque, il n'isole pas. Ce n'est qu'une mesure parmi
d'autres avant de traiter des fichiers venant de tiers.

## Bornes numériques

Les colonnes décimales sont des `NUMERIC(28, 10)` : dix décimales, donc
dix-huit chiffres avant la virgule, soit une capacité **exclusive** de 10^18.
`metreo_domain.bounds` définit huit bornes métier — quantité, prix unitaire,
total, taux, rendement, masse volumique, coefficient, distance — chacune avec
son minimum inclusif ou exclusif, son maximum, ses décimales utiles et son
unité. Les schémas Pydantic les dérivent au lieu de redéclarer des maxima.

La démonstration a d'abord échoué, et c'est ce qui a corrigé la conception :
quantité maximale × prix unitaire maximal vaut exactement 10^18 et ne tient
donc pas. Ce ne sont pas les bornes d'entrée qui protègent le stockage, mais
celle du **total**, vérifiée sur le résultat calculé avant écriture. La plus
grande valeur réellement écrite vaut 10^12 — six ordres de grandeur sous la
capacité, vérifié par `test_no_accepted_value_can_saturate_the_sql_column`.

Un dépassement produit un `422` nommant la borne, la valeur et les deux
limites, jamais un arrondi silencieux ni une erreur SQL. Un test documente
honnêtement ce que cette protection ne peut pas attraper : 2,4 saisi en
g/cm³ au lieu de 2400 kg/m³ reste une masse volumique valide dans la plage.

## Intégration continue

Dix jobs, `.github/workflows/ci.yml`, permissions du jeton limitées à
`contents: read`, actions tierces épinglées à leur SHA complet, `concurrency`
annulant les exécutions obsolètes d'une même référence :

| Job | Ce qu'il prouve |
| --- | --- |
| Domaine (calculs déterministes) | le moteur s'installe seul, sans l'API |
| API (SQLite, sans service) | la suite passe sur une machine sans base |
| API (PostgreSQL + PostGIS) | migrations, seed et **suite complète** sur un vrai serveur |
| Web | types et build de production |
| Parcours web (Playwright) | les écrans fonctionnent contre l'API réelle |
| Skills du dépôt | frontmatter, chemins cités, absence de données volatiles |
| Installation depuis les manifestes | un environnement vierge démarre, et sur les versions que les manifestes exigent |
| Images Docker | construction, UID effectif non nul, point de santé |
| Vulnérabilités des dépendances | `pip-audit` et `npm audit`, **bloquants** en haute et critique |
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
| 401 / 403 / 404 distingués sur chaque route montée | `apps/api/tests/test_authorization_matrix.py` — 52 routes montées, 42 en 401, 30 en 403, 29 en 404 |
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
- **`apps/worker/`, `packages/contracts/`, `packages/config/`** ne contiennent
  qu'un `README.md` décrivant leur rôle futur. `scripts/` porte désormais du
  code — `check_skills.py`, `check_clean_install.py`,
  `verify_dependency_closure.py` — et entre à ce titre dans `make lint`,
  `make types` et le job API de la CI.
- **Données de démonstration entièrement fictives**, marquées `is_demo_data`.
  Aucun prix n'est un prix de marché.

## Matrice de fermeture

Chaque ligne se lit : le risque, la reproduction qui l'a rendu visible, la
correction, la falsification qui prouve que la correction mord, et le test
permanent qui la retient. Toutes ont été jouées sur le commit nommé en tête.

| Risque | Reproduction rouge | Correction | Falsification | Test permanent |
| --- | --- | --- | --- | --- |
| Numérotation concurrente | `UniqueViolation` sur 2 tours de 5 | verrou sur la ligne parente avant de compter | verrou neutralisé → rouge | `test_version_concurrency.py` |
| Publication contre écriture | ordre déduit d'un horodatage pris après `commit()` | deux ordres imposés par `Event`, connexion préchauffée | 5 rouges sur 5 sans verrou | idem, paramétré |
| Double gel | deux gels réussissaient, deux événements écrits | verrou sur la version avant de décider | verrou neutralisé → rouge | idem |
| Interblocage d'audit | `40P01` déterministe, 3 essais sur 3 en SQL isolé | `FOR NO KEY UPDATE` au lieu de `FOR UPDATE` | `FOR UPDATE` rétabli → rouge 5/5 | `test_write_contention.py` |
| Quantité approuvée | modifiée sans dérogation ni droit d'approbation | verrou tenu entre la lecture du statut et l'écriture | correctif retiré de la route → rouge 3/3 | `test_version_concurrency.py` |
| Rejeu d'import | double clic rejouait, deux événements d'audit | verrou puis relecture du statut | correctif retiré de la route → rouge 3/3 | `test_import_idempotence.py` |
| Migration sur données invalides | `IntegrityError` nue, sans nommer la ligne | inspection préalable, arrêt qui nomme | `UPDATE` automatique ajouté → rouge | `test_migration_policy.py` |
| Cible de base destructive | `downgrade base` sur une base fournie | cible retirée ; base créée et possédée par le run | 11 noms refusés dont `metreo_gate` | `test_migration_roundtrip.py` |
| Détournement libpq | `?dbname=` déplaçait la base réellement ouverte | dialecte interrogé, paramètres de redirection refusés | interception retirée → rouge | `test_disposable_database.py` |
| Variables d'environnement | `?=` laissait l'environnement écraser la base validée | affectation inconditionnelle, passage en ligne de commande | `make -n` avec la variable exportée | `Makefile` |
| SQLite réellement exécuté | même suite jouée deux fois sur PostgreSQL | `env -u METREO_TEST_DATABASE_URL` sur `test-api` | chiffres désormais distincts entre les deux étapes | `Makefile` |
| Concurrence réellement exécutée | un échec passait pour un succès | `pipefail`, refus de `failed` et de `skipped` | trois cas vérifiés : 1, 1, 0 | garde CI sur 5 modules |
| Identité des tests ignorés | seul le nombre était contrôlé | inventaire nommé, dans les deux sens | garde retirée / ajoutée → rouge | `test_postgres_only_inventory.py` |
| Vérificateur de skills | vert de complaisance sur des compteurs périmés | motifs élargis, suivi de blocs conforme à CommonMark | chaque règle falsifiée séparément | `test_skills_checker.py` |
| Règle de cohérence sous `-O` | `assert` supprimé, refus disparu | `raise RuntimeError` | `AssertionError` rétablie → rouge | `test_lock_order.py` |
| Ordre de verrouillage non exhaustif | 3 routes BOQ hors contrôle, 2 autres trouvées | source unique, détection des enveloppes | verrou déplacé après l'audit → rouge sur les 3 | idem |
| Dialecte inconnu | traceback nu, code de sortie 0 | interception, refus lisible, code 1 | interception retirée → rouge | `test_disposable_database.py` |
| `seed` en production | aucun refus, prix fictifs écrivables | `SeedRefused` hors développement et test | refus neutralisé → rouge | `test_seed_safety.py` |
| `seed --reset` général | supprimait toutes les organisations et tous les utilisateurs | restreint aux organisations semées, nommément | `delete` général rétabli → rouge | idem |
| Migrations de la base de CI | régression : plus de schéma, seed en échec | étape `upgrade head` séparée de l'aller-retour | étape retirée → rouge | `test_migration_roundtrip.py` |
| Aller-retour détourné par `?dbname=` | `…/postgres?dbname=metreo_victim_a` : la victime passe de 2 organisations à 0, la base jetable reste vide | refus des paramètres redirecteurs **avant** `create_engine` | refus neutralisé → 9 rouges | `test_migration_roundtrip.py` |
| URL éphémère encore redirigée | `parsed.set(database=…)` garde la chaîne de requête | URL construite sans ces paramètres, confrontée à `create_connect_args()` | vérification neutralisée → rouge, l'URL rendue ouvre « victime » | idem |
| Destruction sans preuve de création | collision de nom : `CREATE` échoue, la base préexistante et ses 3 lignes témoins sont supprimées | `created` passé à True après le seul CREATE réussi ; rien n'est terminé ni supprimé sans lui | garde retirée → 2 rouges, dont la preuve sur serveur réel | idem |
| Listes de paramètres divergentes | le contrôle de nom connaissait `dbname`, pas `database` ; l'aller-retour ni l'un ni l'autre | source unique dans `scripts/_url_safety.py` | seconde définition réintroduite → rouge | idem |
| Porte semant une base non migrée | clone propre, base `metreo_gate` neuve : `relation "organizations" does not exist` | `upgrade head` avant le seed, URL transportée explicitement | étape retirée → rouge | idem |
| Preuves détruisant une base étrangère | base préexistante au nom fixe du test, avec trois sentinelles et une table sans rapport : elle disparaît **pendant que le test passe au vert** | base témoin possédée, nom tiré au hasard, `created_by_test` faux au départ | nom fixe ou DROP de préparation réintroduit → rouge | `test_migration_roundtrip.py`, `witness_database.py` |
| Helper « faisant de la place » | un second helper à qui l'on impose un nom occupé | collision → nouveau tirage, jamais de suppression | suppression de préparation rétablie → « le second a pris la base du premier » | idem |
| Destruction après échec total de création | toutes les créations échouent, le nettoyage supprime quand même | terminaison et DROP conditionnés par `created_by_test` | drapeau neutralisé → DROP émis sur un nom non créé | idem, contrôle déterministe sans serveur |
| Suites concurrentes non isolées | deux exécutions visaient les mêmes noms fixes | noms aléatoires par test et par run | tourniquet imposant le chevauchement, noms comparés | idem |

Douze de ces risques n'étaient pas dans la liste demandée : ils sont apparus en
traitant les autres. Les quatre derniers viennent d'une supervision externe, et
tiennent en une phrase : le code de production avait fermé la classe de
défauts, les tests censés le prouver la rouvraient. Une preuve qui commence par
détruire ce qu'elle prétend épargner n'est pas une preuve — elle passait au
vert en supprimant une base de développeur. Deux ont été trouvés par un contrôle que le travail
venait d'ajouter — l'inventaire des enveloppes de verrouillage en a nommé deux
de plus que celles qui étaient signalées. Trois autres sont sortis du dernier
tour : les deux couches de défense qui manquaient autour de l'URL éphémère, et
une porte qui semait une base que plus rien ne migrait — celle-là n'était
visible que depuis un clone propre sur une base réellement neuve, les
exécutions précédentes profitant du schéma laissé par une commande antérieure.

Les deux derniers bloquants ont la même forme que les trois premiers de la
série : une commande destructrice qui déduit sa cible d'une URL plutôt que de
la produire elle-même, et une preuve de propriété qu'on croyait acquise. Le
correctif ne les rattrape pas au vol : il refuse en amont ce qui est ambigu,
et n'accorde le droit de détruire qu'au vu d'une création réussie.

## Limites connues de l'outillage

`scripts/check_skills.py` réimplémente à la main un fragment de CommonMark —
le suivi des blocs de code, avec le caractère et la longueur du délimiteur.
C'est allé assez loin : trois corrections successives, dont deux qui se
trompaient dans les deux sens à la fois.

**Cette logique ne doit plus croître.** Si un cas de délimiteur supplémentaire
se présente, la réponse est un analyseur Markdown existant, pas une règle de
plus. En l'état les tests couvrent les formes rencontrées et aucun faux
positif n'est connu ; c'est une limite à surveiller, pas une dette à payer
aujourd'hui.

Le principe vaut plus généralement : quelques formes périssables clairement
interdites, des tests ciblés, zéro faux positif connu — plutôt qu'un
pseudo-analyseur qui grandit.

## Reproduire cette vérification

Le commit nommé dans « Ce qui fait foi » ci-dessus — et non un hash recopié
ici, qui redeviendrait faux au commit suivant.

```bash
git checkout <le commit contrôlé, en tête de ce document>
make install
make release-gate METREO_TEST_DATABASE_URL=postgresql+psycopg://…/metreo_gate
```

`release-gate` enchaîne `verify`, les migrations, le seed et les parcours
navigateur, et refuse de démarrer sans base jetable. Le chiffre SQLite du
tableau vient d'un `make test-api` séparé, sans la variable.
