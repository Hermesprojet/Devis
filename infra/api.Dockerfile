# Image de l'API, en deux étapes.
#
# L'étape de construction porte pip, les en-têtes de compilation et les
# fichiers de projet ; l'étape finale ne reçoit que l'environnement virtuel
# résultant et le code. Les outils de construction ne survivent pas au
# passage, ce qui réduit la surface de l'image livrée.

FROM python:3.11-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY packages/domain /src/packages/domain
COPY apps/api /src/apps/api
COPY constraints/api.txt /src/constraints/api.txt

# Installation par les manifestes, sous contrainte du verrou de résolution.
# Surtout pas une liste de paquets écrite à la main : elle finirait par
# différer de pyproject.toml, et l'image validerait un jeu de dépendances qui
# n'est pas celui du dépôt — c'est précisément le défaut qui a laissé passer
# l'extra `email` manquant de pydantic.
RUN pip install --no-cache-dir -c /src/constraints/api.txt \
      /src/packages/domain "/src/apps/api[postgres]"

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq5 seul : le client PostgreSQL à l'exécution, sans sa chaîne de
# compilation.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --create-home --home-dir /home/metreo metreo

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=root:root packages/domain /app/packages/domain
COPY --chown=root:root apps/api /app/apps/api

ENV PYTHONPATH=/app/apps/api/src

# Le code appartient à root et l'application tourne sous `metreo` : le
# processus ne peut pas réécrire ses propres fichiers. Seul le répertoire de
# stockage est accessible en écriture, et il est monté depuis l'extérieur.
ENV METREO_STORAGE_ROOT=/var/lib/metreo
RUN mkdir -p /var/lib/metreo && chown metreo:metreo /var/lib/metreo && chmod 700 /var/lib/metreo

USER metreo

EXPOSE 8000

# Le point de santé n'interroge aucune dépendance externe : il dit que le
# processus répond, pas que la base est saine.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "metreo_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
