#!/usr/bin/env bash
# La pile de l'essai sur plans réels : sur CETTE machine, pour CET essai, et rien d'autre.
#
#     ops/essai_local.sh up        monter (migrations, amorçage, construction web, API, web)
#     ops/essai_local.sh status    état des processus, des sondes et du dossier privé
#     ops/essai_local.sh journal   les dernières lignes des journaux API et web
#     ops/essai_local.sh down      arrêter en CONSERVANT la base, le stockage et les résultats
#     ops/essai_local.sh effacer --confirmer
#                                  détruire base, stockage, résultats et état — JAMAIS les plans
#
# **Pourquoi un script de plus, alors que `ops/demonstration.sh` monte déjà une
# pile locale.** La démonstration tourne dans Docker, charge un jeu fictif et
# stocke les pièces dans un volume que `reset` efface. L'essai sur plans réels
# a trois exigences qu'elle ne porte pas :
#
#  1. **les plans restent hors du dépôt et hors de tout artefact de CI.** Tout
#     ce que la pile écrit — base SQLite, pièces déposées, tuiles rendues,
#     comptes rendus, journaux — vit dans UN dossier privé en droits 700, hors
#     de l'arbre du dépôt. Le script refuse un dossier privé qui serait dans
#     le dépôt, et `status` signale tout plan qui s'y serait glissé ;
#  2. **la pile éprouve l'arbre de travail**, c'est-à-dire exactement le SHA
#     qu'on vient de vérifier — pas une image reconstruite ailleurs. Mêmes
#     sources, même interpréteur, même recette que `apps/web/e2e-premier-devis/banc.ts` ;
#  3. **rien ne demande Docker ni le réseau** : `127.0.0.1` seulement, ports
#     propres à cet essai, connexion de développement sans mot de passe. La
#     pile n'est donc jamais joignable d'une autre machine.
#
# Ce que cette pile N'EST PAS : la préproduction. Elle ne dit rien du VPS, de
# l'image Docker ni de PostgreSQL. Ce qu'elle établit, c'est la justesse de la
# mesure sur un vrai dessin et la tenue de la quantité jusqu'au PDF du devis.
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOSSIER_API="$RACINE/apps/api"
DOSSIER_WEB="$RACINE/apps/web"

# Le dossier privé. Surchargeable, parce que le disque qui porte les plans
# n'est pas toujours celui du dépôt — mais JAMAIS dans le dépôt (contrôlé), et
# jamais un chemin de premier niveau que « effacer » pourrait vider.
if [[ -z "${METREO_ESSAI_DIR:-}" && -z "${HOME:-}" ]]; then
	echo "Ni METREO_ESSAI_DIR ni HOME ne sont définis : impossible de choisir le dossier privé." >&2
	exit 1
fi
PRIVE="${METREO_ESSAI_DIR:-${HOME:?}/.metreo-essai}"

# Des ports à part : 8000/3000 (démonstration), 8011/3011 (parcours web),
# 8041/3041 (première organisation) et 8055/3055 (captures) sont déjà pris
# par d'autres recettes, qui peuvent tourner en même temps.
PORT_API="${METREO_ESSAI_PORT_API:-8071}"
PORT_WEB="${METREO_ESSAI_PORT_WEB:-3071}"
URL_API="http://127.0.0.1:$PORT_API/api/v1"
URL_WEB="http://127.0.0.1:$PORT_WEB"

# L'organisation et l'administrateur de l'essai. `.example` et non `.invalid` :
# l'amorçage applique le même contrôle d'adresse que la connexion, et refuse
# les domaines réservés (voir docs/AUTHENTIFICATION.md).
ORGANISATION="${METREO_ESSAI_ORGANISATION:-Essai sur plans réels (organisation fictive)}"
ADMIN="${METREO_ESSAI_ADMIN:-admin@essai.example}"

PYTHON="${METREO_PYTHON:-$RACINE/.venv/bin/python}"

BASE="$PRIVE/base/essai.sqlite3"
STOCKAGE="$PRIVE/stockage"
PLANS="$PRIVE/plans"
RESULTATS="$PRIVE/resultats"
ETAT="$PRIVE/etat"
SECRET="$ETAT/secret-de-session"

avertissement() {
	cat <<TXT

  ┌──────────────────────────────────────────────────────────────────────┐
  │  ESSAI LOCAL — NE JAMAIS EXPOSER, NE JAMAIS COMMITER                  │
  │                                                                      │
  │  Connexion de développement sans mot de passe, ports sur 127.0.0.1   │
  │  seulement. Tout ce que la pile écrit vit dans le dossier privé :    │
  │    $PRIVE
  │  Les plans y restent. Rien n'en sort vers le dépôt ni vers la CI.    │
  └──────────────────────────────────────────────────────────────────────┘

TXT
}

# Le dossier privé ne peut pas être dans le dépôt : `git add -A` emporterait
# les plans. C'est la garde la plus importante du fichier. Et il doit avoir au
# moins deux niveaux : « effacer » ne videra jamais un répertoire de premier
# niveau.
# Résolu SANS le créer : un dossier refusé ne doit pas avoir été fabriqué
# avant d'être refusé — dans le dépôt, il y resterait. Python plutôt que
# `realpath -m`, qui manque au macOS d'origine ; l'interpréteur est de toute
# façon exigé.
dossier_prive_resolu() {
	"$PYTHON" -c 'import os, sys; print(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$PRIVE"
}

refuser_un_dossier_dangereux() {
	local prive_absolu
	prive_absolu="$(dossier_prive_resolu)"
	case "$prive_absolu/" in
	"$RACINE"/*)
		echo "Le dossier privé « $prive_absolu » est DANS le dépôt. Un plan déposé là" >&2
		echo "finirait dans un commit. Choisissez un dossier hors du dépôt :" >&2
		echo "    METREO_ESSAI_DIR=\$HOME/.metreo-essai ops/essai_local.sh up" >&2
		exit 1
		;;
	esac
	if [[ ! "$prive_absolu" =~ ^/[^/]+/[^/]+ ]]; then
		echo "Le dossier privé « $prive_absolu » est trop haut dans l'arborescence : refusé." >&2
		exit 1
	fi
}

exiger_les_outils() {
	if [[ ! -x "$PYTHON" ]]; then
		echo "Interpréteur introuvable : $PYTHON" >&2
		echo "Créez l'environnement (make install) ou désignez-le : METREO_PYTHON=/chemin/python" >&2
		exit 1
	fi
	if [[ ! -d "$DOSSIER_WEB/node_modules" ]]; then
		echo "Dépendances web absentes : lancez « npm ci » dans apps/web, puis relancez." >&2
		exit 1
	fi
	for outil in curl npx; do
		if ! command -v "$outil" >/dev/null 2>&1; then
			echo "Outil manquant : $outil" >&2
			exit 1
		fi
	done
}

# Tout en 700 : les plans sont des pièces confidentielles d'un dossier client.
preparer_les_dossiers() {
	umask 077
	mkdir -p "$PRIVE" "$PRIVE/base" "$STOCKAGE" "$PLANS" "$RESULTATS" "$ETAT"
	chmod 700 "$PRIVE" "$PRIVE/base" "$STOCKAGE" "$PLANS" "$RESULTATS" "$ETAT"
	if [[ ! -s "$SECRET" ]]; then
		# Un secret de session propre à cette machine, jamais versionné. Il ne
		# protège rien d'autre que la pile locale, mais un secret vide ferait
		# dépendre la validité des sessions d'un défaut de l'application.
		"$PYTHON" -c 'import secrets; print(secrets.token_hex(32))' >"$SECRET"
		chmod 600 "$SECRET"
	fi
}

environnement_api() {
	export METREO_DATABASE_URL="sqlite+pysqlite:///$BASE"
	export METREO_ENVIRONMENT=development
	export METREO_AUTH_MODE=dev
	METREO_JWT_SECRET="$(cat "$SECRET")"
	export METREO_JWT_SECRET
	export METREO_CORS_ORIGINS="$URL_WEB"
	export METREO_STORAGE_ROOT="$STOCKAGE"
	# Les sources du dépôt, et non seulement le paquet installé : la pile doit
	# éprouver ce que porte l'arbre de travail.
	export PYTHONPATH="$DOSSIER_API/src:$RACINE/packages/domain/src:$RACINE/packages/contracts/src"
	# `METREO_MAX_UPLOAD_BYTES` (25 Mio) et `METREO_PLAN_SYNC_MAX_BYTES` (12 Mio)
	# gardent leurs défauts ; un plan réel plus lourd est refusé en le disant.
	# Pour les relever LE TEMPS DE L'ESSAI, posez-les dans l'environnement
	# avant `up` : elles passent telles quelles.
}

vivant() {
	local fichier_pid="$1"
	[[ -s "$fichier_pid" ]] && kill -0 "$(cat "$fichier_pid")" 2>/dev/null
}

attendre() {
	local libelle="$1" url="$2" attendu="$3" limite="${4:-180}"
	local i code=000
	printf '  %-34s' "$libelle"
	for ((i = 0; i < limite; i++)); do
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || echo 000)
		if [[ "$code" == "$attendu" ]]; then
			echo "prêt"
			return 0
		fi
		sleep 1
	done
	echo "PAS PRÊT (dernier code : $code)"
	return 1
}

# Le paquet web porte l'URL de l'API COMPILÉE : un paquet construit pour un
# autre port appellerait une API qui n'écoute pas ici. On ne fait confiance à
# aucune marque : on lit le paquet lui-même.
web_construit_pour_cette_api() {
	[[ -f "$DOSSIER_WEB/.next/BUILD_ID" ]] && grep -rqs -- "$URL_API" "$DOSSIER_WEB/.next/static"
}

construire_le_web() {
	if web_construit_pour_cette_api; then
		echo "Paquet web déjà construit pour $URL_API"
		return 0
	fi
	echo "Construction du paquet web pour $URL_API (quelques minutes)…"
	(cd "$DOSSIER_WEB" && NEXT_PUBLIC_API_URL="$URL_API" npx next build)
}

# Chaque serveur dans son propre groupe de processus : `next start` engendre
# un serveur enfant, et le tuer seul laisserait le port pris.
demarrer() {
	local nom="$1"
	shift
	setsid "$@" >"$ETAT/$nom.log" 2>&1 &
	echo $! >"$ETAT/$nom.pid"
}

arreter() {
	local nom="${1:?}" pid
	if [[ -s "$ETAT/$nom.pid" ]]; then
		pid="$(cat "$ETAT/$nom.pid")"
		kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
		rm -f "${ETAT:?}/${nom:?}.pid"
	fi
}

commande_up() {
	refuser_un_dossier_dangereux
	exiger_les_outils
	preparer_les_dossiers
	if vivant "$ETAT/api.pid" || vivant "$ETAT/web.pid"; then
		echo "La pile tourne déjà : ops/essai_local.sh status" >&2
		exit 1
	fi
	avertissement
	environnement_api

	echo "Migrations…"
	(cd "$DOSSIER_API" && "$PYTHON" -m alembic -c "$DOSSIER_API/alembic.ini" upgrade head)

	# L'amorçage, et RIEN d'autre : pas de jeu de démonstration. Idempotent.
	echo "Amorçage…"
	(cd "$DOSSIER_API" && "$PYTHON" -m metreo_api.bootstrap \
		--organization "$ORGANISATION" --admin-email "$ADMIN" --admin-name "Essai")

	construire_le_web

	echo "Démarrage…"
	(cd "$DOSSIER_API" && demarrer api "$PYTHON" -m uvicorn metreo_api.main:app \
		--port "$PORT_API" --host 127.0.0.1)
	(cd "$DOSSIER_WEB" && demarrer web npx next start -p "$PORT_WEB" -H 127.0.0.1)

	echo
	echo "Attente des services :"
	attendre "API (base joignable)" "$URL_API/ready" 200
	attendre "interface web" "$URL_WEB" 200

	cat <<TXT

  Ouvrez : $URL_WEB
  Connexion (sans mot de passe) : $ADMIN

  Déposez vos plans dans : $PLANS
  Les comptes rendus arrivent dans : $RESULTATS

  Repérer les cotes, puis jouer l'essai :
    $PYTHON scripts/essai_sur_plans_reels.py deposer --base $URL_API --email $ADMIN --fichier $PLANS/<plan>.pdf
    $PYTHON scripts/essai_sur_plans_reels.py textes  --base $URL_API --email $ADMIN --document <id> --revision <id> --motif '^[0-9]{3,5}$'
    $PYTHON scripts/essai_sur_plans_reels.py tuile   --base $URL_API --email $ADMIN --document <id> --revision <id> --x 0.5 --y 0.5 --sortie $RESULTATS/zone.png
    METREO_ESSAI_PLAN=$PLANS/essai.json METREO_ESSAI_CAPTURES=$RESULTATS/captures \\
      npx --prefix apps/web playwright test --config=apps/web/playwright.essai.config.ts
    $PYTHON scripts/essai_sur_plans_reels.py rapport --base $URL_API --email $ADMIN --plan $PLANS/rapport.json --resultats $RESULTATS/compte-rendu

  ops/essai_local.sh status   ·   ops/essai_local.sh down
TXT
}

commande_status() {
	echo "Dossier privé : $PRIVE"
	if [[ ! -d "$PRIVE" ]]; then
		echo "  absent — la pile n'a jamais été montée ici"
		return 0
	fi
	echo
	echo "Processus :"
	for nom in api web; do
		if vivant "$ETAT/$nom.pid"; then
			printf '  %-6s pid %s\n' "$nom" "$(cat "$ETAT/$nom.pid")"
		else
			printf '  %-6s arrêté\n' "$nom"
		fi
	done
	echo
	echo "Sondes :"
	for sonde in live ready health; do
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_API/$sonde" 2>/dev/null || echo 000)
		printf '  %-8s %s\n' "$sonde" "$code"
	done
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_WEB" 2>/dev/null || echo 000)
	printf '  %-8s %s\n' "web" "$code"
	echo
	echo "Contenu :"
	printf '  %-12s %s fichier(s)\n' "plans" "$(find "$PLANS" -type f 2>/dev/null | wc -l | tr -d ' ')"
	printf '  %-12s %s fichier(s)\n' "stockage" "$(find "$STOCKAGE" -type f 2>/dev/null | wc -l | tr -d ' ')"
	printf '  %-12s %s essai(s)\n' "résultats" "$(find "$RESULTATS" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
	printf '  %-12s %s\n' "droits" "$(stat -c '%a' "$PRIVE" 2>/dev/null || stat -f '%Lp' "$PRIVE")"
	echo
	# Le contrôle qui compte : aucun plan ne s'est glissé dans le dépôt. Les
	# fixtures fabriquées sont ignorées par Git et n'apparaissent pas ici.
	local egares
	egares="$(git -C "$RACINE" status --porcelain --untracked-files=all 2>/dev/null \
		| grep -iE '\.(pdf|dxf|dwg)$' || true)"
	if [[ -n "$egares" ]]; then
		echo "ATTENTION — des fichiers de plan sont dans l'arbre du dépôt, non ignorés :"
		echo "$egares" | sed 's/^/    /'
		echo "  Déplacez-les dans $PLANS avant tout commit."
	else
		echo "Dépôt : aucun fichier de plan non suivi ni modifié."
	fi
	if [[ -d "$DOSSIER_API/var/storage" ]] && [[ -n "$(find "$DOSSIER_API/var/storage" -type f 2>/dev/null | head -1)" ]]; then
		echo "ATTENTION — apps/api/var/storage porte des fichiers : une pile a tourné sans METREO_STORAGE_ROOT."
	fi
}

commande_journal() {
	for nom in api web; do
		echo "=== $nom ($ETAT/$nom.log) ==="
		tail -n 40 "$ETAT/$nom.log" 2>/dev/null || echo "  (aucun journal)"
		echo
	done
}

commande_down() {
	arreter api
	arreter web
	echo "Pile arrêtée. Base, stockage, plans et résultats conservés dans $PRIVE"
}

commande_effacer() {
	refuser_un_dossier_dangereux
	local prive_absolu
	prive_absolu="$(dossier_prive_resolu)"
	if [[ "${1:-}" != "--confirmer" ]]; then
		echo "Ce que « effacer --confirmer » détruirait :"
		for dossier in "$prive_absolu/base" "$prive_absolu/stockage" "$prive_absolu/resultats" "$prive_absolu/etat"; do
			[[ -d "$dossier" ]] && du -sh "$dossier" 2>/dev/null | sed 's/^/  /'
		done
		echo
		echo "Et ce qu'il ne touche JAMAIS : $prive_absolu/plans (vos plans ; à vous de les retirer)."
		echo "Relancez avec --confirmer pour effacer."
		return 1
	fi
	commande_down
	# Quatre sous-dossiers nommés un par un, sous un dossier déjà contrôlé —
	# jamais le dossier privé lui-même, qui porte les plans.
	rm -rf "${prive_absolu:?}/base" "${prive_absolu:?}/stockage" "${prive_absolu:?}/resultats" "${prive_absolu:?}/etat"
	echo "Base, stockage, résultats et état effacés. Les plans restent dans $prive_absolu/plans."
}

case "${1:-}" in
up) commande_up ;;
status) commande_status ;;
journal) commande_journal ;;
down) commande_down ;;
effacer) commande_effacer "${2:-}" ;;
*)
	echo "Usage : ops/essai_local.sh up | status | journal | down | effacer --confirmer" >&2
	exit 2
	;;
esac
