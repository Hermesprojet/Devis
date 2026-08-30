#!/usr/bin/env bash
# Contrôle de disponibilité, à lancer depuis l'extérieur du déploiement.
#
# Trois questions, dans cet ordre, parce que la réponse à chacune change la
# lecture de la suivante :
#
#   1. le processus répond-il ?            -> /live
#   2. sert-il vraiment ?                  -> /health (touche la base)
#   3. le front est-il servi ?             -> /
#
# Un `/health` en échec pendant que `/live` répond dit « la base est
# injoignable », pas « l'application est morte ». C'est la distinction qui
# évite de redémarrer des conteneurs sains pendant un incident de base.
#
# Code de sortie : 0 tout va bien, 1 dégradé, 2 indisponible. Pensé pour cron,
# une sonde externe, ou une exécution à la main pendant un incident.

set -uo pipefail

BASE="${1:-${METREO_BASE_URL:-}}"
if [[ -z "$BASE" ]]; then
	echo "usage: $0 https://app.exemple.invalid" >&2
	exit 2
fi
BASE="${BASE%/}"

DELAI="${METREO_TIMEOUT:-10}"
# Le seuil au-delà duquel une réponse, même juste, est un symptôme.
SEUIL_LENT_MS="${METREO_SEUIL_LENT_MS:-2000}"

etat=0

# Rend « code_http temps_en_ms ». Aucun corps n'est affiché : une réponse
# d'erreur peut porter des détails qui n'ont rien à faire dans un journal de
# supervision.
interroger() {
	curl -sS -o /dev/null \
		--max-time "$DELAI" \
		-w '%{http_code} %{time_total}' \
		"$1" 2>/dev/null || echo "000 0"
}

controler() {
	local libelle="$1" url="$2" attendu="$3"
	local reponse code secondes ms
	reponse=$(interroger "$url")
	code="${reponse%% *}"
	secondes="${reponse##* }"
	ms=$(awk -v s="$secondes" 'BEGIN { printf "%.0f", s * 1000 }')

	if [[ "$code" == "000" ]]; then
		printf '%-28s INJOIGNABLE  (delai %ss)\n' "$libelle" "$DELAI"
		return 2
	fi
	if [[ "$code" != "$attendu" ]]; then
		printf '%-28s HTTP %s        (%s ms)\n' "$libelle" "$code" "$ms"
		return 2
	fi
	if (( ms > SEUIL_LENT_MS )); then
		printf '%-28s LENT  HTTP %s  (%s ms, seuil %s)\n' "$libelle" "$code" "$ms" "$SEUIL_LENT_MS"
		return 1
	fi
	printf '%-28s ok    HTTP %s  (%s ms)\n' "$libelle" "$code" "$ms"
	return 0
}

echo "Disponibilité de $BASE"

controler "processus (/live)" "$BASE/api/v1/live" 200 || etat=$?

# `/health` peut répondre 200 en se déclarant `degraded` : le code HTTP seul ne
# suffit donc pas, il faut lire ce qu'il dit de lui-même.
sante=$(curl -sS --max-time "$DELAI" "$BASE/api/v1/health" 2>/dev/null || echo '')
resultat=0
controler "service  (/health)" "$BASE/api/v1/health" 200 || resultat=$?
(( resultat > etat )) && etat=$resultat

if [[ -n "$sante" ]]; then
	statut=$(printf '%s' "$sante" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
	base=$(printf '%s' "$sante" | sed -n 's/.*"database"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
	printf '%-28s statut=%s base=%s\n' "  déclaré par le service" "${statut:-?}" "${base:-?}"
	if [[ "$statut" != "ok" ]]; then
		# Les problèmes de configuration sont nommés par le service, et ne
		# contiennent jamais de valeur — seulement des noms de réglages.
		printf '%s\n' "$sante" | tr ',' '\n' | grep -i "configuration_problems" -A5 || true
		(( etat < 1 )) && etat=1
	fi
	[[ "$base" != "ok" ]] && etat=2
fi

controler "front    (/)" "$BASE/" 200 || {
	resultat=$?
	(( resultat > etat )) && etat=$resultat
}

case "$etat" in
0) echo "Verdict : disponible." ;;
1) echo "Verdict : DÉGRADÉ — le service répond, quelque chose ne va pas." ;;
*) echo "Verdict : INDISPONIBLE." ;;
esac
exit "$etat"
