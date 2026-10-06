"""Rendre une page de PDF en une image affichable, sans logiciel externe.

Le pendant de `rendu_de_plan.py` pour le PDF. Les deux modules ne sont pas
symétriques, et la différence est imposée par les formats :

- un DXF est **vectoriel et interprétable** : ezdxf en redessine la géométrie,
  et le résultat est un SVG qui reste net à tous les grossissements ;
- un PDF est **déjà une page composée**. PDFium ne rend pas de géométrie
  réinterprétable : il rastérise. La sortie est donc un PNG, et le choix de la
  résolution est définitif au moment du rendu.

**Ce que coûte cette asymétrie, mesuré le 6 octobre 2026 sur quatre plans
réels.** Un A0 de 3 370 × 2 591 points rendu à l'échelle 2 donne
6 741 × 5 182 pixels en 3,46 s. C'est déjà hors d'une requête HTTP, et la
surface croît avec le carré du facteur : doubler encore coûterait quatorze
secondes et 140 Mo. La résolution est donc bornée par la **taille affichée**
et non par un facteur fixe — même règle que pour le SVG, pour la même raison :
ce qu'on sert doit être affichable, pas maximal.

**Ce module ne mesure rien et n'écrit rien en base.** Il rend des octets PNG
et les dimensions correspondantes. Le repère du PNG est celui de l'écran —
origine en haut à gauche — et c'est exactement celui dans lequel
`lecture_pdf` rend ses cadres normalisés : une boîte dans [0,1] se pose donc
directement sur l'image, sans rien recalculer. Un test le vérifie, parce que
rien dans le code ne l'impose.

**Le mot « échelle » n'apparaît pas dans ce module, et c'est voulu.** Dans ce
produit, une échelle est le rapport entre le dessin et l'ouvrage — 1:50 — et
elle ne s'obtient que par confirmation humaine. Ce qui se décide ici est un
nombre de pixels par point PostScript, qui ne dit rien de l'ouvrage. Les deux
notions portent donc des noms différents.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from .rendu_de_plan import DOSSIER_RENDUS

#: Le plus grand côté de l'aperçu, en pixels.
#:
#: Même valeur que `rendu_de_plan.COTE_AFFICHEE`, et pour une raison
#: différente : là-bas c'est la taille que le navigateur réserve avant zoom,
#: ici c'est la quantité d'information réellement produite. Deux mille pixels
#: sur le grand côté d'un A0 donnent 0,42 mm par pixel — un trait de plan
#: reste visible, un texte de cote reste lisible.
#:
#: Au-delà, le coût est quadratique et le gain nul sur un écran : ce qui
#: manquerait est le ZOOM, et un zoom utile demande un rendu par niveau, pas
#: un rendu géant. Cette décision est à reprendre le jour où le zoom sera
#: livré, et pas avant.
COTE_AFFICHEE = 2000

#: Au-delà, le PNG produit est jeté plutôt que posé.
#:
#: Mesuré : un A0 rendu à 2 000 pixels de grand côté pèse 0,9 Mo. Huit mébi
#: octets laissent donc huit fois la marge, et au-delà on servirait une image
#: qu'aucun navigateur n'affiche sans peine.
#:
#: Cette borne arrive APRÈS l'encodage : elle ne protège pas la mémoire, elle
#: protège le service et le navigateur. La mémoire, elle, est bornée en amont
#: par le facteur de rendu, qui plafonne la surface produite.
PLAFOND_OCTETS_PNG = 8 * 1024 * 1024

__all__ = [
    "COTE_AFFICHEE",
    "DOSSIER_RENDUS",
    "PLAFOND_OCTETS_PNG",
    "Apercu",
    "RenduRefuse",
    "cle_de_l_apercu",
    "rendre",
]


class RenduRefuse(Exception):
    """L'aperçu n'a pas eu lieu, et on dit pourquoi en un code stable.

    Les codes reprennent ceux de `rendu_de_plan.RenduRefuse` quand ils
    désignent la même chose : l'appelant traduit déjà `fichier_illisible`,
    `fichier_invalide` et `rendu_non_servable`, et deux vocabulaires pour un
    seul écran se seraient contredits.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - lisibilité d'un journal
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class Apercu:
    """Un PNG prêt à servir, et de quoi poser un surlignage dessus.

    `largeur` et `hauteur` sont en **pixels** : ce sont celles du PNG. Celles
    de la page en points PostScript ne figurent pas ici — elles vivent dans le
    constat de lecture, le seul endroit où elles ont un sens, et les redire ici
    les ferait diverger.

    Le rapport de forme est celui de la page. C'est ce qui permet à un cadre
    normalisé de `lecture_pdf` de tomber au bon endroit : `x × largeur`,
    `y × hauteur`, sans correction.
    """

    png: bytes
    #: 1-indexée, comme une citation documentaire.
    page: int
    largeur: int
    hauteur: int
    #: Combien de pixels valent un point PostScript. Pour le journal, et pour
    #: expliquer un aperçu flou — **jamais pour un calcul de métré** : ce
    #: nombre ne dit rien de l'ouvrage.
    pixels_par_point: float


def facteur_de_rendu(largeur: float, hauteur: float) -> float:
    """Le facteur qui amène le plus grand côté à `COTE_AFFICHEE`, sans agrandir.

    Plafonné à 1 : agrandir un A4 de 595 points à 2 000 pixels ne crée aucune
    information et triple le poids du fichier. Une page plus petite que la
    cote affichée est donc rendue à sa taille naturelle.

    Exposé plutôt que privé parce qu'un test en vérifie les deux branches :
    c'est la seule décision de ce module qui change ce que l'utilisateur voit.
    """
    grand = max(largeur, hauteur)
    if grand <= 0:
        return 1.0
    return min(1.0, COTE_AFFICHEE / grand)


def rendre(chemin: str | Path, *, page: int = 1) -> Apercu:
    """Rend une page en PNG. `page` est 1-indexée, comme une citation.

    Lève `RenduRefuse` — jamais une exception de PDFium — pour que l'appelant
    n'ait qu'un seul chemin d'erreur à écrire, exactement comme pour le DXF.
    """
    import pypdfium2 as pdfium

    chemin = Path(chemin)
    if page < 1:
        raise RenduRefuse(
            "page_inconnue",
            f"La page {page} n'existe pas : les pages se comptent depuis 1.",
        )

    try:
        document = pdfium.PdfDocument(str(chemin))
        nombre = len(document)
    except OSError as erreur:
        raise RenduRefuse(
            "fichier_illisible",
            f"Le fichier n'a pas pu être ouvert : {type(erreur).__name__}.",
        ) from erreur
    except pdfium.PdfiumError as erreur:
        raise RenduRefuse(
            "fichier_invalide",
            f"Le PDF n'a pas pu être ouvert : {erreur}.",
        ) from erreur

    if page > nombre:
        raise RenduRefuse(
            "page_inconnue",
            f"Le document porte {nombre} page(s) : la page {page} n'existe pas.",
        )

    objet = document[page - 1]
    largeur_pt, hauteur_pt = objet.get_size()
    if largeur_pt <= 0 or hauteur_pt <= 0:
        raise RenduRefuse(
            "fichier_invalide",
            "La page ne déclare aucune dimension exploitable : il n'y a rien à rendre.",
        )

    facteur = facteur_de_rendu(largeur_pt, hauteur_pt)
    image = objet.render(scale=facteur).to_pil()

    # Un tampon mémoire plutôt qu'un fichier : l'appelant pose les octets
    # lui-même par le stockage, et un fichier temporaire ici serait un fichier
    # de plus à nettoyer dans tous les chemins d'erreur.
    tampon = BytesIO()
    # `optimize` coûte un passage de plus et rend 10 à 15 % de moins ; sur une
    # image servie à chaque ouverture d'écran, ce passage se paie une fois.
    image.save(tampon, format="PNG", optimize=True)
    octets = tampon.getvalue()

    if len(octets) > PLAFOND_OCTETS_PNG:
        raise RenduRefuse(
            "rendu_non_servable",
            f"L'aperçu produit pèse {len(octets) // (1024 * 1024)} Mio, au-delà "
            f"des {PLAFOND_OCTETS_PNG // (1024 * 1024)} Mio qu'un écran affiche "
            "utilement. Il est jeté plutôt que servi.",
        )

    return Apercu(
        png=octets,
        page=page,
        largeur=image.width,
        hauteur=image.height,
        pixels_par_point=facteur,
    )


def cle_de_l_apercu(organization_id: str, revision_id: str, page: int = 1) -> str:
    """La clé de l'artefact d'aperçu. Déterministe, et elle porte la page.

    Le numéro de page fait partie de la clé parce qu'un PDF en a plusieurs et
    que chacune est un artefact distinct. Le DXF n'en a pas besoin : son rendu
    est unique par révision, et sa clé le reflète. Deux conventions pour deux
    formats, écrites plutôt que devinées.
    """
    return f"{DOSSIER_RENDUS}/{organization_id}/{revision_id}-p{page}.png"
