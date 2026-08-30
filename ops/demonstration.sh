#!/usr/bin/env bash
# Pilote la démonstration locale. Appelé par les cibles `make demo-*`.
#
#     ops/demonstration.sh up | status | down | reset
#
# Ce script porte les garde-fous plutôt que le Makefile : la logique de refus
# de `reset` mérite d'être lisible, et un Makefile est un mauvais endroit pour
# lire une condition.
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSITION="$RACINE/infra/docker-compose.demo.yml"

# Le nom de projet est LA garantie de `reset`. Il est figé ici, jamais lu dans
# l'environnement : une variable surchargeable ferait de `reset` une commande
# capable de détruire n'importe quel projet.
PROJET="metreo-demo"

# Les seuls volumes que `reset` a le droit de supprimer, nommés un par un.
# Une liste explicite plutôt qu'un filtre : un filtre qui déraperait
# emporterait des volumes voisins, et personne ne relit un filtre avant de
# taper la commande.
VOLUMES=("${PROJET}_metreo-demo-base" "${PROJET}_metreo-demo-stockage")

URL_WEB="http://127.0.0.1:3000"
URL_API="http://127.0.0.1:8000/api/v1"

COMPOSE=(docker compose --project-name "$PROJET" -f "$COMPOSITION")

COMPTES=$(cat <<'TXT'
  admin@dubois.demo      Administrateur — Terrassements Dubois SA
  metreur@dubois.demo    Métreur / deviseur — Terrassements Dubois SA
  lecteur@dubois.demo    Lecteur / auditeur — Terrassements Dubois SA
  admin@janssens.demo    Administrateur — Wegenbouw Janssens NV
TXT
)

avertissement() {
	cat <<'TXT'

  ┌──────────────────────────────────────────────────────────────────────┐
  │  DÉMONSTRATION LOCALE — NE JAMAIS EXPOSER SUR INTERNET               │
  │                                                                      │
  │  Connexion sans mot de passe, secret de session public, données      │
  │  fictives. Les ports n'écoutent que sur 127.0.0.1 : cette machine,   │
  │  jamais le réseau local, jamais Internet.                            │
  └──────────────────────────────────────────────────────────────────────┘

TXT
}

exiger_docker() {
	if ! docker info >/dev/null 2>&1; then
		echo "Docker ne répond pas. Démarrez Docker Desktop, puis relancez." >&2
		exit 1
	fi
}

# Attend qu'une URL réponde le code attendu. Le point de `demo-up` : un
# message « c'est prêt » affiché avant que ça le soit envoie la personne sur
# une page blanche, et lui fait croire que le produit est cassé.
attendre() {
	local libelle="$1" url="$2" attendu="$3" limite="${4:-180}"
	local i code
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

commande_up() {
	exiger_docker
	avertissement
	echo "Construction et démarrage…"
	"${COMPOSE[@]}" up -d --build --wait-timeout 300

	echo
	echo "Attente des services :"
	# `/ready` et non `/live` : la vivacité serait verte avant que la base ne
	# réponde, et le premier écran échouerait.
	attendre "API (base joignable)" "$URL_API/ready" 200
	attendre "interface web" "$URL_WEB" 200

	echo
	echo "  Ouvrez : $URL_WEB"
	echo
	echo "  Comptes de démonstration (aucun mot de passe) :"
	echo "$COMPTES"
	echo
	echo "  make demo-status  état    ·  make demo-down  arrêt (données gardées)"
	echo
}

commande_status() {
	exiger_docker
	echo "Pile « $PROJET » :"
	"${COMPOSE[@]}" ps
	echo
	echo "Sondes :"
	for sonde in live ready health; do
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_API/$sonde" 2>/dev/null || echo 000)
		printf '  %-8s %s\n' "$sonde" "$code"
	done
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_WEB" 2>/dev/null || echo 000)
	printf '  %-8s %s\n' "web" "$code"
	echo
	echo "Données :"
	for volume in "${VOLUMES[@]}"; do
		if docker volume inspect "$volume" >/dev/null 2>&1; then
			printf '  %-40s présent\n' "$volume"
		else
			printf '  %-40s absent\n' "$volume"
		fi
	done
	echo
	echo "  $URL_WEB"
	echo "$COMPTES"
}

commande_down() {
	exiger_docker
	# PAS de `-v`, et ce n'est pas un oubli : arrêter n'est pas effacer.
	# Quelqu'un qui a saisi un devis d'essai doit le retrouver demain.
	"${COMPOSE[@]}" down
	echo
	echo "Arrêté. Les données sont conservées — « make demo-up » les retrouve."
	echo "Pour les effacer : make demo-reset"
}

commande_reset() {
	# La confirmation AVANT tout le reste, y compris avant de demander à
	# Docker s'il répond. Deux raisons : un refus ne doit dépendre de rien
	# d'extérieur, et la garde reste éprouvable sur une machine sans démon.
	echo
	echo "  Cette commande efface les données de la démonstration :"
	for volume in "${VOLUMES[@]}"; do
		echo "      $volume"
	done
	echo
	echo "  Rien d'autre n'est touché : ni vos autres projets Docker, ni les"
	echo "  volumes portant un autre nom, ni aucun fichier de cet ordinateur."
	echo

	# Confirmation explicite, et un mot à taper plutôt qu'un « o » : un
	# caractère unique se donne par réflexe, un mot demande de lire.
	if [[ "${DEMO_RESET_CONFIRME:-}" == "EFFACER" ]]; then
		echo "  Confirmé par DEMO_RESET_CONFIRME."
	else
		if [[ ! -t 0 ]]; then
			echo "Refus : « reset » demande une confirmation et l'entrée n'est pas" >&2
			echo "  un terminal. Pour un script : DEMO_RESET_CONFIRME=EFFACER" >&2
			exit 1
		fi
		printf '  Tapez EFFACER pour confirmer : '
		read -r reponse
		if [[ "$reponse" != "EFFACER" ]]; then
			echo
			echo "Refus : confirmation absente. Rien n'a été effacé." >&2
			exit 1
		fi
	fi

	exiger_docker
	"${COMPOSE[@]}" down
	for volume in "${VOLUMES[@]}"; do
		# Chaque nom est vérifié juste avant la suppression. Une variable mal
		# construite plus haut ne peut donc pas viser un volume étranger : la
		# garde est ici, au contact de la commande destructrice.
		case "$volume" in
		"${PROJET}_"*) ;;
		*)
			echo "Refus : « $volume » n'appartient pas au projet « $PROJET »." >&2
			exit 1
			;;
		esac
		if docker volume inspect "$volume" >/dev/null 2>&1; then
			docker volume rm "$volume" >/dev/null
			echo "  effacé : $volume"
		else
			echo "  déjà absent : $volume"
		fi
	done
	echo
	echo "Données effacées. « make demo-up » repart d'un jeu de démonstration neuf."
}

case "${1:-}" in
up) commande_up ;;
status) commande_status ;;
down) commande_down ;;
reset) commande_reset ;;
*)
	echo "usage: $0 up|status|down|reset" >&2
	exit 2
	;;
esac
