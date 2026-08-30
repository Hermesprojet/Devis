#!/usr/bin/env bash
# Sauvegarde la base et le stockage des fichiers, chiffre, et dépose.
#
#   ops/sauvegarder.sh [répertoire-de-sortie]
#
# Ce que ce script refuse de faire, et pourquoi :
#
#   - il ne sauvegarde pas une base qu'il n'a pas pu joindre. Une archive vide
#     déposée sans erreur est pire que pas d'archive : on croit être protégé.
#   - il refuse de DÉPOSER une archive non chiffrée chez un tiers. Un dump
#     PostgreSQL en clair chez un hébergeur, c'est la base entière. Sans
#     destination distante, une archive locale en clair reste permise — elle
#     ne quitte pas la machine, et l'exiger chiffrée bloquerait l'exercice de
#     restauration sans rien protéger.
#   - il n'écrase jamais une archive existante : le nom porte l'horodatage.
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SORTIE="${1:-${BACKUP_DIR:-$RACINE/var/sauvegardes}}"
# Quelle pile sauvegarder. Par défaut celle de préproduction, décrite par le
# seul fichier de composition ; une répétition ou une seconde instance passent
# les leurs.
#
# Sans ces variables, le script visait le projet « metreo-staging » et
# échouait à résoudre `WEB_IMAGE` — il ne pouvait donc sauvegarder aucune pile
# portant un autre nom, y compris celle qu'on monte pour vérifier qu'il marche.
read -r -a FICHIERS_COMPOSE <<< "${BACKUP_COMPOSE_FILES:--f $RACINE/infra/docker-compose.staging.yml}"
COMPOSE=(docker compose)
[[ -n "${BACKUP_COMPOSE_PROJECT:-}" ]] && COMPOSE+=(--project-name "$BACKUP_COMPOSE_PROJECT")
COMPOSE+=("${FICHIERS_COMPOSE[@]}")

ENV_COMPOSE="${BACKUP_ENV_FILE:-$RACINE/infra/staging.env}"
if [[ -f "$ENV_COMPOSE" ]]; then
  COMPOSE+=(--env-file "$ENV_COMPOSE")
  # shellcheck disable=SC1091
  set -a; . "$ENV_COMPOSE"; set +a
fi

HORODATAGE="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$SORTIE"
BASE="$SORTIE/metreo-$HORODATAGE"

echo "→ base de données"
# `--format=custom` : restaurable sélectivement, et compressé. `pg_dumpall`
# aurait embarqué les rôles du serveur, qui ne nous appartiennent pas.
"${COMPOSE[@]}" exec -T db \
  pg_dump --format=custom --no-owner --no-privileges \
  -U "${POSTGRES_USER:?}" "${POSTGRES_DB:?}" > "$BASE.dump"

if [[ ! -s "$BASE.dump" ]]; then
  echo "sauvegarde refusée : le dump est vide." >&2
  rm -f "$BASE.dump"
  exit 1
fi

echo "→ stockage des fichiers"
# Le volume est lu depuis un conteneur jetable : le service n'a pas à être
# arrêté, et rien n'est monté sur la machine hôte.
"${COMPOSE[@]}" run --rm --no-deps --entrypoint sh api \
  -c 'tar -C /var/lib/metreo -cf - .' > "$BASE-storage.tar"

echo "→ archive"
tar -C "$SORTIE" -czf "$BASE.tar.gz" \
  "$(basename "$BASE.dump")" "$(basename "$BASE-storage.tar")"
rm -f "$BASE.dump" "$BASE-storage.tar"

ARCHIVE="$BASE.tar.gz"
if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
  echo "→ chiffrement"
  command -v age >/dev/null || { echo "age est requis pour chiffrer." >&2; exit 1; }
  age -r "$BACKUP_AGE_RECIPIENT" -o "$ARCHIVE.age" "$ARCHIVE"
  rm -f "$ARCHIVE"
  ARCHIVE="$ARCHIVE.age"
else
  echo "! aucun BACKUP_AGE_RECIPIENT : archive NON chiffrée." >&2
fi

if [[ -n "${BACKUP_DESTINATION:-}" ]]; then
  # REFUS, et non un avertissement. Une archive en clair déposée chez un tiers,
  # c'est la base entière — tous les clients, tous les prix, tous les devis —
  # lisible par quiconque atteint ce dépôt.
  #
  # Ce refus corrige un écart réel : le message ci-dessus annonçait
  # « locale seulement », puis ce bloc envoyait quand même l'archive en clair.
  # La phrase rassurait sur ce qui n'arrivait pas.
  if [[ -z "${BACKUP_AGE_RECIPIENT:-}" ]]; then
    echo "sauvegarde refusée : dépôt vers un tiers demandé sans chiffrement." >&2
    echo "  BACKUP_DESTINATION est posée, BACKUP_AGE_RECIPIENT ne l'est pas." >&2
    echo "  L'archive reste sur cette machine : $ARCHIVE" >&2
    exit 1
  fi
  echo "→ dépôt vers $BACKUP_DESTINATION"
  case "$BACKUP_DESTINATION" in
    s3://*) aws s3 cp "$ARCHIVE" "$BACKUP_DESTINATION/" ;;
    *)      rsync -a "$ARCHIVE" "$BACKUP_DESTINATION/" ;;
  esac
else
  echo "! aucune BACKUP_DESTINATION : la sauvegarde reste sur cette machine." >&2
  echo "  Une sauvegarde qui vit sur la machine sauvegardée ne protège de rien." >&2
fi

echo "sauvegarde terminée : $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
