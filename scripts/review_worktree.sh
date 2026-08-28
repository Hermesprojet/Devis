#!/usr/bin/env bash
# Un arbre de travail jetable pour les relectures adversariales.
#
# Une revue mute le code pour éprouver ses garde-fous — neutraliser un verrou,
# retirer une condition, inverser un ordre. Faire cela dans l'arbre candidat
# est dangereux : une version fautive y est déjà restée en place et serait
# repartie sous une étiquette sans rapport si un hook ne l'avait pas vue.
#
# L'isolation de git suffit ; aucun garde-fou supplémentaire n'est nécessaire.
set -euo pipefail

RACINE="$(git rev-parse --show-toplevel)"
NOM="revue-$(git rev-parse --short HEAD)"
CHEMIN="${TMPDIR:-/tmp}/metreo-$NOM"

case "${1:-aide}" in
  créer|creer|create)
    git -C "$RACINE" worktree add --detach "$CHEMIN" HEAD >&2
    echo "$CHEMIN"
    ;;
  nettoyer|clean)
    git -C "$RACINE" worktree remove --force "$CHEMIN" 2>/dev/null || true
    git -C "$RACINE" worktree prune
    echo "worktree de revue supprimé : $CHEMIN" >&2
    ;;
  *)
    echo "usage : $0 {créer|nettoyer}" >&2
    echo "  créer    — crée un worktree détaché sur HEAD et affiche son chemin" >&2
    echo "  nettoyer — le supprime" >&2
    exit 2
    ;;
esac
