#!/usr/bin/env bash
# =============================================================================
#  RÉPÉTITION DE PRÉPRODUCTION
# =============================================================================
#
#  Monte une préproduction complète — PostgreSQL, migrations, API, front,
#  proxy, stockage persistant, fournisseur d'identité — et la fait travailler
#  comme le ferait un jour d'exploitation : quelqu'un se connecte, produit un
#  devis, le gèle ; les conteneurs redémarrent ; on sauvegarde ; on restaure
#  ailleurs ; on vérifie que tout est là.
#
#  Ce que cette répétition N'EST PAS : un déploiement. Rien ne sort de la
#  machine, aucun port ne quitte 127.0.0.1, aucune image n'est publiée, aucun
#  service externe n'est appelé. Le fournisseur d'identité est un faux, monté
#  ici et détruit avec le reste.
#
#      ops/repetition_staging.sh
#
#  Variables : RAPPORT (JSON de sortie), CONSERVER=1 pour ne pas démonter à la
#  fin (mise au point locale uniquement — la CI ne la passe jamais).
#
#  Tout ce qui est créé est préfixé par les deux noms de projet ci-dessous, et
#  détruit par `demonter`, appelée par un `trap` — succès comme échec.
# =============================================================================
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RACINE"

PROJET="metreo-repetition"
PROJET_RESTAURE="metreo-repetition-restaure"

COMPOSITIONS=(-f infra/docker-compose.staging.yml -f infra/docker-compose.repetition.yml)
ENV_FICHIER="${ENV_FICHIER:-$RACINE/infra/repetition.env}"

BASE="http://localhost:8080"
API="$BASE/api/v1"
FOURNISSEUR="http://127.0.0.1:8021"

RAPPORT="${RAPPORT:-$RACINE/var/repetition/rapport.json}"
JOURNAUX="${JOURNAUX:-$RACINE/var/repetition/journaux}"
TRAVAIL="$RACINE/var/repetition/travail"

mkdir -p "$(dirname "$RAPPORT")" "$JOURNAUX" "$TRAVAIL"

ETAPES_OK=()
ETAPES_KO=()
ETAPE_COURANTE=""

# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------

titre() {
	ETAPE_COURANTE="$1"
	printf '\n\033[1m── %s\033[0m\n' "$1"
}

ok() {
	ETAPES_OK+=("${ETAPE_COURANTE}: $1")
	printf '   \033[32m✓\033[0m %s\n' "$1"
}

ko() {
	ETAPES_KO+=("${ETAPE_COURANTE}: $1")
	printf '   \033[31m✗\033[0m %s\n' "$1" >&2
}

detail() { printf '     %s\n' "$1"; }

# Un échec n'arrête pas la répétition : on veut la liste complète de ce qui ne
# va pas, pas le premier symptôme. Le code de sortie final la résume.
verifier() {
	local libelle="$1" attendu="$2" obtenu="$3"
	if [[ "$obtenu" == "$attendu" ]]; then
		ok "$libelle"
		return 0
	fi
	ko "$libelle — attendu « $attendu », obtenu « $obtenu »"
	return 1
}

# Deux MONTANTS, comparés comme des nombres et non comme des chaînes.
#
# « 23080.10 » et « 23080.1 » désignent la même somme ; le premier vient de
# l'API, qui rend le montant tel qu'il s'imprime, le second de la colonne
# NUMERIC relue par le pilote. Les comparer caractère par caractère faisait
# échouer la restauration dès que le total finissait par un zéro — mesuré, et
# indépendant de la restauration elle-même, qui avait parfaitement rendu la
# bonne valeur.
verifier_montant() {
	local libelle="$1" attendu="$2" obtenu="$3"
	if python3 -c '
import sys
from decimal import Decimal, InvalidOperation
try:
    sys.exit(0 if Decimal(sys.argv[1]) == Decimal(sys.argv[2]) else 1)
except InvalidOperation:
    sys.exit(1)
' "$attendu" "$obtenu"; then
		ok "$libelle"
		return 0
	fi
	ko "$libelle — attendu « $attendu », obtenu « $obtenu »"
	return 1
}

compose() { docker compose --project-name "$PROJET" "${COMPOSITIONS[@]}" --env-file "$ENV_FICHIER" "$@"; }

# ---------------------------------------------------------------------------
# Démontage — appelé par trap, donc y compris sur échec ou interruption
# ---------------------------------------------------------------------------

demonter() {
	local code=$?
	if [[ "${CONSERVER:-0}" == "1" ]]; then
		printf '\nCONSERVER=1 : la pile reste debout. À démonter à la main :\n'
		printf '  docker compose --project-name %s ... down -v\n' "$PROJET"
		return "$code"
	fi
	printf '\n\033[1m── Démontage\033[0m\n'
	# Journaux d'abord : `down` les emporte, et ce sont eux qu'on lit quand la
	# répétition a échoué.
	recolter_journaux || true
	for projet in "$PROJET" "$PROJET_RESTAURE"; do
		docker compose --project-name "$projet" "${COMPOSITIONS[@]}" \
			--env-file "$ENV_FICHIER" down --volumes --remove-orphans >/dev/null 2>&1 || true
		printf '   projet démonté : %s\n' "$projet"
	done
	# Filet : un conteneur ou un volume qui aurait échappé à `down`. Le filtre
	# ne peut désigner que ces deux projets — jamais un voisin.
	for projet in "$PROJET" "$PROJET_RESTAURE"; do
		docker ps -aq --filter "label=com.docker.compose.project=$projet" \
			| xargs -r docker rm -f >/dev/null 2>&1 || true
		docker volume ls -q --filter "label=com.docker.compose.project=$projet" \
			| xargs -r docker volume rm -f >/dev/null 2>&1 || true
		docker network ls -q --filter "label=com.docker.compose.project=$projet" \
			| xargs -r docker network rm >/dev/null 2>&1 || true
	done
	printf '   ressources de répétition supprimées\n'
	return "$code"
}
trap demonter EXIT

recolter_journaux() {
	for service in db migrate api web proxy oidc; do
		docker compose --project-name "$PROJET" "${COMPOSITIONS[@]}" --env-file "$ENV_FICHIER" \
			logs --no-color --timestamps "$service" > "$JOURNAUX/$service.log" 2>/dev/null || true
	done
}

# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

# Attend une réponse HTTP précise. La répétition ne doit jamais conclure sur un
# service qui n'a pas fini de démarrer : ce serait accuser l'application d'un
# défaut qui n'est qu'une course.
attendre_code() {
	local url="$1" attendu="$2" limite="${3:-120}" i code
	for ((i = 0; i < limite; i++)); do
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)
		[[ "$code" == "$attendu" ]] && return 0
		sleep 1
	done
	detail "dernier code obtenu sur $url : ${code:-000}"
	return 1
}

code_de() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" 2>/dev/null || echo 000; }

# Exécute une commande Python dans le conteneur de l'API, avec son
# environnement. Sert au bootstrap et aux contrôles en base.
dans_api() { compose exec -T api "$@"; }

# Une tâche jetable sur l'image de l'API, pour ce qui doit tourner hors des
# conteneurs en service.
tache_api() { compose run --rm --no-deps -T "$@"; }

# ===========================================================================
#  1. Une configuration OIDC incomplète refuse de démarrer
# ===========================================================================
#
# Éprouvé AVANT de monter la pile : c'est un refus au démarrage, et le
# vérifier ensuite demanderait de casser une pile qui marche.

etape_refus_configuration() {
	titre "Refus de démarrage sur configuration OIDC incomplète"

	local sortie
	# Le secret client retiré, tout le reste en place. L'API doit refuser de
	# se construire, et NOMMER ce qui manque.
	sortie=$(compose run --rm --no-deps -T \
		-e METREO_OIDC_CLIENT_SECRET= \
		api python -c "from metreo_api.main import create_app; create_app()" 2>&1)
	local code=$?

	if [[ $code -eq 0 ]]; then
		ko "l'API démarre malgré un secret client OIDC absent"
	else
		ok "l'API refuse de démarrer sans secret client OIDC"
	fi
	if grep -q "oidc_client_secret" <<<"$sortie"; then
		ok "le refus nomme la valeur manquante"
	else
		ko "le refus ne nomme pas oidc_client_secret"
		detail "$(tail -2 <<<"$sortie")"
	fi
}

# ===========================================================================
#  2. Démarrage de la pile
# ===========================================================================

etape_demarrage() {
	titre "Démarrage de la pile de préproduction"

	compose up -d --wait --wait-timeout 300 >/dev/null 2>&1 || true

	# La tâche de migration d'abord : l'API l'attend en
	# `service_completed_successfully`. Si elle a échoué, l'API n'a jamais
	# démarré et TOUT le reste échouera pour cette seule raison — autant le
	# dire ici, avec son journal, plutôt que de laisser deviner.
	local code_migration
	code_migration=$(docker inspect -f '{{.State.ExitCode}}' "$(compose ps -aq migrate)" 2>/dev/null || echo "?")
	if [[ "$code_migration" != "0" ]]; then
		ko "la tâche de migration a échoué (code $code_migration) — rien ne démarrera"
		printf '     ── journal de « migrate » ──\n' >&2
		compose logs --no-color migrate 2>&1 | tail -20 | sed 's/^/     /' >&2
		return 1
	fi

	# Par le PROXY, pas par l'API : c'est le chemin qu'emprunte un utilisateur,
	# et c'est donc le seul dont la réponse prouve quelque chose.
	if attendre_code "$API/ready" 200 180; then
		ok "le service répond par le proxy ($API/ready)"
	else
		ko "le service ne répond pas par le proxy"
		compose ps
		return 1
	fi

	verifier "le front est servi à la racine" 200 "$(code_de "$BASE/")"

	local environnement
	environnement=$(curl -s "$API/health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["environment"])' 2>/dev/null || echo "?")
	verifier "l'environnement annoncé est staging" "staging" "$environnement"

	local methodes
	methodes=$(curl -s "$API/health" | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)["login_methods"]))' 2>/dev/null || echo "?")
	verifier "la seule connexion offerte est oidc" "oidc" "$methodes"
}

# ===========================================================================
#  3. `dev-login` est injoignable
# ===========================================================================

etape_dev_login_absent() {
	titre "La connexion de développement est injoignable"
	local code
	code=$(code_de -X POST -H 'Content-Type: application/json' \
		-d '{"email":"admin@dubois.demo"}' "$API/auth/dev-login")
	verifier "POST /auth/dev-login répond 404" "404" "$code"
}

# ===========================================================================
#  4. Aucune donnée de démonstration en préproduction
# ===========================================================================

etape_aucune_demonstration() {
	titre "Aucune donnée de démonstration n'a été chargée"

	# Compté en base, avant tout bootstrap : une préproduction fraîchement
	# migrée doit être VIDE. Un jeu de démonstration s'y serait glissé par la
	# composition, et personne ne le verrait avant de chercher un vrai devis.
	local compte
	compte=$(dans_api python -c "
from sqlalchemy import func, select
from metreo_api.db import get_session_factory
from metreo_api.models import Organization, User, Project
with get_session_factory()() as s:
    print(':'.join(str(s.scalar(select(func.count()).select_from(m)) or 0)
                   for m in (Organization, User, Project)))
" 2>/dev/null | tr -d '\r')
	verifier "organisations:utilisateurs:projets tous à zéro" "0:0:0" "$compte"

	# Et le seed ne doit pas être invocable par accident : il porte lui-même
	# son garde-fou, ou il n'en porte pas — on le constate plutôt que de
	# l'espérer.
	local sortie
	sortie=$(compose run --rm --no-deps -T api python -m metreo_api.seed 2>&1)
	if [[ $? -ne 0 ]]; then
		ok "le jeu de démonstration refuse de se charger en préproduction"
	else
		ko "le jeu de démonstration s'est chargé en préproduction"
		detail "$(tail -1 <<<"$sortie")"
		# On remet la base dans l'état attendu pour la suite.
		dans_api python -c "
from metreo_api.db import get_session_factory
from metreo_api.models import Organization
with get_session_factory()() as s:
    for o in s.query(Organization).all(): s.delete(o)
    s.commit()
" >/dev/null 2>&1 || true
	fi
}

# ===========================================================================
#  5. Les migrations n'ont été exécutées que par la tâche prévue
# ===========================================================================

etape_migrations_une_seule_fois() {
	titre "Les migrations sont l'affaire de la seule tâche « migrate »"

	# La tâche a tourné et s'est terminée.
	local etat
	etat=$(docker inspect -f '{{.State.Status}}' \
		"$(compose ps -aq migrate)" 2>/dev/null || echo "?")
	verifier "la tâche « migrate » s'est terminée" "exited" "$etat"

	local sortie
	sortie=$(docker inspect -f '{{.State.ExitCode}}' \
		"$(compose ps -aq migrate)" 2>/dev/null || echo "?")
	verifier "elle s'est terminée avec succès" "0" "$sortie"

	# Et l'API, elle, n'a pas migré : son journal ne doit porter aucune trace
	# d'exécution d'Alembic. Deux instances qui migrent en concurrence est le
	# défaut que cette séparation existe pour empêcher.
	if compose logs --no-color api 2>/dev/null | grep -qi "Running upgrade"; then
		ko "l'API a exécuté des migrations"
	else
		ok "l'API n'a exécuté aucune migration"
	fi

	# Une seule tête, et la base y est.
	local tete
	tete=$(dans_api alembic -c apps/api/alembic.ini current 2>/dev/null | grep -oE '^[0-9a-f]+' | head -1)
	if [[ -n "$tete" ]]; then
		ok "la base est à la révision $tete"
	else
		ko "impossible de lire la révision courante"
	fi
}

# ===========================================================================
#  6. Amorçage : l'organisation initiale et son premier administrateur
# ===========================================================================

# `.invalid` est refusé par la validation d'adresse de l'API (nom réservé) :
# l'amorçage l'accepterait, et le compte ne pourrait jamais se connecter.
# `.example` est réservé à la documentation et passe la validation.
ADMIN="premiere.administratrice@repetition.example"
ORGANISATION="Organisation de répétition"

etape_bootstrap() {
	titre "Amorçage de l'organisation initiale"

	local sortie
	sortie=$(dans_api python -m metreo_api.bootstrap \
		--organization "$ORGANISATION" \
		--admin-email "$ADMIN" \
		--admin-name "Première administratrice" 2>&1)
	if [[ $? -ne 0 ]]; then
		ko "l'amorçage a échoué"
		detail "$(tail -2 <<<"$sortie")"
		return 1
	fi
	ok "organisation et premier administrateur créés"
	detail "$(tail -1 <<<"$sortie")"

	# Idempotence : relancée à l'identique, elle ne doit RIEN créer de plus.
	# C'est la propriété qui permet de la laisser dans un script de démarrage.
	local seconde
	seconde=$(dans_api python -m metreo_api.bootstrap \
		--organization "$ORGANISATION" --admin-email "$ADMIN" 2>&1 | tail -1)
	if grep -q "déjà en place" <<<"$seconde"; then
		ok "relancée à l'identique, elle ne duplique rien"
	else
		ko "la seconde exécution n'annonce pas « déjà en place »"
		detail "$seconde"
	fi

	local compte
	compte=$(dans_api python -c "
from sqlalchemy import func, select
from metreo_api.db import get_session_factory
from metreo_api.models import Organization, User, Membership
with get_session_factory()() as s:
    print(':'.join(str(s.scalar(select(func.count()).select_from(m)) or 0)
                   for m in (Organization, User, Membership)))
" 2>/dev/null | tr -d '\r')
	verifier "exactement une organisation, un utilisateur, une appartenance" "1:1:1" "$compte"

	# Aucun mot de passe : ce que l'amorçage crée est le DROIT d'entrer, pas un
	# moyen d'entrer.
	local colonnes
	colonnes=$(dans_api python -c "
from metreo_api.models import User
noms = {c.name for c in User.__table__.columns}
print('oui' if noms & {'password', 'password_hash', 'hashed_password', 'secret'} else 'non')
" 2>/dev/null | tr -d '\r')
	verifier "aucune colonne de mot de passe sur les comptes" "non" "$colonnes"
}

# ===========================================================================
#  7. Le parcours de connexion complet, avec PKCE, par le proxy
# ===========================================================================

JETON=""
ORGANISATION_ID=""

etape_connexion_oidc() {
	titre "Connexion OpenID Connect complète (code d'autorisation + PKCE)"

	# --- a) départ : l'application ouvre une transaction et rend l'URL du
	#        fournisseur. Le `code_challenge` y figure ; le vérificateur reste
	#        en base, côté application, et ne transite jamais.
	local depart url_autorisation
	depart=$(curl -s "$API/auth/oidc/start")
	url_autorisation=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["authorization_url"])' "$depart" 2>/dev/null)
	if [[ -z "$url_autorisation" ]]; then
		ko "/auth/oidc/start n'a pas rendu d'URL d'autorisation"
		detail "$depart"
		return 1
	fi
	ok "l'application a ouvert une transaction de connexion"

	for exige in "code_challenge=" "code_challenge_method=S256" "state=" "nonce=" "response_type=code"; do
		if grep -q "$exige" <<<"$url_autorisation"; then
			ok "l'URL d'autorisation porte $exige"
		else
			ko "l'URL d'autorisation ne porte pas $exige"
		fi
	done

	# Le vérificateur PKCE ne doit surtout PAS être dans l'URL : il n'y a de
	# protection que parce qu'il reste secret jusqu'à l'échange.
	if grep -q "code_verifier" <<<"$url_autorisation"; then
		ko "le vérificateur PKCE voyage dans l'URL d'autorisation"
	else
		ok "le vérificateur PKCE ne quitte pas l'application"
	fi

	local etat nonce
	etat=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("state", [""])[0])' "$url_autorisation")
	nonce=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("nonce", [""])[0])' "$url_autorisation")
	local redirection
	redirection=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("redirect_uri", [""])[0])' "$url_autorisation")

	# --- b) le fournisseur authentifie. On joue ici le rôle du navigateur :
	#        le formulaire est envoyé tel qu'il serait soumis par une personne.
	local retour code_autorisation
	retour=$(curl -s -o /dev/null -w '%{redirect_url}' \
		-X POST "$FOURNISSEUR/authorize" \
		--data-urlencode "email=$ADMIN" \
		--data-urlencode "redirect_uri=$redirection" \
		--data-urlencode "state=$etat" \
		--data-urlencode "nonce=$nonce")
	code_autorisation=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("code", [""])[0])' "$retour")
	if [[ -z "$code_autorisation" ]]; then
		ko "le fournisseur n'a pas rendu de code d'autorisation"
		detail "$retour"
		return 1
	fi
	ok "le fournisseur a authentifié et rendu un code d'autorisation"

	# --- c) retour : l'application vérifie signature, émetteur, audience,
	#        expiration et nonce, puis renvoie le navigateur avec un code
	#        OPAQUE. Le jeton ne doit apparaître nulle part dans cette URL.
	local retour_app
	retour_app=$(curl -s -o /dev/null -w '%{redirect_url}' \
		"$API/auth/oidc/callback?code=$code_autorisation&state=$etat")

	local erreur
	erreur=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("login_error", [""])[0])' "$retour_app")
	if [[ -n "$erreur" ]]; then
		ko "le retour de connexion a échoué : $erreur"
		return 1
	fi

	local code_connexion
	code_connexion=$(python3 -c '
import sys, urllib.parse as u
print(u.parse_qs(u.urlparse(sys.argv[1]).query).get("login_code", [""])[0])' "$retour_app")
	if [[ -z "$code_connexion" ]]; then
		ko "aucun code de connexion dans le retour"
		return 1
	fi
	ok "le retour porte un code de connexion opaque"

	# Le contrôle qui compte : AUCUN jeton dans l'URL. Une URL se retrouve
	# dans l'historique, les journaux du proxy et l'en-tête Referer.
	if grep -qE 'eyJ|access_token|Bearer' <<<"$retour_app"; then
		ko "un jeton apparaît dans l'URL de retour"
		return 1
	fi
	ok "aucun jeton n'apparaît dans l'URL de retour"

	# --- d) échange : le jeton n'existe que dans ce corps de réponse.
	local echange
	echange=$(curl -s -X POST "$API/auth/oidc/exchange" \
		-H 'Content-Type: application/json' \
		-d "{\"login_code\":\"$code_connexion\"}")
	JETON=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("access_token",""))' "$echange" 2>/dev/null)
	ORGANISATION_ID=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("organization_id",""))' "$echange" 2>/dev/null)
	if [[ -z "$JETON" ]]; then
		ko "l'échange n'a pas rendu de jeton"
		detail "$echange"
		return 1
	fi
	ok "le code a été échangé contre une session"

	local role
	role=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("role",""))' "$echange")
	verifier "le rôle obtenu est org_admin" "org_admin" "$role"

	# --- e) usage unique : rejouer le même code doit échouer.
	local rejeu
	rejeu=$(code_de -X POST "$API/auth/oidc/exchange" \
		-H 'Content-Type: application/json' \
		-d "{\"login_code\":\"$code_connexion\"}")
	verifier "le code de connexion ne sert qu'une fois (rejeu refusé)" "401" "$rejeu"

	# --- f) et le `state` non plus : le rejouer doit être refusé.
	local rejeu_etat
	rejeu_etat=$(curl -s -o /dev/null -w '%{redirect_url}' \
		"$API/auth/oidc/callback?code=$code_autorisation&state=$etat")
	if grep -q "login_error=" <<<"$rejeu_etat"; then
		ok "le rejeu du state est refusé"
	else
		ko "le rejeu du state a été accepté"
	fi

	# --- g) la session obtenue est utilisable.
	local moi
	moi=$(curl -s -H "Authorization: Bearer $JETON" "$API/auth/me")
	if grep -q "$ADMIN" <<<"$moi"; then
		ok "la session identifie bien le premier administrateur"
	else
		ko "la session ne correspond pas au compte amorcé"
		detail "$moi"
	fi
}

# ===========================================================================
#  8. Le devis : de l'organisation vide au document gelé
# ===========================================================================

ESTIMATION_ID=""
VERSION_ID=""
TOTAL_HT=""
EMPREINTE_DEVIS=""

etape_devis() {
	titre "Premier devis, produit au navigateur par une personne"

	# Ce que cette étape éprouve a changé, et c'est le fond du sujet.
	#
	# Elle posait un taux de TVA DIRECTEMENT EN BASE, puis fabriquait projet,
	# bordereau et devis avec `ops/parcours_devis.py`. Les deux raccourcis
	# étaient assumés et dits — mais ils prouvaient une chose que personne ne
	# pouvait faire : aucun écran ne permettait de configurer une taxe, et
	# aucun utilisateur n'avait de script.
	#
	# Le scénario passe maintenant par le NAVIGATEUR, sur la pile réelle,
	# derrière le proxy : la fiscalité, la bibliothèque, le prix, le chantier,
	# la ligne chiffrée et le gel sont produits à la souris. Ce qui suit —
	# redémarrage, sauvegarde, restauration — porte donc sur un devis
	# qu'une personne aurait pu remettre à un client.
	if ! command -v npx >/dev/null 2>&1; then
		ko "npx est absent : le parcours navigateur ne peut pas être joué"
		return 1
	fi

	local constat="$TRAVAIL/devis-navigateur.json"
	CONSTAT_DOCUMENTS="$TRAVAIL/documents-navigateur.json"
	rm -f "$constat" "$CONSTAT_DOCUMENTS"

	local journal="$JOURNAUX/parcours-navigateur.txt"
	if ! (
		cd apps/web \
			&& METREO_BANC_URL="$BASE" \
				METREO_BANC_ADMIN="$ADMIN" \
				METREO_BANC_ORGANISATION="$ORGANISATION" \
				METREO_BANC_CONSTAT="$constat" \
				METREO_BANC_CONSTAT_DOCUMENTS="$CONSTAT_DOCUMENTS" \
				npx playwright test --config=playwright.premier-devis.config.ts
	) >"$journal" 2>&1; then
		ko "le parcours navigateur du premier devis a échoué"
		detail "$(tail -12 "$journal")"
		return 1
	fi
	ok "une organisation vide a produit son premier devis, au navigateur seul"
	ok "taxe, bibliothèque, prix, chantier et gel : aucun script, aucune écriture en base"

	if [[ ! -s "$constat" ]]; then
		ko "le parcours n'a pas rendu les identifiants du devis"
		return 1
	fi
	ESTIMATION_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["estimate_id"])' "$constat")
	VERSION_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version_id"])' "$constat")

	# Relecture — et RIEN d'autre. Ce que le navigateur a produit, on le relit
	# par HTTP pour disposer de l'empreinte de l'instantané, que l'écran ne
	# montre qu'abrégée. Aucune donnée n'est créée ici.
	local constat_api
	constat_api=$(python3 ops/parcours_devis.py --base "$API" --jeton "$JETON" \
		--verifier-seulement --estimation "$ESTIMATION_ID" --version "$VERSION_ID" \
		--exiger-tva 2>&1)
	if [[ $? -ne 0 ]]; then
		ko "le devis fabriqué au navigateur n'est pas relisible par l'API"
		detail "$(tail -3 <<<"$constat_api")"
		return 1
	fi

	TOTAL_HT=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["total_ht"])' "$constat_api")
	local total_ttc
	total_ttc=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["total_ttc"])' "$constat_api")
	EMPREINTE_DEVIS=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["snapshot_sha256"])' "$constat_api")

	ok "devis gelé : $TOTAL_HT HT, $total_ttc TTC"
	ok "le total du document s'additionne, sur le calcul, le CSV et l'aperçu HTML"
	detail "empreinte de l'instantané : ${EMPREINTE_DEVIS:0:16}…"
	printf '%s\n' "$constat_api" > "$TRAVAIL/devis.json"
}

# ===========================================================================
#  9. Le stockage persistant — de vraies pièces jointes
# ===========================================================================
#
# Cette étape écrivait elle-même un fichier dans le volume, faute de route de
# téléversement : elle éprouvait le MÉCANISME de persistance, et le disait.
# Il y a désormais une route, et le parcours navigateur vient de déposer un
# CCTP et sa révision à la souris, à travers le proxy. Ce qui est éprouvé ici
# n'est donc plus un mécanisme, mais les octets qu'un utilisateur a réellement
# confiés au produit — et qu'il doit retrouver après un redémarrage, une
# sauvegarde et une restauration.

#: Empreintes des originaux déposés, renseignées par `etape_stockage`, relues
#: après redémarrage et après restauration.
EMPREINTES_PIECES=""
CONSTAT_DOCUMENTS=""

#: Empreintes de TOUS les originaux du volume, une par ligne, triées, calculées
#: par le processus applicatif dans son propre conteneur.
empreintes_du_volume() {
	local projet="${1:-$PROJET}"
	docker compose --project-name "$projet" "${COMPOSITIONS[@]}" --env-file "$ENV_FICHIER" \
		exec -T api sh -c \
		"find /var/lib/metreo/documents -type f 2>/dev/null -exec sha256sum {} + | cut -d' ' -f1 | sort" \
		2>/dev/null | tr -d '\r' | grep -E '^[0-9a-f]{64}$' | sort
}

etape_stockage() {
	titre "Stockage persistant — les pièces déposées au navigateur"

	if [[ ! -s "$CONSTAT_DOCUMENTS" ]]; then
		ko "le parcours navigateur n'a pas rendu les empreintes des pièces jointes"
		return 1
	fi

	local attendues
	attendues=$(python3 -c '
import json, sys
print("\n".join(sorted(json.load(open(sys.argv[1]))["sha256"])))' "$CONSTAT_DOCUMENTS")

	EMPREINTES_PIECES=$(empreintes_du_volume)
	if [[ -z "$EMPREINTES_PIECES" ]]; then
		ko "aucun original dans le volume après le parcours"
		return 1
	fi
	ok "$(wc -l <<<"$EMPREINTES_PIECES") original(aux) écrit(s) dans le volume par l'application"

	# Inclusion et non égalité : le parcours des rôles dépose lui aussi un
	# métré, et le volume porte donc plus que le CCTP et sa révision. Ce qui
	# doit être vrai, c'est que TOUT ce que le navigateur a envoyé s'y
	# retrouve — comparé à l'empreinte calculée CÔTÉ CLIENT, et non à celle
	# que le serveur aurait recalculée sur ses propres octets. C'est la seule
	# comparaison qui prouve que rien n'a été transformé en chemin.
	local manquantes
	manquantes=$(comm -23 <(printf '%s\n' "$attendues") <(printf '%s\n' "$EMPREINTES_PIECES"))
	if [[ -z "$manquantes" ]]; then
		ok "les originaux déposés au navigateur sont sur le volume, au bit près"
	else
		ko "des originaux déposés manquent du volume"
		detail "$manquantes"
	fi

	# Non-root : une image qui traite des fichiers venant de tiers ne doit pas
	# les écrire en uid 0.
	local uid proprietaire
	uid=$(dans_api id -u 2>/dev/null | tr -d '\r')
	if [[ "$uid" != "0" ]]; then
		ok "le processus applicatif ne tourne pas en root (uid $uid)"
	else
		ko "le processus applicatif tourne en root"
	fi
	proprietaire=$(dans_api sh -c \
		"find /var/lib/metreo/documents -type f | head -1 | xargs stat -c %u" 2>/dev/null | tr -d '\r')
	verifier "les originaux appartiennent à ce même compte" "$uid" "$proprietaire"
}

# ===========================================================================
#  10. Redémarrage : la persistance tient-elle ?
# ===========================================================================

etape_redemarrage() {
	titre "Redémarrage de tous les conteneurs applicatifs"

	compose restart api web proxy >/dev/null 2>&1
	if attendre_code "$API/ready" 200 180; then
		ok "la pile répond de nouveau après redémarrage"
	else
		ko "la pile ne répond plus après redémarrage"
		return 1
	fi

	# La base : le devis gelé doit être identique, aux mêmes nombres et à la
	# même empreinte. C'est ce qui distingue « le service redémarre » de
	# « les données ont survécu ».
	#
	# Nouvelle session : le redémarrage a vidé la mémoire des processus, et un
	# jeton encore valable prouve en plus que la session ne dépendait d'aucun
	# état local.
	local apres
	apres=$(python3 ops/parcours_devis.py --base "$API" --jeton "$JETON" \
		--verifier-seulement --estimation "$ESTIMATION_ID" --version "$VERSION_ID" \
		--exiger-tva 2>&1)
	if [[ $? -ne 0 ]]; then
		ko "le devis n'est plus vérifiable après redémarrage"
		detail "$(tail -2 <<<"$apres")"
		return 1
	fi
	local ht_apres empreinte_apres
	ht_apres=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["total_ht"])' "$apres")
	empreinte_apres=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["snapshot_sha256"])' "$apres")
	verifier_montant "le total du devis est inchangé" "$TOTAL_HT" "$ht_apres"
	verifier "l'empreinte de l'instantané est inchangée" "$EMPREINTE_DEVIS" "$empreinte_apres"

	# Le stockage : les originaux doivent être là, aux mêmes octets.
	verifier "les pièces jointes ont survécu au redémarrage" \
		"$EMPREINTES_PIECES" "$(empreintes_du_volume)"

	# Et la session reste valable : le jeton n'était adossé à aucune mémoire
	# de processus.
	local code
	code=$(code_de -H "Authorization: Bearer $JETON" "$API/auth/me")
	verifier "la session survit au redémarrage" "200" "$code"
}

# ===========================================================================
#  11. Les trois sondes pendant une panne de base
# ===========================================================================
#
# Le contrôle le plus utile de cette répétition. Une panne de base est le
# moment où `/live`, `/ready` et `/health` doivent dire trois choses
# différentes — et où une confusion entre elles transforme une panne en
# cascade de redémarrages.

etape_sondes_pendant_panne() {
	titre "Sondes pendant une panne de base"

	local redemarrages_avant
	redemarrages_avant=$(docker inspect -f '{{.RestartCount}}' "$(compose ps -q api)" 2>/dev/null || echo 0)

	compose stop db >/dev/null 2>&1
	ok "base arrêtée"

	# Laisser les sondes constater la panne. Le pool de connexions garde des
	# sockets ouvertes : sans ce délai on mesurerait l'état d'avant.
	sleep 12

	# Interrogées EN INTERNE, pas par le proxy : ce sont des contrôles
	# d'orchestrateur, et c'est le seul endroit où l'on s'adresse
	# directement à l'API.
	local vivant pret sante
	vivant=$(compose exec -T api sh -c \
		"curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/api/v1/live" 2>/dev/null | tr -d '\r')
	pret=$(compose exec -T api sh -c \
		"curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/api/v1/ready" 2>/dev/null | tr -d '\r')
	sante=$(compose exec -T api sh -c \
		"curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8000/api/v1/health" 2>/dev/null | tr -d '\r')

	verifier "/live reste vert pendant la panne" "200" "$vivant"
	verifier "/ready passe au rouge pendant la panne" "503" "$pret"
	verifier "/health répond encore, en se déclarant dégradé" "200" "$sante"

	# Et surtout : la panne ne doit pas se propager. Le HEALTHCHECK de l'image
	# interroge `/live` ; s'il interrogeait `/ready`, Docker déclarerait les
	# conteneurs malsains et un orchestrateur les redémarrerait tous.
	local etat_sante
	etat_sante=$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q api)" 2>/dev/null || echo "?")
	verifier "le conteneur API reste sain aux yeux de Docker" "healthy" "$etat_sante"

	local redemarrages_apres
	redemarrages_apres=$(docker inspect -f '{{.RestartCount}}' "$(compose ps -q api)" 2>/dev/null || echo 0)
	verifier "aucun redémarrage de l'API pendant la panne" "$redemarrages_avant" "$redemarrages_apres"

	local web_debout
	web_debout=$(docker inspect -f '{{.State.Status}}' "$(compose ps -q web)" 2>/dev/null || echo "?")
	verifier "le front n'est pas tombé avec la base" "running" "$web_debout"

	compose start db >/dev/null 2>&1
	if attendre_code "$API/ready" 200 120; then
		ok "la base revenue, /ready repasse au vert sans intervention"
	else
		ko "/ready ne repasse pas au vert après le retour de la base"
	fi
}

# ===========================================================================
#  12. Sauvegarde
# ===========================================================================

ARCHIVE=""

etape_sauvegarde() {
	titre "Sauvegarde de la base et du stockage"

	local sortie
	sortie=$(BACKUP_COMPOSE_PROJECT="$PROJET" \
		BACKUP_COMPOSE_FILES="-f $RACINE/infra/docker-compose.staging.yml -f $RACINE/infra/docker-compose.repetition.yml" \
		BACKUP_ENV_FILE="$ENV_FICHIER" \
		BACKUP_DIR="$TRAVAIL/sauvegardes" \
		ops/sauvegarder.sh "$TRAVAIL/sauvegardes" 2>&1)
	if [[ $? -ne 0 ]]; then
		ko "la sauvegarde a échoué"
		detail "$(tail -3 <<<"$sortie")"
		return 1
	fi

	ARCHIVE=$(ls -1t "$TRAVAIL/sauvegardes"/metreo-*.tar.gz 2>/dev/null | head -1)
	if [[ -z "$ARCHIVE" || ! -s "$ARCHIVE" ]]; then
		ko "aucune archive produite"
		return 1
	fi
	ok "archive produite : $(basename "$ARCHIVE") ($(du -h "$ARCHIVE" | cut -f1))"

	# Une archive qui ne contient pas les deux morceaux ne restaurera rien.
	local contenu
	contenu=$(tar -tzf "$ARCHIVE" 2>/dev/null)
	if grep -q '\.dump$' <<<"$contenu"; then
		ok "l'archive contient le dump de la base"
	else
		ko "l'archive ne contient aucun dump"
	fi
	if grep -q 'storage\.tar$' <<<"$contenu"; then
		ok "l'archive contient le stockage"
	else
		ko "l'archive ne contient pas le stockage"
	fi

	# Le refus qui compte : déposer en clair chez un tiers.
	local refus
	refus=$(BACKUP_DESTINATION="s3://exemple-inexistant/metreo" \
		BACKUP_COMPOSE_PROJECT="$PROJET" \
		BACKUP_COMPOSE_FILES="-f $RACINE/infra/docker-compose.staging.yml -f $RACINE/infra/docker-compose.repetition.yml" \
		BACKUP_ENV_FILE="$ENV_FICHIER" \
		BACKUP_DIR="$TRAVAIL/refus" \
		ops/sauvegarder.sh "$TRAVAIL/refus" 2>&1)
	if grep -q "sans chiffrement" <<<"$refus"; then
		ok "la sauvegarde refuse de déposer en clair chez un tiers"
	else
		# Le script peut avoir échoué plus tôt (base injoignable avec ces
		# variables factices) : on ne conclut alors rien, plutôt que de
		# conclure à tort.
		detail "refus non observé dans ce contexte — contrôlé par ops/tests/test_demonstration.py"
	fi
}

# ===========================================================================
#  13. Restauration dans une SECONDE pile, isolée
# ===========================================================================
#
# Une restauration par-dessus la pile d'origine ne prouverait rien : on ne
# saurait pas distinguer « restauré » de « jamais effacé ». La seconde pile
# part de volumes vides.

etape_restauration() {
	titre "Restauration dans une seconde pile isolée"

	if [[ -z "$ARCHIVE" ]]; then
		ko "aucune archive à restaurer"
		return 1
	fi

	local utilisateur base motdepasse
	utilisateur=$(grep -E '^POSTGRES_USER=' "$ENV_FICHIER" | cut -d= -f2-)
	base=$(grep -E '^POSTGRES_DB=' "$ENV_FICHIER" | cut -d= -f2-)
	motdepasse=$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FICHIER" | cut -d= -f2-)

	# La seconde pile : mêmes images, volumes neufs, autre nom de projet.
	# `--no-deps db api` : ni proxy ni front, on ne restaure pas pour servir.
	docker compose --project-name "$PROJET_RESTAURE" "${COMPOSITIONS[@]}" \
		--env-file "$ENV_FICHIER" up -d --wait --wait-timeout 240 db >/dev/null 2>&1
	docker compose --project-name "$PROJET_RESTAURE" "${COMPOSITIONS[@]}" \
		--env-file "$ENV_FICHIER" up -d --no-deps api >/dev/null 2>&1
	sleep 8
	ok "seconde pile montée, volumes neufs"

	local sortie
	sortie=$(RESTORE_COMPOSE_PROJECT="$PROJET_RESTAURE" \
		RESTORE_COMPOSE_FILES="-f $RACINE/infra/docker-compose.staging.yml -f $RACINE/infra/docker-compose.repetition.yml" \
		RESTORE_ENV_FILE="$ENV_FICHIER" \
		POSTGRES_USER="$utilisateur" POSTGRES_DB="$base" POSTGRES_PASSWORD="$motdepasse" \
		ops/restaurer.sh "$ARCHIVE" "metreo_restore_repetition" 2>&1)
	local code=$?
	printf '%s\n' "$sortie" > "$JOURNAUX/restauration.log"

	if [[ $code -ne 0 ]]; then
		ko "la restauration a échoué"
		detail "$(tail -4 <<<"$sortie")"
		return 1
	fi
	ok "base et stockage restaurés dans la seconde pile"

	# Le contrôle de restauration a tourné ; on lit ce qu'il a constaté.
	for attendu in "organisations" "utilisateurs" "chaine d'audit" "comptes connectables"; do
		if grep -qi "$attendu" <<<"$sortie"; then
			ok "contrôle présent : $attendu"
		else
			ko "contrôle absent du rapport de restauration : $attendu"
		fi
	done
	if grep -qi "intègre" <<<"$sortie"; then
		ok "la chaîne d'audit est intègre après restauration"
	else
		ko "la chaîne d'audit n'est pas déclarée intègre"
	fi

	# Le devis gelé, relu DANS la pile restaurée. C'est le contrôle qui
	# distingue « des lignes sont revenues » de « le document est le même ».
	local restaure
	restaure=$(docker compose --project-name "$PROJET_RESTAURE" "${COMPOSITIONS[@]}" \
		--env-file "$ENV_FICHIER" exec -T \
		-e METREO_DATABASE_URL="postgresql+psycopg://$utilisateur:$motdepasse@db:5432/metreo_restore_repetition" \
		api python -c "
from decimal import Decimal
from sqlalchemy import select
from metreo_api.db import get_session_factory
from metreo_api.models import EstimateVersion, Organization, User
with get_session_factory()() as s:
    v = s.get(EstimateVersion, '$VERSION_ID')
    print('|'.join([
        v.status if v else 'ABSENT',
        str(v.document_total_ht) if v and v.document_total_ht is not None else 'NULL',
        v.snapshot_sha256 if v else 'ABSENT',
        str(s.scalar(select(Organization.name).limit(1)) or 'ABSENT'),
        str(s.scalar(select(User.email).limit(1)) or 'ABSENT'),
    ]))
" 2>/dev/null | tr -d '\r' | tail -1)

	IFS='|' read -r etat total empreinte organisation courriel <<<"$restaure"
	verifier "le devis restauré est toujours gelé" "frozen" "$etat"
	verifier_montant "le total du document est celui d'avant la sauvegarde" "$TOTAL_HT" "$total"
	verifier "l'empreinte de l'instantané est identique" "$EMPREINTE_DEVIS" "$empreinte"
	verifier "l'organisation est revenue" "$ORGANISATION" "$organisation"
	verifier "le premier administrateur est revenu" "$ADMIN" "$courriel"

	# Les pièces jointes : mêmes octets, dans une pile qui n'a jamais vu le
	# navigateur qui les a déposées.
	verifier "les originaux sont revenus à l'identique" \
		"$EMPREINTES_PIECES" "$(empreintes_du_volume "$PROJET_RESTAURE")"

	# Et surtout : ils se TÉLÉCHARGENT encore. Comparer des empreintes sur le
	# disque ne dit rien de la route — un `storage_key` restauré qui ne
	# désignerait plus rien rendrait 410 sans que le fichier ait bougé.
	local telecharge
	telecharge=$(docker compose --project-name "$PROJET_RESTAURE" "${COMPOSITIONS[@]}" \
		--env-file "$ENV_FICHIER" exec -T \
		-e METREO_DATABASE_URL="postgresql+psycopg://$utilisateur:$motdepasse@db:5432/metreo_restore_repetition" \
		api python -c "
import hashlib
from fastapi.testclient import TestClient
from sqlalchemy import select
from metreo_api.db import get_session_factory
from metreo_api.main import create_app
from metreo_api.models import DocumentRevision

with get_session_factory()() as session:
    revisions = session.scalars(
        select(DocumentRevision).order_by(DocumentRevision.revision_number)
    ).all()
    attendus = [(r.document_id, r.id, r.sha256) for r in revisions]

# Le jeton du parcours reste valable : même secret de signature, mêmes comptes.
entetes = {'Authorization': 'Bearer $JETON'}
with TestClient(create_app()) as client:
    for document_id, revision_id, empreinte in attendus:
        reponse = client.get(
            f'/api/v1/documents/{document_id}/revisions/{revision_id}/content',
            headers=entetes,
        )
        obtenue = hashlib.sha256(reponse.content).hexdigest()
        marque = 'ok' if reponse.status_code == 200 and obtenue == empreinte else 'ko'
        print(f'{marque} {reponse.status_code} {obtenue}')
" 2>/dev/null | tr -d '\r' | grep -E '^(ok|ko) ')

	if [[ -z "$telecharge" ]]; then
		ko "aucun téléchargement n'a pu être rejoué dans la pile restaurée"
	elif grep -q '^ko ' <<<"$telecharge"; then
		ko "un original restauré ne se télécharge pas à l'identique"
		detail "$telecharge"
	else
		ok "$(grep -c '^ok ' <<<"$telecharge") original(aux) retéléchargé(s) à l'identique après restauration"
	fi
}

# ===========================================================================
#  14. Les journaux ne portent ni jeton, ni secret, ni code
# ===========================================================================

etape_journaux_propres() {
	titre "Aucun secret en clair dans les journaux"

	recolter_journaux
	local tout="$TRAVAIL/journaux-assembles.txt"
	cat "$JOURNAUX"/*.log > "$tout" 2>/dev/null || true
	if [[ ! -s "$tout" ]]; then
		ko "aucun journal recueilli — le contrôle ne prouverait rien"
		return 1
	fi
	detail "$(wc -l < "$tout") lignes analysées"

	# Les valeurs RÉELLES de cette exécution, cherchées telles quelles. Un
	# motif générique dirait « rien trouvé » aussi bien parce qu'il n'y a rien
	# que parce qu'il cherche mal.
	local secret_client jwt
	secret_client=$(grep -E '^METREO_OIDC_CLIENT_SECRET=' "$ENV_FICHIER" | cut -d= -f2-)
	jwt=$(grep -E '^METREO_JWT_SECRET=' "$ENV_FICHIER" | cut -d= -f2-)

	local fuite=0
	verifier_absence() {
		local quoi="$1" valeur="$2"
		[[ -z "$valeur" ]] && return 0
		if grep -qF -- "$valeur" "$tout"; then
			ko "$quoi apparaît en clair dans les journaux"
			grep -nF -- "$valeur" "$tout" | head -2 | sed 's/^/       /' >&2
			fuite=1
		else
			ok "$quoi n'apparaît pas dans les journaux"
		fi
	}

	verifier_absence "le secret client OIDC" "$secret_client"
	verifier_absence "le secret de signature des sessions" "$jwt"
	verifier_absence "le jeton de session obtenu" "$JETON"

	# Et les formes génériques : un JWT commence par `eyJ`, un en-tête
	# d'autorisation par `Bearer`.
	for motif in 'eyJ[A-Za-z0-9_-]\{20,\}' 'Bearer [A-Za-z0-9._-]\{20,\}' 'login_code=[A-Za-z0-9_-]\{16,\}'; do
		if grep -qE "$motif" "$tout"; then
			ko "un motif de secret apparaît dans les journaux : $motif"
			grep -nE "$motif" "$tout" | head -2 | sed 's/^/       /' >&2
			fuite=1
		fi
	done
	[[ $fuite -eq 0 ]] && ok "aucun motif de jeton, de secret ou de code de connexion"

	# Les journaux conservés comme artefact sont ceux-ci : on vient de vérifier
	# qu'ils ne portent rien de sensible.
	cp "$tout" "$JOURNAUX/journaux-nettoyes.txt"
}

# ===========================================================================
#  Déroulé
# ===========================================================================

if [[ ! -f "$ENV_FICHIER" ]]; then
	echo "Fichier d'environnement absent : $ENV_FICHIER" >&2
	echo "  cp infra/repetition.env.example infra/repetition.env" >&2
	exit 2
fi

printf '\033[1mRÉPÉTITION DE PRÉPRODUCTION\033[0m\n'
printf '  pile            %s\n' "$PROJET"
printf '  pile restaurée  %s\n' "$PROJET_RESTAURE"
printf '  application     %s\n' "$BASE"
printf '  rapport         %s\n' "$RAPPORT"

etape_refus_configuration
if ! etape_demarrage; then
	# Sans pile debout, les étapes suivantes produiraient une dizaine
	# d'échecs en cascade qui masqueraient la cause unique. On s'arrête, on
	# recueille les journaux, et on le dit.
	titre "Arrêt"
	ko "la pile n'a pas démarré : les étapes suivantes ne sont pas jouées"
	recolter_journaux
	printf '\n\033[31mRÉPÉTITION INTERROMPUE\033[0m — %d contrôles passés, %d en échec\n' \
		"${#ETAPES_OK[@]}" "${#ETAPES_KO[@]}"
	for echec in "${ETAPES_KO[@]}"; do printf '  · %s\n' "$echec"; done
	exit 1
fi
etape_dev_login_absent
etape_aucune_demonstration
etape_migrations_une_seule_fois
etape_bootstrap || true
etape_connexion_oidc || true
etape_devis || true
etape_stockage || true
etape_redemarrage || true
etape_sondes_pendant_panne || true
etape_sauvegarde || true
etape_restauration || true
etape_journaux_propres || true

# ===========================================================================
#  Verdict
# ===========================================================================

titre "Verdict"
printf '   %d contrôles passés, %d en échec\n\n' "${#ETAPES_OK[@]}" "${#ETAPES_KO[@]}"

if ((${#ETAPES_KO[@]} > 0)); then
	printf '   \033[31mÉchecs :\033[0m\n'
	for echec in "${ETAPES_KO[@]}"; do printf '     · %s\n' "$echec"; done
	printf '\n'
fi

python3 - "$RAPPORT" "${#ETAPES_OK[@]}" "${#ETAPES_KO[@]}" \
	"$TOTAL_HT" "$EMPREINTE_DEVIS" "$(head -1 <<<"$EMPREINTES_PIECES")" "$(basename "${ARCHIVE:-}")" <<'PY'
import json
import sys

chemin, passes, echecs, total_ht, empreinte_devis, empreinte_piece, archive = sys.argv[1:8]
rapport = {
    "verdict": "vert" if int(echecs) == 0 else "rouge",
    "controles_passes": int(passes),
    "controles_en_echec": int(echecs),
    "devis": {
        "total_ht": total_ht or None,
        "snapshot_sha256": empreinte_devis or None,
    },
    "stockage": {"empreinte_piece": empreinte_piece or None},
    "sauvegarde": {"archive": archive or None},
    "note": (
        "Répétition locale : aucun port hors 127.0.0.1, aucune image publiée, "
        "aucun service externe appelé, toutes les ressources détruites à la fin."
    ),
}
with open(chemin, "w", encoding="utf-8") as f:
    json.dump(rapport, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"   rapport écrit : {chemin}")
PY

if ((${#ETAPES_KO[@]} > 0)); then
	printf '\n\033[31mRÉPÉTITION EN ÉCHEC\033[0m\n'
	exit 1
fi
printf '\n\033[32mRÉPÉTITION COMPLÈTE ET VERTE\033[0m\n'
