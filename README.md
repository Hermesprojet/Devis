# Metreo — étude de prix et devis BTP

Création des devis pour travaux publics et construction.

Application professionnelle d'étude de prix, de métré et de devis pour les
entreprises de terrassement, égouttage, voirie, démolition, génie civil et
travaux publics. Premier marché : la **Belgique** (Wallonie), avec un modèle de
données multi-pays et multilingue dès la conception.

> **Outil d'aide à la décision.** Aucune quantité, aucun prix, aucune conclusion
> technique n'est présenté comme certain. Tout montant est accompagné de sa
> décomposition, toute donnée issue d'un document reste une proposition à
> valider par un humain.

`[NOM_APPLICATION]` du cahier des charges est provisoirement **Metreo**. Ce nom
n'est pas figé (voir `docs/ASSUMPTIONS.md`).

---

## État réel du produit

> **Phase 1 fonctionnellement complète — candidate de validation.**
> Déploiement et clôture de sécurité **bloqués** jusqu'à Next.js 15.5.24.
> **Production non prête** : authentification réelle, sauvegardes, supervision
> et packs juridiques validés restent absents.
>
> Ces trois choses sont distinctes et ne se remplacent pas : *fonctionnellement
> complet* décrit ce qu'un utilisateur peut faire et ce que les tests prouvent ;
> *déployable* suppose en plus qu'aucun correctif de sécurité connu ne manque ;
> *prêt pour la production* suppose l'exploitation.

| Phase | Périmètre | État |
| --- | --- | --- |
| 0 | Cadrage, architecture, socle technique, CI | **Fonctionnellement complet** |
| 1 | Organisation, projet, bibliothèque de prix, bordereau, moteur de calcul, gel de version, exports, audit | **Fonctionnellement complet — candidate de validation** |
| 1+ | Répertoire de clients, devis remis en PDF figé, cycle commercial (transmission, consultation, acceptation ou refus), lien client sécurisé, conservation et effacement encadrés | **Fonctionnellement complet** |
| 2 | Intelligence documentaire (OCR, extraction, citations, validation) | Non implémenté |
| 3 | Métrés assistés, plans, IFC/DXF/DWG | Non implémenté |
| 4 | Fournisseurs, demandes de prix, comparatifs | Non implémenté |
| 5 | Industrialisation Belgique (packs validés, néerlandais, connecteurs) | Non implémenté |
| 6 | France puis Europe | Non implémenté |

Aucun écran de l'application ne présente une fonction non implémentée comme
disponible. Il n'y a **aucune intégration externe**, **aucun fournisseur IA ou
OCR**, et **aucun envoi d'e-mail** dans cette version.

Le cycle commercial fonctionne donc **sans domaine ni SMTP** : le lien client se
copie à la main, et l'entreprise peut aussi enregistrer une réponse reçue par
téléphone ou en rendez-vous. Ce n'est pas une signature électronique qualifiée
et l'application ne le prétend nulle part — l'identité du répondant est
déclarative, et les écrans le disent.

**La conservation n'est pas décidée à votre place.** Détruire une organisation
exige une décision de conservation portant sa durée, sa juridiction, sa source
datée, sa date d'effet et son validateur. Le dépôt n'en fournit aucune : une
durée de conservation est une règle réglementaire, et l'inventer serait rendre
un avis juridique. Sans décision, la destruction est refusée — le refus
conserve. Voir `docs/adr/0006-conservation-et-effacement.md`.

---

## Démarrage rapide (sans aucun service payant)

Prérequis : Python 3.11+, Node 22+. Docker est optionnel.

```bash
git clone <ce dépôt> && cd Devis
cp .env.example .env
make install
```

`make install` crée le venv et installe **depuis les manifestes**, sous le
verrou du dépôt. Il n'existe pas de troisième liste de dépendances : les
`pyproject.toml` et `apps/web/package-lock.json` font foi, et c'est ce que la
CI installe. Sans `make`, exactement la même chose :

```bash
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -c constraints/api.txt -e packages/domain -e "apps/api[dev,postgres]"
(cd apps/web && npm ci)
```

Puis, dans deux terminaux :

```bash
make api-dev    # migrations, jeu de démonstration fictif, puis l'API sur :8000
make web-dev    # le front sur :3000, branché sur l'API locale
```

Les deux commandes explicites, si l'on préfère les lancer à la main :

```bash
export METREO_DATABASE_URL="sqlite+pysqlite:///$PWD/var/dev.sqlite3"
(cd apps/api && PYTHONPATH=src alembic -c alembic.ini upgrade head)
(cd apps/api && PYTHONPATH=src python -m metreo_api.seed)
(cd apps/api && PYTHONPATH=src python -m uvicorn metreo_api.main:app --reload --port 8000)

# second terminal
(cd apps/web && NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 npm run dev)
```

Ouvrir <http://localhost:3000> et se connecter avec `admin@dubois.demo`
(mode développement : pas de mot de passe, refusé hors développement).

Documentation interactive de l'API : <http://localhost:8000/docs>.

### Avec PostgreSQL

```bash
docker compose -f infra/docker-compose.yml up -d db redis
export METREO_DATABASE_URL="postgresql+psycopg://metreo:metreo@localhost:5432/metreo"
(cd apps/api && alembic -c alembic.ini upgrade head && python -m metreo_api.seed)
```

### Tout en conteneurs

```bash
docker compose -f infra/docker-compose.yml --profile app up --build
```

---

## Comptes de démonstration

Créés par `python -m metreo_api.seed`. Deux organisations existent afin de
pouvoir vérifier l'isolation multi-tenant.

| Adresse | Rôle | Organisation |
| --- | --- | --- |
| `admin@dubois.demo` | Administrateur de l'entreprise | Terrassements Dubois SA (démo) |
| `metreur@dubois.demo` | Métreur / deviseur | Terrassements Dubois SA (démo) |
| `lecteur@dubois.demo` | Lecteur / auditeur | Terrassements Dubois SA (démo) |
| `admin@janssens.demo` | Administrateur | Wegenbouw Janssens NV (demo) |

**Tous les prix de démonstration sont fictifs** et signalés comme tels dans
l'interface et dans l'aperçu de devis.

---

## Organisation du dépôt

```text
apps/
  api/        API FastAPI, migrations Alembic, tests d'intégration
  web/        Application Next.js (App Router, TypeScript)
  worker/     Réservé aux traitements asynchrones (phase 2)
packages/
  domain/     Cœur métier pur : monnaie, unités, moteur de calcul (aucun I/O)
infra/        Docker Compose et images
docs/         Cadrage, architecture, modèle de données, sécurité, ADR
fixtures/     Jeux de données fictifs (imports CSV valides et fautifs)
.claude/      Skills du dépôt (règles produit, moteur de prix, sécurité…)
```

Le découpage central : **`packages/domain` ne connaît ni la base de données, ni
HTTP, ni l'IA.** Il est déterministe et testable seul. Tout ce qui touche à
l'argent y vit.

---

## Commandes

```bash
# Domaine (aucune dépendance)
python -m pytest packages/domain/tests -q

# API (base SQLite créée par les migrations réelles)
cd apps/api && python -m pytest -q

# Qualité
ruff format --check packages/domain apps/api/src apps/api/tests
ruff check       packages/domain apps/api/src apps/api/tests
mypy             packages/domain/src/metreo_domain apps/api/src/metreo_api

# Web
cd apps/web && npm run typecheck && npm run build

# Migrations
cd apps/api && alembic -c alembic.ini revision --autogenerate -m "..."
cd apps/api && alembic -c alembic.ini upgrade head
cd apps/api && alembic -c alembic.ini downgrade -1
```

---

## Documentation

| Fichier | Contenu |
| --- | --- |
| `docs/PRODUCT_BRIEF.md` | Le produit, ses utilisateurs, son parcours cible |
| `docs/ASSUMPTIONS.md` | Décisions prises faute de réponse, et comment les changer |
| `docs/ARCHITECTURE.md` | Architecture, flux, découpage en modules |
| `docs/DATA_MODEL.md` | Modèle de données et règles d'intégrité |
| `docs/SECURITY_THREAT_MODEL.md` | Modèle de menaces et mesures |
| `docs/ROADMAP.md` | Phases, critères de fin, ce qui n'est pas fait |
| `docs/TESTING.md` | Stratégie de tests et scénarios d'acceptation |
| `docs/CONVENTIONS.md` | Conventions de code, de commit et de revue |
| `docs/adr/` | Décisions d'architecture (ADR) |

---

## Licence et données

Aucune donnée personnelle réelle, aucun cahier des charges protégé et aucun
prix de marché réel ne doit être commité dans ce dépôt. Les fixtures sont
entièrement inventées.
