"""Rendre UNE tuile, dans un processus qui meurt juste après.

    python -m metreo_api.rendu_tuile --fichier P --page 1 \\
        --zone 0.10 0.11 0.23 0.20 --sortie tuile.png

**Pourquoi un processus séparé, et pourquoi ce n'est pas un raffinement.**

Mesuré sur les quatre plans réels : charger une page de PDF coûte de **54 à
422 mégaoctets** de mémoire résidente de plus — 422 pour le plan d'étage le
plus dense, 54 pour la coupe. L'écart suit la densité du dessin, pas la taille
du fichier : le plan le plus lourd des quatre sur le disque est le moins cher
des deux en mémoire. Et
cette mémoire **n'est pas rendue** au système quand on ferme le document :
vérifié, un processus parti de 12 Mo passe à 489 Mo après le plan le plus
dense, documents fermés, et y reste — le plan suivant n'ajoute rien, il
réutilise ce qui est déjà réservé.

Ce n'est donc pas une fuite — la consommation ne croît pas sans fin — mais un
**plancher définitif** : tout processus qui a rendu une page dense en garde la
trace jusqu'à sa mort. Un travailleur d'API qui analyse un grand format pèse ensuite un
demi-gigaoctet, et il le pèse pour toujours. Multiplié par le nombre de
travailleurs, c'est la mémoire du conteneur.

Un processus séparé règle le problème à la racine, parce qu'un processus qui
se termine rend tout : vérifié, le parent reste à **11 Mo** après quatre
rendus successifs, là où le même travail en direct l'aurait mené à 489 Mo. Le fils
le plus lourd, lui, a culminé à 510 Mo — et les a rendus en mourant.

Le prix est le temps : mesuré sur les quatre plans réels, **220 ms à 5,3
secondes** par tuile, dominées par le chargement de la page — l'écart tient à
la densité du dessin, pas à la taille du fichier. C'est pourquoi le résultat
est mis en cache sur le volume par `services/tuiles.py` : une tuile pèse 26 à
246 Ko (médiane 92 Ko sur cent zones), et la deuxième demande retombe à 0,3
milliseconde.

**Les deux garde-fous de ce processus-ci** : une limite d'adressage posée
avant d'importer PDFium, pour qu'un fichier hostile tue le fils plutôt que le
conteneur, et une sortie en code nommé que le parent sait interpréter.
"""

from __future__ import annotations

import argparse
import resource
import sys
from pathlib import Path

#: La mémoire maximale que ce processus peut s'allouer, en octets.
#:
#: Mesuré : le fils le plus lourd des quatre plans réels a culminé à 510 Mo.
#: Un gigaoctet et demi laisse donc trois fois la marge pour une page encore
#: plus dense, et arrête net un fichier forgé pour épuiser la machine.
#:
#: La limite est posée AVANT d'importer PDFium : une bibliothèque C qui a déjà
#: réservé son arène ne la rendra pas, et `setrlimit` ne s'applique qu'aux
#: allocations futures.
PLAFOND_MEMOIRE = 1536 * 1024 * 1024

#: Les codes de sortie, et ce qu'ils disent au parent.
#:
#: Un code distinct par cause : le parent doit pouvoir dire « ce PDF est trop
#: lourd » plutôt que « le rendu a échoué », qui n'aide personne.
SORTIE_OK = 0
SORTIE_REFUS = 3
SORTIE_MEMOIRE = 4
SORTIE_ARGUMENTS = 2


def _borner_la_memoire() -> None:
    """Pose le plafond, sans faire échouer un système qui ne le permet pas."""
    try:
        # La limite SOUPLE actuelle ne sert à rien ici : on la remplace. Seule
        # la DURE compte, parce qu'on ne peut pas la dépasser.
        _, dur = resource.getrlimit(resource.RLIMIT_AS)
        plafond = PLAFOND_MEMOIRE if dur == resource.RLIM_INFINITY else min(PLAFOND_MEMOIRE, dur)
        resource.setrlimit(resource.RLIMIT_AS, (plafond, dur))
    except (ValueError, OSError):  # pragma: no cover - dépend du système hôte
        # Un conteneur peut refuser d'abaisser la limite. On continue : le
        # rendu reste utile, et la protection par cgroup joue encore.
        print("avertissement : plafond mémoire non posé", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="metreo_api.rendu_tuile",
        description="Rend une zone d'une page de PDF en PNG, puis se termine.",
    )
    analyseur.add_argument("--fichier", required=True, help="le PDF à lire")
    analyseur.add_argument("--page", type=int, required=True, help="1-indexée")
    analyseur.add_argument(
        "--zone",
        nargs=4,
        type=float,
        required=True,
        metavar=("X0", "Y0", "X1", "Y1"),
        help="la zone dans le repère de l'écran, [0,1], origine en haut à gauche",
    )
    analyseur.add_argument("--sortie", required=True, help="le PNG à écrire")
    analyseur.add_argument(
        "--cote",
        type=int,
        default=None,
        help="le plus grand côté de la tuile, en pixels",
    )
    arguments = analyseur.parse_args(argv)

    _borner_la_memoire()

    # Importé APRÈS le plafond, et à l'intérieur de `main` : l'import lui-même
    # réserve de la mémoire, et il doit le faire sous la limite.
    from .services import rendu_pdf

    try:
        tuile = rendu_pdf.rendre_une_zone(
            Path(arguments.fichier),
            page=arguments.page,
            zone=tuple(arguments.zone),  # type: ignore[arg-type]
            **({"cote": arguments.cote} if arguments.cote else {}),
        )
    except rendu_pdf.RenduRefuse as refus:
        # Le code du refus part sur la sortie d'erreur : le parent le relit et
        # le rend tel quel, au lieu d'inventer un message.
        print(refus.code, file=sys.stderr)
        return SORTIE_REFUS
    except MemoryError:
        print("memoire_epuisee", file=sys.stderr)
        return SORTIE_MEMOIRE

    sortie = Path(arguments.sortie)
    sortie.write_bytes(tuile.png)
    # La hauteur que la zone demandée atteint dans l'image, que le parent
    # conserve avec la tuile : quand cette zone est la boîte d'une cote, c'est
    # elle qui dit si la cote est relisible, et elle serait perdue si seul le
    # PNG traversait.
    print(f"{tuile.largeur} {tuile.hauteur} {tuile.hauteur_de_la_zone_px or 0:.2f}")
    return SORTIE_OK


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())
