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

| Phase | Périmètre | État |
| --- | --- | --- |
| 0 | Cadrage, architecture, socle technique, CI | **Livré** |
| 1 | Organisation, projet, bibliothèque de prix, bordereau, moteur de calcul, gel de version, exports, audit | **Livré** |
| 2 | Intelligence documentaire (OCR, extraction, citations, validation) | Non implémenté |
| 3 | Métrés assistés, plans, IFC/DXF/DWG | Non implémenté |
| 4 | Fournisseurs, demandes de prix, comparatifs | Non implémenté |
| 5 | Industrialisation Belgique (packs validés, néerlandais, connecteurs) | Non implémenté |
| 6 | France puis Europe | Non implémenté |

Aucun écran de l'application ne présente une fonction non implémentée comme
disponible. Il n'y a **aucune intégration externe**, **aucun fournisseur IA ou
OCR**, et **aucun envoi d'e-mail** dans cette version.

---

## Démarrage rapide (sans aucun service payant)

Prérequis : Python 3.11+, Node 22+. Docker est optionnel.

```bash
git clone <ce dépôt> && cd Devis
cp .env.example .env

# --- API -----------------------------------------------------------------
python -m venv .venv && source .venv/bin/activate
pip install -e packages/domain
pip install "fastapi>=0.115,<1.0" "uvicorn[standard]" "sqlalchemy>=2.0.30" alembic \
            "pydantic[email]>=2.7" pydantic-settings python-multipart pyjwt

mkdir -p var
export METREO_DATABASE_URL="sqlite+pysqlite:///$PWD/var/dev.sqlite3"
export PYTHONPATH="$PWD/apps/api/src"

(cd apps/api && alembic -c alembic.ini upgrade head)   # migrations
(cd apps/api && python -m metreo_api.seed)             # jeu de démonstration fictif
python -m uvicorn metreo_api.main:app --reload --port 8000
```

```bash
# --- Web (dans un second terminal) ---------------------------------------
cd apps/web
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 npm run dev
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
# Domaine (61 tests, aucune dépendance)
python -m pytest packages/domain/tests -q

# API (86 tests, base SQLite créée par les migrations réelles)
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
