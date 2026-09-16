#!/usr/bin/env bash
# Vérifie la CHAÎNE d'un déploiement derrière un proxy déjà en place, maillon
# par maillon — parce que deux pannes différentes s'y ressemblent trait pour
# trait vues de l'extérieur.
#
#   ops/verifier_deploiement.sh [domaine]
#
# Lit infra/staging.env s'il existe (PUBLIC_DOMAIN, HTTP_DIAGNOSTIC_PORT).
# Code de sortie : 0 tout va bien, 1 au moins un maillon en défaut.
#
# Ce que ce script NE fait pas : il ne se connecte pas, ne crée rien, ne
# lit aucun corps de réponse qu'il afficherait. Il pose des questions
# oui/non à chaque maillon, dans l'ordre où la réponse de l'un change la
# lecture du suivant.
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FICHIER="${METREO_ENV_FILE:-$RACINE/infra/staging.env}"
if [[ -f "$ENV_FICHIER" ]]; then
	# Seules deux variables sont lues, et aucune n'est un secret.
	PUBLIC_DOMAIN_ENV=$(sed -n 's/^PUBLIC_DOMAIN=//p' "$ENV_FICHIER" | tail -1)
	PORT_ENV=$(sed -n 's/^HTTP_DIAGNOSTIC_PORT=//p' "$ENV_FICHIER" | tail -1)
fi
DOMAINE="${1:-${PUBLIC_DOMAIN_ENV:-}}"
PORT="${PORT_ENV:-8081}"
PROJET="${METREO_COMPOSE_PROJECT:-metreo-staging}"
RESEAU="${METREO_COMPOSE_NETWORK:-${PROJET}_default}"

if [[ -z "$DOMAINE" ]]; then
	echo "usage: $0 preprod.exemple.invalid   (ou PUBLIC_DOMAIN dans infra/staging.env)" >&2
	exit 1
fi

defauts=0
ok() { printf '  ok   %s\n' "$1"; }
ko() { printf '  KO   %s\n' "$1"; defauts=$((defauts + 1)); }
code_de() { curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$@" 2>/dev/null || echo 000; }

echo "Déploiement de $DOMAINE — projet « $PROJET »"
echo

# ---------------------------------------------------------------------------
echo "1. Les conteneurs, aux yeux de Docker"
# ---------------------------------------------------------------------------
for service in db api web proxy; do
	id=$(docker compose --project-name "$PROJET" ps -q "$service" 2>/dev/null | head -1)
	if [[ -z "$id" ]]; then ko "$service : aucun conteneur"; continue; fi
	sante=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}sans sonde{{end}}' "$id" 2>/dev/null || echo "?")
	etat=$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo "?")
	case "$sante" in
	healthy) ok "$service : running, healthy" ;;
	"sans sonde") [[ "$etat" == "running" ]] && ok "$service : running (pas de sonde déclarée)" || ko "$service : $etat" ;;
	*) ko "$service : $etat, sonde = $sante" ;;
	esac
done
echo

# ---------------------------------------------------------------------------
echo "2. Caddy, joint en local avec le bon Host"
# ---------------------------------------------------------------------------
# Sans l'en-tête Host, Caddy répond 404 à tout : il n'ouvre qu'un seul site.
c=$(code_de -H "Host: $DOMAINE" "http://127.0.0.1:$PORT/api/v1/live")
[[ "$c" == "200" ]] && ok "127.0.0.1:$PORT/api/v1/live → $c" || ko "127.0.0.1:$PORT/api/v1/live → $c (Caddy ou API en défaut)"
c=$(code_de -H "Host: $DOMAINE" "http://127.0.0.1:$PORT/")
[[ "$c" == "200" ]] && ok "127.0.0.1:$PORT/ → $c (front servi)" || ko "127.0.0.1:$PORT/ → $c (front non servi par Caddy)"
echo

# ---------------------------------------------------------------------------
echo "3. Le chemin que le proxy de devant emprunte : l'IP du conteneur"
# ---------------------------------------------------------------------------
# Le provider Docker de Traefik vise l'IP du conteneur sur son port 80, jamais
# la publication sur 127.0.0.1. Si ce maillon échoue alors que le 2 passe,
# c'est le réseau Docker qui est en cause, pas Caddy.
proxy_id=$(docker compose --project-name "$PROJET" ps -q proxy 2>/dev/null | head -1)
ip=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$RESEAU\").IPAddress}}" "$proxy_id" 2>/dev/null || true)
if [[ -z "$ip" ]]; then
	ko "le conteneur proxy n'a pas d'adresse sur « $RESEAU » (nom de réseau faux dans les étiquettes ?)"
else
	c=$(code_de -H "Host: $DOMAINE" "http://$ip/api/v1/live")
	[[ "$c" == "200" ]] && ok "$ip:80/api/v1/live → $c" || ko "$ip:80/api/v1/live → $c (Traefik ne pourra pas joindre Caddy)"
fi
echo

# ---------------------------------------------------------------------------
echo "4. Le certificat réellement présenté sur le 443 de cette machine"
# ---------------------------------------------------------------------------
emetteur=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAINE" 2>/dev/null \
	| openssl x509 -noout -issuer 2>/dev/null | sed 's/^issuer=//')
if [[ -z "$emetteur" ]]; then
	ko "aucun certificat lisible sur 127.0.0.1:443 pour $DOMAINE"
elif [[ "$emetteur" == *"TRAEFIK DEFAULT"* ]]; then
	ko "certificat par défaut de Traefik : aucun certificat n'a été DEMANDÉ (routeur sans tls/certresolver ?)"
elif [[ "$emetteur" == *"Let's Encrypt"* ]]; then
	ok "émetteur : $emetteur"
else
	ko "émetteur inattendu : $emetteur"
fi
expiration=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAINE" 2>/dev/null \
	| openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')
[[ -n "$expiration" ]] && printf '       expire : %s\n' "$expiration"
echo

# ---------------------------------------------------------------------------
echo "5. Vu de l'extérieur, en HTTPS, par le nom public"
# ---------------------------------------------------------------------------
sante=$(curl -sS --max-time 10 "https://$DOMAINE/api/v1/health" 2>/dev/null || true)
if [[ -z "$sante" ]]; then
	ko "https://$DOMAINE/api/v1/health injoignable"
else
	lire() { printf '%s' "$sante" | python3 -c "import json,sys; d=json.load(sys.stdin); v=d.get('$1'); print(','.join(v) if isinstance(v,list) else v)" 2>/dev/null || echo "?"; }
	statut=$(lire status); base=$(lire database); problemes=$(lire configuration_problems); methodes=$(lire login_methods)
	[[ "$statut" == "ok" ]] && ok "status = ok" || ko "status = $statut"
	[[ "$base" == "ok" ]] && ok "database = ok" || ko "database = $base"
	[[ -z "$problemes" || "$problemes" == "?" ]] && ok "configuration_problems vide" || ko "configuration_problems = $problemes"
	[[ "$methodes" == *oidc* ]] && ok "login_methods contient oidc" || ko "login_methods = $methodes (oidc absent)"
fi
entetes=$(curl -sSI --max-time 10 "https://$DOMAINE/api/v1/live" 2>/dev/null || true)
printf '%s' "$entetes" | grep -qi '^strict-transport-security' && ok "HSTS relayé par le proxy de devant" || ko "HSTS absent : les en-têtes de Caddy ne traversent pas"
printf '%s' "$entetes" | grep -qi '^x-request-id' && ok "X-Request-Id présent (corrélation des journaux)" || ko "X-Request-Id absent"
echo

# ---------------------------------------------------------------------------
echo "6. Ce que le proxy de devant dit de l'émission du certificat"
# ---------------------------------------------------------------------------
traefik=$(docker ps -q --filter name=traefik | head -1)
if [[ -z "$traefik" ]]; then
	ko "aucun conteneur traefik trouvé"
else
	erreurs=$(docker logs --tail 500 "$traefik" 2>&1 | grep -iE 'unable to obtain|rate ?limit|acme.*error|error.*acme' | tail -5)
	if [[ -n "$erreurs" ]]; then
		ko "erreurs ACME dans les journaux de Traefik :"
		printf '%s\n' "$erreurs" | sed 's/^/         /'
	else
		ok "aucune erreur ACME dans les 500 dernières lignes de Traefik"
	fi
fi
echo

if ((defauts == 0)); then
	echo "Verdict : chaîne complète, aucun maillon en défaut."
	exit 0
fi
echo "Verdict : $defauts maillon(s) en défaut — lire de haut en bas, le premier KO explique souvent les suivants."
exit 1
