#!/usr/bin/env bash
# Vérifie la CHAÎNE d'un déploiement derrière un proxy déjà en place, maillon
# par maillon — parce que deux pannes différentes s'y ressemblent trait pour
# trait vues de l'extérieur.
#
#   ops/verifier_deploiement.sh [domaine]
#
# Lit infra/staging.env s'il existe (PUBLIC_DOMAIN, HTTP_DIAGNOSTIC_PORT).
# Codes de sortie : 0 tout va bien, 1 au moins un maillon en défaut,
# 2 Docker injoignable — rien n'a été contrôlé.
#
# Ce que ce script NE fait pas : il ne se connecte pas, ne crée rien, et
# n'affiche du corps de `/health` que quatre champs — `status`, `database`,
# `configuration_problems`, `login_methods` —, qui ne portent que des noms de
# réglages, jamais une valeur. Il exige les droits Docker de l'opérateur de la
# pile (groupe docker, ou sudo) et python3.
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FICHIER="${METREO_ENV_FILE:-$RACINE/infra/staging.env}"

# Compose retire les guillemets et le commentaire de fin de ligne (` # …`) :
# on fait pareil, sinon un fichier que Compose accepte ferait échouer ici les
# maillons 2 à 5 sur une pile pourtant saine. Seules deux variables sont lues,
# et aucune n'est un secret.
valeur_env() {
	[[ -f "$ENV_FICHIER" ]] || return 0
	sed -n "s/^$1=//p" "$ENV_FICHIER" | tail -1 \
		| sed -E "s/[[:space:]]+#.*$//; s/^[\"']//; s/[\"']$//; s/[[:space:]]+$//"
}
DOMAINE="${1:-$(valeur_env PUBLIC_DOMAIN)}"
PORT="$(valeur_env HTTP_DIAGNOSTIC_PORT)"; PORT="${PORT:-8081}"
PROJET="${METREO_COMPOSE_PROJECT:-metreo-staging}"
RESEAU="${METREO_COMPOSE_NETWORK:-${PROJET}_default}"

if [[ -z "$DOMAINE" ]]; then
	echo "usage: $0 preprod.exemple.invalid   (ou PUBLIC_DOMAIN dans infra/staging.env)" >&2
	exit 2
fi

# Sans accès au démon, `docker compose ps` rend vide en silence, et les
# maillons 1, 3 et 6 accuseraient une pile saine de douze défauts fabriqués.
# Mesuré : c'est exactement ce que ce script affichait sans Docker.
if ! docker info >/dev/null 2>&1; then
	echo "Docker injoignable — démon arrêté, ou droits manquants sur /var/run/docker.sock" >&2
	echo "(groupe docker, ou sudo). Rien n'a été vérifié." >&2
	exit 2
fi

defauts=0
ok() { printf '  ok   %s\n' "$1"; }
ko() { printf '  KO   %s\n' "$1"; defauts=$((defauts + 1)); }

# Rend « code entetes » : `-w` imprime déjà 000 quand curl échoue, un
# `|| echo` en rajouterait un second. Mesuré : « 000000 ».
sonder() {
	local sortie
	sortie=$(curl -sS -o /dev/null -D - -w '\n%{http_code}' --max-time 10 "$@" 2>/dev/null) || true
	printf '%s' "${sortie:-000}"
}
code_de() { sonder "$@" | tail -1; }
# À un Host qu'il ne sert pas, Caddy répond un 200 VIDE (son gestionnaire par
# défaut), pas un 404 : le code seul ne prouve rien. L'en-tête HSTS, lui, est
# posé dans le bloc de site du Caddyfile — sa présence dit que ce site a servi.
servi_par_caddy() { sonder "$@" | grep -qi '^strict-transport-security'; }

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
for chemin in /api/v1/live /; do
	c=$(code_de -H "Host: $DOMAINE" "http://127.0.0.1:$PORT$chemin")
	if [[ "$c" != "200" ]]; then
		ko "127.0.0.1:$PORT$chemin → $c"
	elif servi_par_caddy -H "Host: $DOMAINE" "http://127.0.0.1:$PORT$chemin"; then
		ok "127.0.0.1:$PORT$chemin → 200, servi par le site « $DOMAINE »"
	else
		ko "127.0.0.1:$PORT$chemin → 200 VIDE : Caddy ne sert pas le Host « $DOMAINE » (PUBLIC_HOST ≠ PUBLIC_DOMAIN ?)"
	fi
done
echo

# ---------------------------------------------------------------------------
echo "3. Le chemin que le proxy de devant emprunte : l'IP du conteneur"
# ---------------------------------------------------------------------------
# Le provider Docker de Traefik vise l'IP du conteneur sur son port 80, jamais
# la publication sur 127.0.0.1. Si ce maillon échoue alors que le 2 passe,
# c'est le réseau Docker qui est en cause, pas Caddy.
proxy_id=$(docker compose --project-name "$PROJET" ps -q proxy 2>/dev/null | head -1)
if [[ -z "$proxy_id" ]]; then
	ko "proxy absent ou arrêté : voir le maillon 1"
else
	ip=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$RESEAU\").IPAddress}}" "$proxy_id" 2>/dev/null || true)
	if [[ -z "$ip" ]]; then
		ko "le conteneur proxy n'a pas d'adresse sur « $RESEAU » (nom de réseau faux dans les étiquettes ?)"
	elif servi_par_caddy -H "Host: $DOMAINE" "http://$ip/api/v1/live"; then
		ok "$ip:80/api/v1/live → servi par le site « $DOMAINE »"
	else
		ko "$ip:80/api/v1/live → $(code_de -H "Host: $DOMAINE" "http://$ip/api/v1/live") (Traefik ne pourra pas joindre Caddy)"
	fi
fi
echo

# ---------------------------------------------------------------------------
echo "4. Le certificat réellement présenté sur le 443 de cette machine"
# ---------------------------------------------------------------------------
cert=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAINE" 2>/dev/null | openssl x509 2>/dev/null)
emetteur=$(printf '%s\n' "$cert" | openssl x509 -noout -issuer 2>/dev/null | sed 's/^issuer=//')
if [[ -z "$emetteur" ]]; then
	ko "aucun certificat lisible sur 127.0.0.1:443 pour $DOMAINE"
elif [[ "$emetteur" == *"TRAEFIK DEFAULT"* ]]; then
	ko "certificat par défaut de Traefik : aucun certificat n'a été DEMANDÉ (routeur sans tls/certresolver, ou TRAEFIK_CERT_RESOLVER ne nomme pas un résolveur de ce Traefik ?)"
elif [[ "$emetteur" == *STAGING* ]]; then
	ko "certificat de l'autorité de TEST Let's Encrypt, que les navigateurs refusent (résolveur sur acme-staging ?) : $emetteur"
elif [[ "$emetteur" == *"Let's Encrypt"* ]]; then
	ok "émetteur : $emetteur"
else
	ko "émetteur inattendu : $emetteur"
fi
if [[ -n "$cert" ]]; then
	# Les seuils sont ceux de docs/EXPLOITATION.md : avertissement à 21 jours.
	fin=$(printf '%s\n' "$cert" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')
	fin_epoch=$(date -d "$fin" +%s 2>/dev/null || echo 0)
	jours=$(( (fin_epoch - $(date +%s)) / 86400 ))
	if (( fin_epoch == 0 )); then
		ko "date d'expiration illisible : $fin"
	elif (( jours < 0 )); then
		ko "certificat EXPIRÉ depuis $(( -jours )) jour(s) : $fin"
	elif (( jours < 21 )); then
		ko "certificat expire dans $jours jour(s) — sous le seuil d'avertissement : $fin"
	else
		ok "expire dans $jours jours ($fin)"
	fi
fi
echo

# ---------------------------------------------------------------------------
echo "5. Vu de l'extérieur, en HTTPS, par le nom public"
# ---------------------------------------------------------------------------
sante=$(curl -sS --max-time 10 "https://$DOMAINE/api/v1/health" 2>/dev/null || true)
# Une clé absente se dit « ABSENT » ; un corps qui n'est pas du JSON se dit « ? »
# — et se traite une seule fois, avant les quatre lectures.
lire() { printf '%s' "$sante" | python3 -c "import json,sys; d=json.load(sys.stdin); v=d.get('$1','ABSENT'); print(','.join(v) if isinstance(v,list) else v)" 2>/dev/null || echo "?"; }
if [[ -z "$sante" ]]; then
	ko "https://$DOMAINE/api/v1/health injoignable"
elif [[ "$(lire status)" == "?" ]]; then
	ko "corps de https://$DOMAINE/api/v1/health illisible (page d'erreur du proxy de devant : 404 sans routeur, 502 Caddy injoignable ?)"
else
	statut=$(lire status); base=$(lire database); problemes=$(lire configuration_problems); methodes=$(lire login_methods)
	[[ "$statut" == "ok" ]] && ok "status = ok" || ko "status = $statut"
	[[ "$base" == "ok" ]] && ok "database = ok" || ko "database = $base"
	[[ -z "$problemes" ]] && ok "configuration_problems vide" || ko "configuration_problems = $problemes"
	[[ "$methodes" == *oidc* ]] && ok "login_methods contient oidc" || ko "login_methods = $methodes (oidc absent)"
fi
entetes=$(sonder "https://$DOMAINE/api/v1/live")
printf '%s' "$entetes" | grep -qi '^strict-transport-security' && ok "HSTS relayé par le proxy de devant" || ko "HSTS absent : les en-têtes de Caddy ne traversent pas"
printf '%s' "$entetes" | grep -qi '^x-request-id' && ok "X-Request-Id présent (corrélation des journaux)" || ko "X-Request-Id absent"
echo

# ---------------------------------------------------------------------------
echo "6. Ce que le proxy de devant dit de l'émission du certificat"
# ---------------------------------------------------------------------------
traefik="${METREO_TRAEFIK_CONTAINER:-}"
if [[ -z "$traefik" ]]; then
	candidats=$(docker ps -q --filter name=traefik 2>/dev/null)
	if (( $(printf '%s\n' "$candidats" | grep -c .) > 1 )); then
		ko "plusieurs conteneurs portent « traefik » dans leur nom : désignez-le avec METREO_TRAEFIK_CONTAINER"
		candidats=""
	fi
	traefik=$(printf '%s\n' "$candidats" | head -1)
fi
if [[ -z "$traefik" ]]; then
	ko "aucun conteneur traefik trouvé (METREO_TRAEFIK_CONTAINER pour le désigner)"
else
	# Fenêtre bornée à 24 h et niveau d'erreur exigé : une erreur ACME de la
	# semaine dernière, corrigée depuis, ne doit pas condamner un certificat
	# que le maillon 4 vient de valider — ni une ligne DEBUG qui cite « acme ».
	erreurs=$(docker logs --since 24h "$traefik" 2>&1 \
		| grep -iE 'level=error|"level":"error"|\bERR\b' \
		| grep -iE 'acme|certificate|challenge|obtain' | tail -5)
	if [[ -n "$erreurs" ]]; then
		ko "erreurs ACME dans les journaux de Traefik (24 h) :"
		printf '%s\n' "$erreurs" | sed 's/^/         /'
	else
		ok "aucune erreur ACME dans les journaux de Traefik sur 24 h"
	fi
fi
echo

if ((defauts == 0)); then
	echo "Verdict : chaîne complète, aucun maillon en défaut."
	exit 0
fi
echo "Verdict : $defauts maillon(s) en défaut — lire de haut en bas, le premier KO explique souvent les suivants."
exit 1
