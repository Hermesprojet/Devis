"""Lire un PDF : ce qu'il porte, où, et ce qu'on refuse de deviner.

Ce module est au PDF ce que `lecture_dxf.py` est au DXF, et il en suit les
conventions à la lettre : un **constat** plutôt qu'une exception, des refus
**nommés**, des anomalies qui dégradent sans bloquer, et aucune conversion
d'unité — ce qui est lu est rendu tel quel.

Ce qu'il fait et ne fait pas, et la frontière n'est pas négociable :

- il rend le **texte positionné** d'un PDF, fragment par fragment, chacun avec
  sa page et sa boîte normalisée. Un fragment est ce que le dessinateur a
  écrit : Metreo le récolte, il ne l'invente pas ;
- il ne produit **aucune mesure**. Un PDF n'a pas d'unité de dessin : ses
  coordonnées sont des **points PostScript** (1/72 de pouce), qui ne disent
  rien de l'ouvrage. Passer d'un point à un millimètre demande une échelle, et
  une échelle demande une confirmation humaine. Rien ici n'y touche ;
- il ne lit **pas** les chemins vectoriels. L'ADR 0007 les confie à pdfplumber,
  qui n'est pas encore déclaré : une dépendance qu'aucun code n'appelle est une
  surface d'attaque gratuite.

Le repère change, et c'est le piège principal : le PDF a son origine **en bas
à gauche**, l'écran **en haut à gauche**. L'inversion est faite ici, une fois,
dans `_normaliser`, et un test la fixe sur une fixture dont on connaît la
position attendue.

Mesuré le 6 octobre 2026 sur quatre plans réels (A0, une page chacun) :
l'ouverture prend 1 ms, l'extraction de 815 à 4 351 fragments prend de 0,04 s
à 0,98 s. C'est ce qui permet de lire dans la requête plutôt que dans un
worker — la même frontière que pour le DXF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .lecture_dxf import Anomalie, Cadre

#: Au-delà, on refuse : un plan d'exécution tient en quelques pages, et un
#: document de cent pages n'est pas un plan mais un dossier — il relève du
#: pipeline documentaire, pas de la lecture de plans.
PLAFOND_PAGES = 50

#: Au-delà, on s'arrête en le disant. Mesuré : 4 351 fragments sur le plan
#: d'étage le plus dense des quatre. Le plafond laisse dix fois la marge et
#: borne la mémoire d'un fichier hostile.
PLAFOND_FRAGMENTS = 50_000

#: En dessous, le document ne porte pas de texte exploitable : il est
#: probablement scanné. Ce n'est PAS un refus — l'aperçu reste utile, et
#: l'OCR est une étape ultérieure. C'est une anomalie qui dégrade la confiance.
SEUIL_TEXTE_MAIGRE = 50

#: Le plus grand côté de l'aperçu, en pixels. Même valeur que le rendu DXF :
#: un plan A0 rendu à l'échelle 1 fait 3 370 × 2 591 points, et à l'échelle 2
#: il demande 3,5 s et 35 Mo — mesuré. On borne donc par la taille affichée,
#: pas par un facteur d'échelle.
COTE_AFFICHEE = 2000


@dataclass(frozen=True)
class Fragment:
    """Un morceau de texte, et où il se trouve.

    `texte` est rendu tel que PDFium le lit, sans normalisation : espaces,
    virgules décimales et unités comprises. Le découpage en fragments est
    celui du fichier, pas le nôtre — sur un plan réel, une cote peut arriver
    entière (« 101 ») ou éclatée caractère par caractère. Regrouper relève
    d'une interprétation, et cette interprétation ne se fait pas ici.
    """

    texte: str
    #: 1-indexée, comme une citation documentaire.
    page: int
    cadre: Cadre


@dataclass
class LecturePdf:
    """Le constat complet d'une lecture de PDF, refus compris."""

    refuse: bool = False
    motif_du_refus: Anomalie | None = None

    pages: int = 0
    #: Largeur et hauteur de chaque page, en **points PostScript**. Conservées
    #: brutes : c'est l'unité du document, et aucune conversion n'a lieu ici.
    dimensions: list[tuple[float, float]] = field(default_factory=list)
    fragments: list[Fragment] = field(default_factory=list)
    anomalies: list[Anomalie] = field(default_factory=list)

    @property
    def porte_du_texte(self) -> bool:
        """Faux pour un document scanné : l'aperçu marche, l'extraction non."""
        return sum(len(f.texte) for f in self.fragments) >= SEUIL_TEXTE_MAIGRE


def _normaliser(
    rectangle: tuple[float, float, float, float], largeur: float, hauteur: float
) -> Cadre | None:
    """Du repère PDF (origine en bas à gauche) à celui de l'écran.

    `rectangle` arrive en `(gauche, bas, droite, haut)`, convention PDFium.
    Le résultat suit celle des citations : `(x0, y0, x1, y1)` dans [0,1],
    **origine en haut à gauche**, donc `y` inversé.

    Rend `None` plutôt qu'une boîte fausse quand la page n'a pas de dimension
    exploitable : `None` veut dire « on ne sait pas », et l'écran sait
    l'afficher ; une boîte inventée désignerait un pixel au hasard.
    """
    if largeur <= 0 or hauteur <= 0:
        return None
    gauche, bas, droite, haut = rectangle
    x0 = max(0.0, min(1.0, gauche / largeur))
    x1 = max(0.0, min(1.0, droite / largeur))
    y0 = max(0.0, min(1.0, 1.0 - haut / hauteur))
    y1 = max(0.0, min(1.0, 1.0 - bas / hauteur))
    return Cadre(x0=x0, y0=y0, x1=x1, y1=y1)


def lire(chemin: str | Path) -> LecturePdf:
    """Lit un PDF et rend son constat. Ne lève pas sur un fichier abîmé.

    L'ordre des opérations porte la sécurité :

    1. la signature, avant tout le reste — un fichier qui n'est pas un PDF est
       refusé pour cette raison-là, pas pour une autre ;
    2. l'ouverture, qui échoue en nommant le chiffrement ou la corruption ;
    3. le nombre de pages, borné avant d'en parcourir une seule ;
    4. seulement ensuite, le texte.
    """
    # Import local : la dépendance `pdf` reste optionnelle, comme `plans`.
    import pypdfium2 as pdfium

    constat = LecturePdf()
    chemin = Path(chemin)

    try:
        with chemin.open("rb") as fichier:
            tete = fichier.read(8)
    except OSError as erreur:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "fichier_illisible",
            f"Le fichier n'a pas pu être ouvert : {type(erreur).__name__}.",
        )
        return constat

    if not tete.startswith(b"%PDF-"):
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "pas_un_pdf",
            "Le fichier ne porte pas la signature d'un PDF. Il est refusé pour "
            "cette raison, et non pour une corruption qu'il n'a peut-être pas.",
        )
        return constat

    try:
        document = pdfium.PdfDocument(str(chemin))
        nombre = len(document)
    except pdfium.PdfiumError as erreur:
        # PDFium distingue le mot de passe du reste ; son message le nomme.
        chiffre = "password" in str(erreur).lower()
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "pdf_chiffre" if chiffre else "pdf_invalide",
            "Le PDF est protégé par un mot de passe : Metreo ne le demande pas et ne le stocke pas."
            if chiffre
            else f"Le PDF n'a pas pu être ouvert : {erreur}.",
        )
        return constat
    except Exception as erreur:  # pragma: no cover - PDFium reste une bibliothèque C
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "pdf_invalide",
            f"Le PDF n'a pas pu être ouvert : {type(erreur).__name__}.",
        )
        return constat

    if nombre > PLAFOND_PAGES:
        constat.refuse = True
        constat.motif_du_refus = Anomalie(
            "pdf_trop_de_pages",
            f"Le document porte {nombre} pages, au-delà des {PLAFOND_PAGES} "
            "que la lecture de plans accepte. Un dossier de cette taille "
            "relève du pipeline documentaire, pas d'un plan.",
        )
        return constat

    constat.pages = nombre
    trop_de_fragments = False

    for numero in range(nombre):
        page = document[numero]
        largeur, hauteur = page.get_size()
        constat.dimensions.append((float(largeur), float(hauteur)))

        texte_de_page = page.get_textpage()
        rectangles = texte_de_page.count_rects()
        for index in range(rectangles):
            if len(constat.fragments) >= PLAFOND_FRAGMENTS:
                trop_de_fragments = True
                break
            gauche, bas, droite, haut = texte_de_page.get_rect(index)
            texte = texte_de_page.get_text_bounded(
                left=gauche, bottom=bas, right=droite, top=haut
            ).strip()
            if not texte:
                continue
            cadre = _normaliser((gauche, bas, droite, haut), largeur, hauteur)
            if cadre is None:
                continue
            constat.fragments.append(Fragment(texte=texte, page=numero + 1, cadre=cadre))
        if trop_de_fragments:
            break

    if trop_de_fragments:
        constat.anomalies.append(
            Anomalie(
                "trop_de_fragments",
                f"La lecture s'est arrêtée à {PLAFOND_FRAGMENTS} fragments de "
                "texte. Le document en porte davantage : ce qui suit n'a pas "
                "été lu, et aucune extraction ne doit être présentée comme "
                "complète.",
            )
        )

    if not constat.porte_du_texte:
        constat.anomalies.append(
            Anomalie(
                "texte_absent",
                "Le document ne porte pas de texte exploitable : il est "
                "probablement scanné. L'aperçu reste utilisable ; la lecture "
                "des cotes demanderait une reconnaissance optique, qui n'est "
                "pas livrée.",
            )
        )

    return constat
