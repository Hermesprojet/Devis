# Metreo — commandes de développement et de vérification.
#
# `make verify` rejoue localement ce que la CI vérifie, dans le même ordre et
# sans rien masquer : chaque étape affiche sa commande et s'arrête au premier
# échec. C'est la commande à lancer avant d'annoncer une tranche terminée
# (voir .claude/skills/definition-of-done).

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
ALEMBIC := $(VENV)/bin/alembic

API_SRC := apps/api/src
DOMAIN := packages/domain
CONTRACTS := packages/contracts

# Base PostgreSQL pour les vérifications qui n'ont de sens que sur un vrai
# serveur. Vide, elles sont ignorées avec un message — jamais silencieusement.
METREO_TEST_DATABASE_URL ?=

.PHONY: help
help: ## Afficher les cibles disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -- installation ---------------------------------------------------------

.PHONY: install
install: ## Créer l'environnement virtuel et installer les dépendances
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	# Sous contrainte du verrou, comme l'image et la CI : sans cela, les tests
	# valident un jeu de versions qui n'est pas celui livré en production.
	# Les outils de développement n'y figurent pas — une contrainte ne force
	# aucune installation, elle ne fait que borner ce qui l'est.
	$(PIP) install -c constraints/api.txt -e $(DOMAIN) -e $(CONTRACTS) -e "apps/api[dev,postgres]"
	cd apps/web && npm ci

# -- vérifications élémentaires -------------------------------------------

.PHONY: format
format: ## Formater le code Python
	$(RUFF) format $(DOMAIN) $(CONTRACTS) apps/api/src apps/api/tests scripts

.PHONY: lint
lint: ## Format et lint Python, sans rien modifier
	$(RUFF) format --check $(DOMAIN) $(CONTRACTS) apps/api/src apps/api/tests scripts
	$(RUFF) check $(DOMAIN) $(CONTRACTS) apps/api/src apps/api/tests scripts

# scripts/ est inclus : il porte désormais de la logique — le contrôle
# d'installation propre et celui des skills — et non plus de simples README.
.PHONY: types
types: ## Vérification de types Python
	$(MYPY) $(DOMAIN)/src/metreo_domain
	$(MYPY) $(CONTRACTS)/src/metreo_contracts $(CONTRACTS)/tests
	$(MYPY) $(API_SRC)/metreo_api
	$(MYPY) scripts

.PHONY: test-domain
test-domain: ## Tests du moteur de calcul (aucune base requise)
	$(PY) -m pytest $(DOMAIN)/tests -q

.PHONY: test-contracts
test-contracts: ## Tests des contrats documentaires purs
	$(PY) -m pytest $(CONTRACTS)/tests -q

.PHONY: test-api
test-api: ## Tests de l'API sur SQLite
	@# METREO_TEST_DATABASE_URL est délibérément retirée : héritée de
	@# l'environnement ou de la ligne de commande de `make verify`, elle faisait
	@# tourner cette cible sur PostgreSQL. Le chemin SQLite n'était alors jamais
	@# vérifié, et la même suite tournait deux fois sur le même moteur.
	cd apps/api && env -u METREO_TEST_DATABASE_URL PYTHONPATH=src ../../$(PY) -m pytest -q

.PHONY: test-api-postgres
test-api-postgres: ## Tests de l'API sur PostgreSQL réel (METREO_TEST_DATABASE_URL)
	@if [ -z "$(METREO_TEST_DATABASE_URL)" ]; then \
		echo "IGNORÉ : METREO_TEST_DATABASE_URL n'est pas défini."; \
		echo "         SQLite ne prouve rien sur NUMERIC, les contraintes ni le DDL"; \
		echo "         transactionnel. Exemple :"; \
		echo "         make test-api-postgres METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo"; \
	else \
		cd apps/api && METREO_TEST_DATABASE_URL="$(METREO_TEST_DATABASE_URL)" \
			PYTHONPATH=src ../../$(PY) -m pytest -q; \
	fi

# Base sur laquelle migrations et seed agissent. Vide, elles retombent sur la
# configuration de l'application (.env, environnement) — ce qui est le défaut
# voulu pour un développeur, mais jamais pour release-gate, qui doit nommer
# explicitement sa base jetable.
METREO_DATABASE_URL ?=
ifneq ($(METREO_DATABASE_URL),)
DB_ENV := METREO_DATABASE_URL="$(METREO_DATABASE_URL)"
else
DB_ENV :=
endif

# Serveur PostgreSQL sur lequel `migration-roundtrip-test` CRÉE sa propre base.
# C'est une cible de connexion, jamais une cible de destruction : la base
# détruite est celle que le script vient de créer, et lui seul en connaît le
# nom.
METREO_ADMIN_DATABASE_URL ?=

.PHONY: migrate
migrate: ## Appliquer les migrations : upgrade head, non destructif
	@# Aucune protection nécessaire : `upgrade head` n'efface rien. C'est la
	@# commande normale, celle qu'on lance sur sa base de travail.
	cd apps/api && $(DB_ENV) PYTHONPATH=src ../../$(ALEMBIC) -c alembic.ini upgrade head

.PHONY: migration-roundtrip-test
migration-roundtrip-test: ## Aller-retour head → base → head, dans une base créée par ce run
	@# La cible publique destructive a été retirée. `downgrade base` supprime
	@# toutes les tables applicatives : aucune URL fournie par l'appelant n'est
	@# acceptée comme cible de destruction, quel que soit son nom. Le script
	@# crée sa propre base, au nom tiré au hasard, ne détruit que celle-là, et
	@# nettoie même en cas d'échec.
	@if [ -z "$(METREO_ADMIN_DATABASE_URL)" ]; then \
		echo "migration-roundtrip-test : refusé — METREO_ADMIN_DATABASE_URL est obligatoire." >&2; \
		echo "  C'est un serveur où ce test peut CRÉER une base, pas une base à détruire." >&2; \
		echo "  Exemple : make migration-roundtrip-test \\" >&2; \
		echo "    METREO_ADMIN_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/postgres" >&2; \
		exit 1; \
	fi
	$(PY) scripts/migration_roundtrip.py --admin-url "$(METREO_ADMIN_DATABASE_URL)" --seed

.PHONY: seed
seed: ## Charger le jeu de démonstration (entièrement fictif)
	cd apps/api && $(DB_ENV) PYTHONPATH=src ../../$(PY) -m metreo_api.seed

.PHONY: clean-install
clean-install: ## Prouver qu'une installation depuis les seuls manifestes démarre
	$(PY) scripts/check_clean_install.py --constraints constraints/api.txt

.PHONY: lock
lock: ## Régénérer constraints/api.txt depuis une résolution propre
	@tmp=$$(mktemp -d); \
	$(PY) -m venv $$tmp/venv; \
	$$tmp/venv/bin/pip install --quiet --upgrade pip; \
	$$tmp/venv/bin/pip install --quiet ./packages/domain ./packages/contracts "./apps/api[postgres]"; \
	{ echo "# Verrou de résolution des dépendances d'EXÉCUTION, régénéré par : make lock"; \
	  echo "#"; \
	  echo "# Ne contient délibérément pas les outils de développement (pytest, ruff,"; \
	  echo "# mypy). Les y inclure épinglait packaging et pathspec, qui contaminaient"; \
	  echo "# l'environnement isolé de construction de hatchling et rendaient la"; \
	  echo "# résolution impossible sur une machine sans cache de roues."; \
	  echo "#"; \
	  echo "# Les manifestes (pyproject.toml) restent la source de vérité des"; \
	  echo "# dépendances ; ce fichier ne fait que figer la résolution obtenue."; \
	  $$tmp/venv/bin/pip freeze --exclude-editable \
	    | grep -viE '^metreo-'; } > constraints/api.txt; \
	rm -rf $$tmp; \
	echo "constraints/api.txt régénéré."

.PHONY: skills
skills: ## Contrôler les skills du dépôt
	$(PY) scripts/check_skills.py

.PHONY: web-typecheck
web-typecheck: ## Vérification de types du front
	cd apps/web && npm run typecheck

.PHONY: web-build
web-build: ## Construction de production du front
	cd apps/web && npm run build

.PHONY: e2e
e2e: ## Parcours de bout en bout (Playwright)
	cd apps/web && npx playwright test

.PHONY: compose-config
compose-config: ## Valider la composition Docker
	docker compose -f infra/docker-compose.yml config >/dev/null && echo "docker compose : valide"

.PHONY: secrets
secrets: ## Refuser un .env versionné ou un motif de secret évident
	@if git ls-files | grep -E '(^|/)\.env$$'; then echo "Un .env est versionné." >&2; exit 1; fi
	@if git grep -nEI '(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' \
	     -- . ':!.github/workflows/ci.yml' ':!Makefile'; then \
		echo "Motif de secret détecté." >&2; exit 1; fi
	@echo "aucun secret évident"

# -- développement --------------------------------------------------------

DEV_DATABASE_URL ?= sqlite+pysqlite:///$(CURDIR)/var/dev.sqlite3
DEV_API_PORT ?= 8000

.PHONY: api-dev
api-dev: ## Migrations, jeu de démonstration, puis l'API en rechargement
	cd apps/api && METREO_DATABASE_URL="$(DEV_DATABASE_URL)" PYTHONPATH=src \
		../../$(ALEMBIC) -c alembic.ini upgrade head
	cd apps/api && METREO_DATABASE_URL="$(DEV_DATABASE_URL)" PYTHONPATH=src \
		../../$(PY) -m metreo_api.seed
	cd apps/api && METREO_DATABASE_URL="$(DEV_DATABASE_URL)" PYTHONPATH=src \
		../../$(PY) -m uvicorn metreo_api.main:app --reload --port $(DEV_API_PORT)

.PHONY: web-dev
web-dev: ## Le front en rechargement, branché sur l'API locale
	cd apps/web && NEXT_PUBLIC_API_URL=http://localhost:$(DEV_API_PORT)/api/v1 npm run dev

# -- la porte -------------------------------------------------------------

.PHONY: verify
verify: ## Tout vérifier, dans l'ordre de la CI, sans rien masquer
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory types
	@$(MAKE) --no-print-directory test-domain
	@$(MAKE) --no-print-directory test-contracts
	@$(MAKE) --no-print-directory test-api
	@$(MAKE) --no-print-directory test-api-postgres
	@$(MAKE) --no-print-directory clean-install
	@$(MAKE) --no-print-directory skills
	@$(MAKE) --no-print-directory secrets
	@$(MAKE) --no-print-directory compose-config
	@$(MAKE) --no-print-directory web-typecheck
	@$(MAKE) --no-print-directory web-build
	@echo
	@echo "verify : terminé. « make e2e » lance en plus les parcours navigateur,"
	@echo "         qui construisent le front et démarrent les deux serveurs."


.PHONY: release-gate
release-gate: ## La porte stricte : rien d'ignoré, PostgreSQL jetable obligatoire
	@# `verify` seul ne suffit pas à conclure : il ne lance ni les migrations,
	@# ni le seed, ni les parcours navigateur, et `test-api-postgres` sort en
	@# succès quand l'URL manque — un silence qui ressemble à un succès.
	@# Cette cible refuse de démarrer sans base, et n'ignore rien.
	@if [ -z "$(METREO_TEST_DATABASE_URL)" ]; then \
		echo "release-gate : refusé — METREO_TEST_DATABASE_URL est obligatoire." >&2; \
		echo "  SQLite ne prouve rien sur NUMERIC, les contraintes, le DDL" >&2; \
		echo "  transactionnel ni les verrous de ligne. Exemple :" >&2; \
		echo "  make release-gate METREO_TEST_DATABASE_URL=postgresql+psycopg://metreo:metreo@localhost:5432/metreo_gate" >&2; \
		exit 1; \
	fi
	@# La suite de tests crée et détruit des SCHÉMAS dans cette base — un par
	@# test. Le contrôle de nom reste utile à ce titre, mais il n'autorise plus
	@# aucun `downgrade base` : l'aller-retour a sa propre base.
	@$(PY) scripts/check_disposable_database.py \
		"$(METREO_TEST_DATABASE_URL)" --label "make release-gate"
	@$(MAKE) --no-print-directory verify METREO_TEST_DATABASE_URL="$(METREO_TEST_DATABASE_URL)"
	@# L'aller-retour des migrations ne touche PAS la base fournie : le script
	@# crée la sienne sur le même serveur, et ne détruit que celle-là. Un nom
	@# rassurant n'est pas une preuve qu'une base est jetable — « metreo_gate »
	@# peut parfaitement désigner une base qui compte.
	@$(MAKE) --no-print-directory migration-roundtrip-test \
		METREO_ADMIN_DATABASE_URL="$(METREO_TEST_DATABASE_URL)"
	@# Le seed écrit dans la base fournie : il faut donc qu'elle porte le
	@# schéma. L'aller-retour ne le pose plus — il travaille dans la sienne —
	@# et la porte ne passait que grâce au schéma laissé par une commande
	@# antérieure. Sur une base réellement neuve, elle tombait sur
	@# « relation "organizations" does not exist ». `upgrade head` ne détruit rien.
	@$(MAKE) --no-print-directory migrate \
		METREO_DATABASE_URL="$(METREO_TEST_DATABASE_URL)"
	@$(MAKE) --no-print-directory seed \
		METREO_DATABASE_URL="$(METREO_TEST_DATABASE_URL)"
	@$(MAKE) --no-print-directory e2e
	@echo
	@echo "release-gate : tout est passé, rien n'a été ignoré."
