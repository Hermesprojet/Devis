#!/usr/bin/env bash
# Restaure une archive dans un environnement JETABLE, puis contrôle.
#
#   ops/restaurer.sh archive.tar.gz[.age] [nom-de-la-base-jetable]
#
# Ce script ne restaure JAMAIS par-dessus une base existante. Il crée la
# sienne, dont le nom doit porter un marqueur de jetabilité. Une restauration
# est un exercice ; l'exercer sur la base de production serait l'incident
# qu'on cherche à savoir traiter.
set -euo pipefail

ARCHIVE="${1:?usage: ops/restaurer.sh <archive> [base-jetable]}"
CIBLE="${2:-metreo_restore_$(date -u +%Y%m%d%H%M%S)}"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$CIBLE" in
  *restore*|*scratch*|*jetable*|*tmp*) : ;;
  *)
    echo "refus : « $CIBLE » ne porte aucun marqueur de jetabilité." >&2
    echo "  Attendu : un nom contenant restore, scratch, jetable ou tmp." >&2
    exit 1 ;;
esac

TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT

if [[ "$ARCHIVE" == *.age ]]; then
  command -v age >/dev/null || { echo "age est requis pour déchiffrer." >&2; exit 1; }
  age --decrypt -i "${BACKUP_AGE_IDENTITY:?BACKUP_AGE_IDENTITY est requis}" \
      -o "$TRAVAIL/archive.tar.gz" "$ARCHIVE"
  ARCHIVE="$TRAVAIL/archive.tar.gz"
fi

tar -C "$TRAVAIL" -xzf "$ARCHIVE"
DUMP="$(find "$TRAVAIL" -name '*.dump' | head -1)"
STOCKAGE="$(find "$TRAVAIL" -name '*-storage.tar' | head -1)"
[[ -s "$DUMP" ]] || { echo "archive sans dump exploitable." >&2; exit 1; }

# Deux façons de restaurer, et le choix se fait sur une seule variable.
#
# SANS RESTORE_COMPOSE_PROJECT — la voie d'origine : un PostgreSQL joignable
# depuis cette machine, et un Python local portant l'application. C'est la voie
# de l'exercice sur poste de travail.
#
# AVEC RESTORE_COMPOSE_PROJECT — la voie d'une PILE : tout se passe dans les
# conteneurs du projet nommé. Elle existe parce que la première ne pouvait pas
# restaurer une préproduction : cette composition NE PUBLIE PAS le port de sa
# base, délibérément. La procédure documentée ne fonctionnait donc pas contre
# la pile pour laquelle elle était écrite.
if [[ -n "${RESTORE_COMPOSE_PROJECT:-}" ]]; then
  read -r -a FICHIERS_COMPOSE <<< "${RESTORE_COMPOSE_FILES:--f $RACINE/infra/docker-compose.staging.yml}"
  CIBLE_COMPOSE=(docker compose --project-name "$RESTORE_COMPOSE_PROJECT" "${FICHIERS_COMPOSE[@]}")
  [[ -n "${RESTORE_ENV_FILE:-}" ]] && CIBLE_COMPOSE+=(--env-file "$RESTORE_ENV_FILE")

  UTILISATEUR="${POSTGRES_USER:?POSTGRES_USER est requis pour la voie compose}"
  BASE_CIBLE="${POSTGRES_DB:?POSTGRES_DB est requis pour la voie compose}"

  echo "→ restauration dans la pile « $RESTORE_COMPOSE_PROJECT »"

  # La base cible est recréée : restaurer par-dessus des tables existantes
  # laisserait un mélange des deux états, indétectable ensuite.
  "${CIBLE_COMPOSE[@]}" exec -T db psql -U "$UTILISATEUR" -d postgres \
    -c "DROP DATABASE IF EXISTS $CIBLE" >/dev/null
  "${CIBLE_COMPOSE[@]}" exec -T db psql -U "$UTILISATEUR" -d postgres \
    -c "CREATE DATABASE $CIBLE" >/dev/null
  "${CIBLE_COMPOSE[@]}" exec -T db pg_restore --no-owner --no-privileges \
    -U "$UTILISATEUR" -d "$CIBLE" < "$DUMP"

  echo "→ stockage des fichiers"
  if [[ -n "$STOCKAGE" ]]; then
    "${CIBLE_COMPOSE[@]}" exec -T api sh -c \
      'mkdir -p "${METREO_STORAGE_ROOT:-/var/lib/metreo}" && tar -C "${METREO_STORAGE_ROOT:-/var/lib/metreo}" -xf -' \
      < "$STOCKAGE"
  fi

  echo "→ migrations et contrôles, dans le conteneur applicatif"
  URL_CIBLE="postgresql+psycopg://$UTILISATEUR:${POSTGRES_PASSWORD:-}@db:5432/$CIBLE"
  "${CIBLE_COMPOSE[@]}" exec -T -e METREO_DATABASE_URL="$URL_CIBLE" api \
    alembic -c apps/api/alembic.ini upgrade head
  # Le contrôle est passé sur l'entrée standard : l'image applicative ne
  # contient pas ops/, et la monter juste pour cela ajouterait un point de
  # montage au seul bénéfice d'un script de vérification.
  "${CIBLE_COMPOSE[@]}" exec -T -e METREO_DATABASE_URL="$URL_CIBLE" api \
    python - < "$RACINE/ops/verifier_restauration.py"

  echo
  echo "restauration vérifiée dans la base « $CIBLE » de « $RESTORE_COMPOSE_PROJECT »"
  echo "Cette pile est jetable : démontez-la avec « down --volumes »."
  exit 0
fi

: "${PGHOST:=localhost}" "${PGUSER:=metreo}" "${PGPORT:=5432}"
export PGHOST PGUSER PGPORT

echo "→ base jetable $CIBLE"
psql -d postgres -c "CREATE DATABASE $CIBLE" >/dev/null
pg_restore --no-owner --no-privileges -d "$CIBLE" "$DUMP"

echo "→ stockage des fichiers"
DESTINATION="${RESTORE_STORAGE_DIR:-$TRAVAIL/storage}"
mkdir -p "$DESTINATION"
[[ -n "$STOCKAGE" ]] && tar -C "$DESTINATION" -xf "$STOCKAGE"

echo "→ migrations et contrôles"
export METREO_DATABASE_URL="postgresql+psycopg://$PGUSER@$PGHOST:$PGPORT/$CIBLE"
export METREO_STORAGE_ROOT="$DESTINATION"
cd "$RACINE/apps/api"
PYTHONPATH=src "${PYTHON:-python3}" -m alembic -c alembic.ini upgrade head
cd "$RACINE"
PYTHONPATH="apps/api/src" "${PYTHON:-python3}" ops/verifier_restauration.py

echo
echo "restauration vérifiée dans $CIBLE (stockage : $DESTINATION)"
echo "Pour la supprimer :  psql -d postgres -c 'DROP DATABASE $CIBLE'"
