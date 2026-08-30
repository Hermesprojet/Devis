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
