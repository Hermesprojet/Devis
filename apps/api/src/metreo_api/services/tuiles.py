"""Les tuiles de détail : les produire hors du processus, les garder sur le volume.

Une tuile est l'agrandissement d'une petite zone d'un plan. Elle existe pour
une raison précise, et mesurée : **l'aperçu pleine page ne permet pas de
relire une cote**. À 2 000 pixels sur le grand côté, la hauteur médiane d'un
caractère y est de **2,1 à 2,5 pixels**, et un pixel vaut 0,59 à 0,85 mm de
papier — soit 12 à 42 mm d'ouvrage aux échelles 1:20 à 1:50. Confirmer une
mesure, ou simplement pointer deux points pour calibrer, demande vingt fois
mieux. Mesuré aussi, pour chiffrer l'autre voie : amener ce texte au seuil de
lecture de 8 pixels demanderait un aperçu de 6 480 à 7 590 pixels de côté,
soit 63 à 124 Mo de bitmap — rendus en entier pour en regarder 5 %.

**Les trois chiffres qui dictent toute la conception.**

| Mesuré sur les quatre plans réels | Valeur |
| --- | --- |
| Charger une page et rendre la tuile, en direct | 143 à 4 972 ms, et **+54 à +422 Mo** résidents |
| Rendre la tuile, page déjà chargée | 2 à 34 ms |
| Poids d'une tuile, sur 100 zones d'une grille | 26 à 246 Ko, **médiane 92 Ko** |

Le coût est donc **entièrement dans le chargement de la page**, et il est à la
fois lent et lourd. Pire : la mémoire n'est pas rendue au système après
fermeture — vérifié, 489 Mo restent résidents, et le plan suivant n'y ajoute
rien : c'est un plancher, pas une fuite. Un travailleur d'API qui a rendu une
page dense pèse un demi-gigaoctet jusqu'à sa mort.

**D'où les deux décisions de ce module**, et elles découlent l'une de l'autre :

1. **Le rendu se fait dans un processus séparé** (`metreo_api.rendu_tuile`),
   qui meurt aussitôt. Vérifié : le parent reste à 11 Mo après quatre rendus
   successifs, contre 489 Mo pour le même travail en direct. Le fils porte en
   plus un plafond d'adressage, pour qu'un fichier forgé le tue lui et non le
   conteneur.

2. **La tuile produite est conservée sur le volume**, comme l'aperçu et le
   constat. La deuxième demande ne coûte plus qu'une lecture de fichier. C'est
   ce qui rend le relevé d'une liste de mesures praticable : mesuré, la
   première demande coûte 226 à 5 376 ms et la seconde **0,3 à 0,5 ms**.

**Ce que ce cache n'est pas.** Il ne garde aucune page en mémoire. Un cache de
pages serait la solution évidente — rendre dix tuiles d'une même page pour un
seul chargement — et c'est précisément ce que les 422 Mo interdisent : trois
pages denses épuiseraient un conteneur ordinaire. Le cache porte donc sur le
RÉSULTAT, qui pèse 92 Ko en médiane, et jamais sur ce qui l'a produit.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .document_storage import StockageLocal
from .rendu_pdf import DOSSIER_RENDUS

logger = logging.getLogger("metreo.api")

#: Le dossier des tuiles, à part des aperçus.
#:
#: Séparé parce qu'elles ne se purgent pas au même rythme : un aperçu existe
#: une fois par page, une tuile autant de fois qu'on a zoomé quelque part.
DOSSIER_TUILES = f"{DOSSIER_RENDUS}/tuiles"

#: Au-delà, le rendu est abandonné.
#:
#: Mesuré : 5,3 secondes pour la page la plus lourde, appel du fils compris.
#: Soixante secondes
#: laissent dix fois la marge, et arrêtent un fichier qui ferait boucler
#: PDFium — un processus bloqué retient son demi-gigaoctet indéfiniment.
DELAI_MAXIMAL_EN_SECONDES = 60

#: La précision à laquelle une zone est arrondie pour former sa clé de cache.
#:
#: Sans arrondi, deux clics d'un pixel d'écart produiraient deux tuiles
#: différentes, et le cache ne servirait jamais. Au millième de page — deux
#: pixels sur un aperçu de deux mille — deux demandes voisines partagent leur
#: tuile, et le décalage reste invisible à l'œil puisque la tuile porte une
#: marge de huit fois la hauteur du texte.
PRECISION_DE_LA_CLE = 3

#: Deux plafonds, et c'est le premier atteint qui arrête l'écriture.
#:
#: **Pourquoi deux.** Un compte seul ne borne rien d'utile : mesuré sur une
#: grille de cent zones, une tuile pèse de 26 à 246 Ko selon la densité du
#: dessin. Cinq cents tuiles font donc 45 Mo au poids médian et **120 Mo au
#: pire**, pour un original qui en pèse 5. Le plafond qui compte est celui des
#: OCTETS ; celui du nombre reste comme garde-fou bon marché, parce qu'il
#: s'évalue sans interroger la taille de chaque fichier.
#:
#: Soixante-quatre mébioctets : une dizaine de fois l'original, et le même
#: ordre de grandeur que les aperçus pleine page d'un document de dix pages
#: (1,2 Mo chacun). Au-delà, le rendu continue de fonctionner — il n'est
#: simplement plus mis en cache, et le dire vaut mieux que remplir le volume
#: en silence.
PLAFOND_DE_TUILES_PAR_REVISION = 500
PLAFOND_D_OCTETS_PAR_REVISION = 64 * 1024 * 1024


class TuileRefusee(Exception):
    """La tuile n'a pas pu être produite, et on dit pourquoi."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Tuile:
    """Une tuile servie, et d'où elle vient."""

    png: bytes
    largeur: int
    hauteur: int
    #: La hauteur que le texte atteint réellement, en pixels. En deçà de seize,
    #: la cote reste difficile à relire, et l'écran doit pouvoir le dire au
    #: lieu d'afficher un flou sans un mot.
    hauteur_du_texte_px: float
    #: Vrai quand elle a été relue du volume plutôt que rendue.
    depuis_le_cache: bool


def cle_de_la_tuile(
    organization_id: str, revision_id: str, page: int, zone: tuple[float, float, float, float]
) -> str:
    """La clé d'une tuile. Déterministe, et arrondie pour que le cache serve.

    L'empreinte porte sur la zone ARRONDIE : c'est ce qui fait qu'un second
    clic à un pixel près retrouve la tuile du premier. Elle est courte — douze
    caractères — parce qu'une collision y coûterait l'affichage d'une mauvaise
    zone, ce qui se verrait immédiatement, et non une fuite entre documents :
    l'organisation et la révision sont dans le CHEMIN, pas dans l'empreinte.
    """
    arrondie = tuple(round(valeur, PRECISION_DE_LA_CLE) for valeur in zone)
    empreinte = hashlib.sha256(f"{page}:{arrondie}".encode()).hexdigest()[:12]
    return f"{DOSSIER_TUILES}/{organization_id}/{revision_id}-p{page}-{empreinte}.png"


def _rendre_hors_processus(
    original: Path, *, page: int, zone: tuple[float, float, float, float], sortie: Path
) -> tuple[int, int, float]:
    """Lance le fils, attend, et traduit sa sortie.

    Le fils est invoqué par `-m` sur le MÊME interpréteur que le parent : c'est
    ce qui garantit qu'il trouve le même environnement virtuel et les mêmes
    versions, y compris dans l'image où l'API tourne sous un utilisateur non
    privilégié.
    """
    commande = [
        sys.executable,
        "-m",
        "metreo_api.rendu_tuile",
        "--fichier",
        str(original),
        "--page",
        str(page),
        "--zone",
        *(f"{valeur:.6f}" for valeur in zone),
        "--sortie",
        str(sortie),
    ]
    try:
        resultat = subprocess.run(
            commande,
            capture_output=True,
            text=True,
            timeout=DELAI_MAXIMAL_EN_SECONDES,
            check=False,
        )
    except subprocess.TimeoutExpired as expiration:
        raise TuileRefusee(
            "rendu_trop_long",
            f"Le rendu de cette zone a dépassé {DELAI_MAXIMAL_EN_SECONDES} "
            "secondes et a été arrêté.",
        ) from expiration

    if resultat.returncode != 0:
        # Le fils écrit un CODE sur sa sortie d'erreur ; on le rend tel quel
        # plutôt que d'inventer un message par-dessus.
        cause = (resultat.stderr or "").strip().splitlines()
        code = cause[-1] if cause else f"code_{resultat.returncode}"
        logger.info(
            "tuile_refusee",
            extra={"code": code, "sortie": resultat.returncode},
        )
        raise TuileRefusee(
            "rendu_impossible",
            f"La zone demandée n'a pas pu être rendue ({code}).",
        )

    morceaux = (resultat.stdout or "").split()
    if len(morceaux) != 3:  # pragma: no cover - le fils garantit ce format
        raise TuileRefusee(
            "rendu_illisible", "Le rendu n'a pas rendu compte de ce qu'il a produit."
        )
    return int(morceaux[0]), int(morceaux[1]), float(morceaux[2])


def obtenir(
    stockage: StockageLocal,
    *,
    organization_id: str,
    revision_id: str,
    original: Path,
    page: int,
    zone: tuple[float, float, float, float],
) -> Tuile:
    """Rend une tuile, depuis le cache si elle y est.

    Les métadonnées — largeur, hauteur, hauteur du texte — sont redéduites du
    PNG quand il vient du cache, plutôt que stockées à côté. Un second fichier
    de métadonnées doublerait le nombre d'écritures et pourrait se désynchroniser
    de l'image ; les trois nombres se relisent du PNG lui-même.
    """
    cle = cle_de_la_tuile(organization_id, revision_id, page, zone)
    taille = stockage.taille(cle)
    if taille is not None:
        octets = b"".join(stockage.lire(cle))
        largeur, hauteur = _dimensions_du_png(octets)
        return Tuile(
            png=octets,
            largeur=largeur,
            hauteur=hauteur,
            # Inconnue depuis le cache : elle dépend de la hauteur du texte
            # d'origine, qui n'est pas dans le PNG. L'écran la reçoit à la
            # PREMIÈRE demande, qui est celle où il décide quoi afficher.
            hauteur_du_texte_px=0.0,
            depuis_le_cache=True,
        )

    import tempfile

    with tempfile.TemporaryDirectory(prefix="metreo-tuile-") as brouillon:
        provisoire = Path(brouillon) / "tuile.png"
        largeur, hauteur, hauteur_du_texte = _rendre_hors_processus(
            original, page=page, zone=zone, sortie=provisoire
        )
        octets = provisoire.read_bytes()

    nombre, octets_deja_la = _occupation_des_tuiles(stockage, organization_id, revision_id)
    plein = (
        nombre >= PLAFOND_DE_TUILES_PAR_REVISION
        or octets_deja_la + len(octets) > PLAFOND_D_OCTETS_PAR_REVISION
    )
    if not plein:
        stockage.ecrire_octets(
            organization_id=organization_id,
            dossier=DOSSIER_TUILES,
            identifiant=cle.rsplit("/", 1)[-1].removesuffix(".png"),
            extension=".png",
            contenu=octets,
            media_type="image/png",
        )
    else:
        # Journalisé, et sans rien du contenu : la mesure reste servie, mais
        # un volume qui se remplit doit être visible avant d'être plein.
        logger.info(
            "cache_de_tuiles_plein",
            extra={
                "organization_id": organization_id,
                "revision_id": revision_id,
                "tuiles": nombre,
                "octets": octets_deja_la,
                "plafond_de_tuiles": PLAFOND_DE_TUILES_PAR_REVISION,
                "plafond_d_octets": PLAFOND_D_OCTETS_PAR_REVISION,
            },
        )

    return Tuile(
        png=octets,
        largeur=largeur,
        hauteur=hauteur,
        hauteur_du_texte_px=hauteur_du_texte,
        depuis_le_cache=False,
    )


def _occupation_des_tuiles(
    stockage: StockageLocal, organization_id: str, revision_id: str
) -> tuple[int, int]:
    """Combien de tuiles cette révision a posées, et combien d'octets.

    Les deux dans le MÊME parcours : la liste des fichiers est la partie
    coûteuse, et `stat()` sur chacun ne l'est pas — le système vient de les
    énumérer, leurs métadonnées sont déjà en cache.
    """
    dossier = stockage.chemin(f"{DOSSIER_TUILES}/{organization_id}")
    if not dossier.is_dir():
        return 0, 0
    nombre = 0
    octets = 0
    for chemin in dossier.glob(f"{revision_id}-p*.png"):
        nombre += 1
        octets += chemin.stat().st_size
    return nombre, octets


def cles_des_tuiles(stockage: StockageLocal, organization_id: str, revision_id: str) -> list[str]:
    """Toutes les tuiles d'une révision, pour la purge.

    Les dérivés n'ont aucune ligne en base : leur clé se calcule depuis la
    révision, et le jour où elle disparaît, plus rien ne sait quels fichiers
    lui appartenaient. Cette liste est donc ce que `conservation.py` inscrit.
    """
    dossier = stockage.chemin(f"{DOSSIER_TUILES}/{organization_id}")
    if not dossier.is_dir():
        return []
    return [
        f"{DOSSIER_TUILES}/{organization_id}/{chemin.name}"
        for chemin in sorted(dossier.glob(f"{revision_id}-p*.png"))
    ]


def _dimensions_du_png(octets: bytes) -> tuple[int, int]:
    """La largeur et la hauteur, lues dans l'en-tête IHDR.

    Seize octets de lecture, sans ouvrir Pillow : les dimensions d'un PNG sont
    à une position fixe, et charger une bibliothèque d'images pour les obtenir
    coûterait plus que le reste de la fonction.
    """
    if len(octets) < 24 or octets[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    largeur = int.from_bytes(octets[16:20], "big")
    hauteur = int.from_bytes(octets[20:24], "big")
    return largeur, hauteur
