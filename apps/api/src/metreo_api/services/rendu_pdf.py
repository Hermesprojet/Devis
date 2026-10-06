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
#: ici c'est la quantité d'information réellement produite.
#:
#: **Ce commentaire affirmait « 0,42 mm par pixel » et « un texte de cote
#: reste lisible ». Les deux étaient faux.** Mesuré sur les quatre plans
#: réels : 0,594 à 0,845 mm par pixel, et une hauteur médiane de texte de 3,5
#: à 4,4 pixels. Un texte de quatre pixels de haut ne se lit pas.
#:
#: La conséquence n'est pas cosmétique : l'aperçu SITUE un fragment, il ne
#: permet pas de le RELIRE. Un propriétaire qui doit confirmer une cote a donc
#: besoin d'autre chose, et c'est `rendre_une_zone` — sans quoi « confirmer ou
#: corriger » demanderait d'ouvrir le PDF hors de Metreo.
#:
#: Au-delà de 2 000 pixels, le coût est quadratique et le gain faible : un
#: texte de 4 pixels passerait à 8 pour quatre fois le poids, et resterait
#: illisible. Agrandir la page entière n'est pas la bonne réponse ; agrandir
#: la zone qu'on regarde l'est.
COTE_AFFICHEE = 2000

#: Au-delà, le PNG produit est jeté plutôt que posé.
#:
#: **Ce plafond valait 8 Mio, et il était trop bas.** Mesuré : un plan
#: vectoriel rendu à 2 000 pixels de grand côté pèse 0,9 Mo, mais une image
#: 2 000 × 2 000 de contenu incompressible rend **11,46 Mio** de PNG. Une
#: page scannée ou photographique dépasse donc les 8 Mio, et son aperçu
#: aurait été jeté — précisément le cas où l'aperçu est la seule chose
#: utilisable, puisqu'il n'y a pas de texte à extraire.
#:
#: Seize mébioctets passent au-dessus de ce pire cas. Le plafond ne peut donc
#: plus être atteint par un document légitime : s'il se déclenche, c'est un
#: défaut, et c'est pour cela qu'il reste.
#:
#: Cette borne arrive APRÈS l'encodage : elle ne protège pas la mémoire, elle
#: protège le service et le navigateur. La mémoire, elle, est bornée en amont
#: par le facteur de rendu, qui plafonne la surface produite.
PLAFOND_OCTETS_PNG = 16 * 1024 * 1024

#: Le plus grand côté d'une TUILE de détail, en pixels.
#:
#: Une tuile montre UNE cote agrandie, pas la page. 512 pixels suffisent à
#: lire un texte et à voir ce qu'il cote autour, et tiennent dans un encart
#: d'écran sans faire défiler.
COTE_TUILE = 512

#: Le facteur d'agrandissement maximal d'une tuile.
#:
#: Quarante fois : une zone de 13 points — la plus petite cote observée sur les
#: plans réels — devient alors 512 pixels, soit la tuile entière. Au-delà, on
#: n'agrandit plus un texte, on agrandit des pixels.
FACTEUR_MAXIMAL = 40.0

#: La hauteur visée, en pixels, pour le texte d'une tuile.
#:
#: **Pourquoi une tuile existe.** Mesuré sur les quatre plans réels : à 2 000
#: pixels de grand côté, un A0 donne 0,594 à 0,845 mm par pixel, et la hauteur
#: MÉDIANE d'un texte y est de 3,5 à 4,4 pixels. Un texte de quatre pixels de
#: haut ne se lit pas. L'aperçu SITUE une cote ; il ne permet pas de la
#: relire — et le propriétaire doit pouvoir la relire pour la confirmer.
#:
#: Seize pixels sont lisibles. C'est ce que vise la tuile, en adaptant son
#: facteur à la hauteur réelle du fragment.
HAUTEUR_DE_TEXTE_VISEE = 16

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
    #: Sur une TUILE, la hauteur que le texte visé atteint réellement, en
    #: pixels. `None` sur un aperçu de page entière, où il n'y a pas de texte
    #: visé. En deçà de `HAUTEUR_DE_TEXTE_VISEE`, l'écran doit dire que la
    #: cote reste difficile à relire au lieu d'afficher un flou sans un mot.
    hauteur_du_texte_px: float | None = None


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
    # `may_draw_forms=False` explicitement : `init_forms()` n'est jamais
    # appelé sur ce document, donc rien ne serait dessiné de toute façon. La
    # garde est écrite plutôt que déduite d'un défaut de la bibliothèque, qui
    # pourrait changer — un champ de formulaire rempli est une donnée, et elle
    # n'a pas à entrer dans un aperçu servi.
    image = objet.render(scale=facteur, may_draw_forms=False).to_pil()

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


#: La marge autour d'une zone, en multiples de la HAUTEUR du texte.
#:
#: **Pas en fraction de la zone elle-même**, et la différence se voit à
#: l'œil : une marge d'une demie de la zone autour d'un « 1040 » de 11 points
#: donne une tuile qui montre « - 1040 E » et pas un trait du dessin. Le
#: propriétaire y lit la cote sans pouvoir juger CE QU'ELLE cote, ce qui est
#: précisément la question qu'on lui pose.
#:
#: Huit fois la hauteur du texte de chaque côté fait apparaître les lignes
#: d'attache et l'ouvrage mesuré. La hauteur du texte est le bon étalon parce
#: qu'elle suit l'échelle du dessin : un plan tracé plus petit a des textes
#: plus petits ET des ouvrages plus petits.
MARGE_DE_TUILE = 8.0


def rendre_une_zone(
    chemin: str | Path,
    *,
    page: int,
    zone: tuple[float, float, float, float],
    cote: int = COTE_TUILE,
) -> Apercu:
    """Agrandit UNE zone d'une page, pour qu'un humain puisse la relire.

    `zone` est `(x0, y0, x1, y1)` dans [0,1], **dans le repère de l'écran** —
    exactement ce que `lecture_pdf.Fragment.cadre` rend, et exactement ce que
    l'aperçu affiche. Aucune conversion n'est demandée à l'appelant, et c'est
    volontaire : une seconde conversion serait un second endroit où se tromper
    de sens d'axe.

    **Pourquoi cette fonction existe.** Mesuré sur les quatre plans réels : à
    2 000 pixels de grand côté, la hauteur médiane d'un texte est de 3,5 à 4,4
    pixels. L'aperçu situe donc un fragment sans permettre de le lire. Or le
    propriétaire doit pouvoir *confirmer ou corriger* une cote, ce qui demande
    de la lire. Sans tuile, « valider une mesure » voudrait dire ouvrir le PDF
    hors de Metreo — et comparer de mémoire.

    **Le coût est dans le chargement de la page, pas dans le rendu.** Mesuré
    sur deux plans réels : ouvrir le document ne coûte rien (moins d'une
    milliseconde), charger la page coûte **512 ms à 3 795 ms**, et rendre la
    tuile 2 à 34 ms — la zone rognée épargne bien le travail, quel que soit le
    facteur.

    Cette répartition commande une décision qui n'est PAS prise dans ce
    module : une route qui appellerait cette fonction à chaque clic ferait
    attendre jusqu'à quatre secondes pour une image de 20 Ko. Les tuiles des
    mesures proposées ont donc leur place dans l'étape d'analyse, où la page
    est déjà chargée — et la fonction est écrite pour les deux usages, parce
    que le choix appartient à l'appelant et doit se faire sur ce chiffre.
    """
    import pypdfium2 as pdfium

    chemin = Path(chemin)
    x0, y0, x1, y1 = zone
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise RenduRefuse(
            "zone_invalide",
            f"La zone {zone} n'est pas une boîte dans [0,1] : il n'y a rien à "
            "agrandir. Une mesure dont la position est inconnue ne peut pas "
            "être montrée sur le plan.",
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
            "fichier_invalide", f"Le PDF n'a pas pu être ouvert : {erreur}."
        ) from erreur

    if page < 1 or page > nombre:
        raise RenduRefuse(
            "page_inconnue",
            f"Le document porte {nombre} page(s) : la page {page} n'existe pas.",
        )

    objet = document[page - 1]
    # `get_size()` rend la taille AFFICHÉE, rotation comprise — et le rognage
    # de PDFium s'applique « au niveau du bitmap, donc après rotation »
    # (documentation, vérifiée par exécution). Les deux sont donc dans le même
    # repère que `zone`, et aucune inversion n'est à faire ici.
    largeur_pt, hauteur_pt = (float(valeur) for valeur in objet.get_size())
    if largeur_pt <= 0 or hauteur_pt <= 0:
        raise RenduRefuse(
            "fichier_invalide",
            "La page ne déclare aucune dimension exploitable : il n'y a rien à rendre.",
        )

    # La zone, élargie de sa marge et ramenée dans la page. La marge est la
    # MÊME dans les deux directions — en fraction de la page, donc corrigée du
    # rapport de forme — sans quoi une cote large et plate recevrait un
    # bandeau horizontal et aucun contexte vertical.
    marge = (y1 - y0) * MARGE_DE_TUILE
    marge_x = marge * hauteur_pt / largeur_pt
    marge_y = marge
    zx0 = max(0.0, x0 - marge_x)
    zy0 = max(0.0, y0 - marge_y)
    zx1 = min(1.0, x1 + marge_x)
    zy1 = min(1.0, y1 + marge_y)

    # Le rognage est ce qu'on RETIRE de chaque bord, en points de page.
    rognage = (
        zx0 * largeur_pt,
        (1.0 - zy1) * hauteur_pt,
        (1.0 - zx1) * largeur_pt,
        zy0 * hauteur_pt,
    )
    largeur_zone = max((zx1 - zx0) * largeur_pt, 1e-6)
    hauteur_zone = max((zy1 - zy0) * hauteur_pt, 1e-6)

    # La zone REMPLIT la tuile. Viser seulement une hauteur de texte donnerait
    # une image de la taille d'un timbre : mesuré, un texte de 11 points sur
    # un A0 atteint 16 pixels au facteur 1,4, et la tuile fait alors 61 × 31
    # pixels — lisible à la loupe, inutilisable dans un écran.
    #
    # Le facteur est borné : une zone de quelques points demanderait sinon un
    # facteur de plusieurs centaines, et PDFium allouerait un bitmap énorme
    # pour quatre caractères.
    facteur = min(cote / max(largeur_zone, hauteur_zone), FACTEUR_MAXIMAL)
    facteur = max(facteur, 1e-3)

    # Et la hauteur que le texte atteint RÉELLEMENT, qui est rendue à
    # l'appelant au lieu d'être supposée : sur une zone large — une cote dans
    # un grand cartouche — elle peut rester sous le seuil de lisibilité, et
    # l'écran doit pouvoir le dire plutôt que d'afficher un flou.
    hauteur_du_texte_px = (y1 - y0) * hauteur_pt * facteur

    image = objet.render(scale=facteur, crop=rognage, may_draw_forms=False).to_pil()

    tampon = BytesIO()
    image.save(tampon, format="PNG", optimize=True)
    octets = tampon.getvalue()
    if len(octets) > PLAFOND_OCTETS_PNG:
        raise RenduRefuse(
            "rendu_non_servable",
            f"La tuile produite pèse {len(octets) // (1024 * 1024)} Mio, au-delà "
            f"des {PLAFOND_OCTETS_PNG // (1024 * 1024)} Mio attendus. Elle est "
            "jetée plutôt que servie.",
        )

    return Apercu(
        png=octets,
        page=page,
        largeur=image.width,
        hauteur=image.height,
        pixels_par_point=facteur,
        hauteur_du_texte_px=hauteur_du_texte_px,
    )
