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

- et **un nombre n'est pas une cote**. Constaté sur un plan réel : 860 des
  4 351 fragments de `etage-1.pdf` sont des nombres de trois chiffres ou plus.
  Le premier d'entre eux est « 1040 » — le code postal d'Etterbeek, dans le
  cartouche : « rue des casernes - 1040 Etterbeek ». Un lecteur qui
  proposerait les nombres comme des cotes proposerait donc des codes postaux,
  des numéros de plan, des années et des numéros de téléphone. Reconnaître une
  cote demande de regarder ce qu'elle cote, c'est-à-dire de regarder le
  dessin ; c'est à cela que sert `rendu_pdf.rendre_une_zone`, et c'est en
  regardant une de ses tuiles que ce défaut a été vu ;
- il ne lit **pas** les chemins vectoriels. L'ADR 0007 les confie à pdfplumber,
  qui n'est pas encore déclaré : une dépendance qu'aucun code n'appelle est une
  surface d'attaque gratuite.

Le repère change, et c'est le piège principal : le PDF a son origine **en bas
à gauche**, l'écran **en haut à gauche**. L'inversion est faite ici, une fois,
dans `_normaliser`, et un test la fixe sur une fixture dont on connaît la
position attendue.

Mesuré le 6 octobre 2026 sur quatre plans réels (grand format, une page
chacun : 1 480 × 850, 1 690 × 850 et deux fois 1 189 × 914 mm) :
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

#: Le code d'erreur de PDFium pour « mot de passe incorrect ».
#: Vérifié à l'exécution sur un PDF réellement chiffré : `err_code` vaut 4.
CODE_MOT_DE_PASSE = 4

#: En deçà, un dépassement de bord n'est pas un dépassement.
#:
#: Les coordonnées d'un PDF sont des flottants, et un texte collé au bord de
#: la page ressort à 1,0000000000002. Sans cette tolérance, chaque cartouche
#: serait annoncé « recadré » et l'avertissement perdrait tout son sens.
TOLERANCE_DE_BORD = 1e-9

#: Le plus grand côté de l'aperçu, en pixels. Même valeur que le rendu DXF :
#: un plan de 1 189 × 914 mm rendu à l'échelle 1 fait 3 370 × 2 591 points, et à l'échelle 2
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
    #: `None` quand la position n'a pas pu être établie — voir `position`.
    cadre: Cadre | None
    #: `"exacte"`, `"recadree"` ou `"inconnue"`. Un écran qui n'afficherait
    #: que le cadre présenterait un surlignage partiel comme s'il était
    #: complet ; ce champ est ce qui lui permet de le dire.
    position: str = "exacte"


@dataclass
class LecturePdf:
    """Le constat complet d'une lecture de PDF, refus compris."""

    refuse: bool = False
    motif_du_refus: Anomalie | None = None

    pages: int = 0
    #: Largeur et hauteur de chaque page, en **points PostScript**. Conservées
    #: brutes : c'est l'unité du document, et aucune conversion n'a lieu ici.
    dimensions: list[tuple[float, float]] = field(default_factory=list)
    #: La BOÎTE de chaque page — `(gauche, bas, droite, haut)` — et non ses
    #: seules dimensions. Les deux diffèrent dès que la page ne commence pas à
    #: (0,0), et c'est la boîte qu'il faut pour situer ou mesurer quoi que ce
    #: soit : les coordonnées d'un texte, comme celles d'un point désigné à la
    #: souris, sont absolues.
    boites: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: La rotation d'affichage de chaque page, en degrés (0, 90, 180, 270).
    #: Nécessaire à qui veut convertir un point de l'écran vers la page —
    #: l'opération inverse de celle que fait `normaliser`.
    rotations: list[int] = field(default_factory=list)
    fragments: list[Fragment] = field(default_factory=list)
    anomalies: list[Anomalie] = field(default_factory=list)

    @property
    def porte_du_texte(self) -> bool:
        """Faux pour un document scanné : l'aperçu marche, l'extraction non."""
        return sum(len(f.texte) for f in self.fragments) >= SEUIL_TEXTE_MAIGRE


#: Comment un point de la page tombe à l'écran, selon `/Rotate`.
#:
#: `a` et `b` sont les coordonnées du point **relatives à la boîte affichée**,
#: ramenées dans [0,1] : `a` le long de la largeur de la page, `b` le long de
#: sa hauteur, `b` croissant vers le HAUT puisque c'est le repère du PDF.
#: Le résultat est `(x, y)` à l'écran, `y` croissant vers le bas.
#:
#: **Établi par vérité terrain, pas par raisonnement** : pour chacune des
#: quatre rotations, la page a été rendue en PNG et la boîte d'encre réelle du
#: texte a été relevée au pixel. Les quatre formules ci-dessous reproduisent
#: ces quatre boîtes. Déduire la rotation d'une convention supposée a une
#: chance sur huit d'être juste, et se trompe sans rien casser.
_VERS_L_ECRAN: dict[int, object] = {
    0: lambda a, b: (a, 1.0 - b),
    90: lambda a, b: (b, a),
    180: lambda a, b: (1.0 - a, b),
    270: lambda a, b: (1.0 - b, 1.0 - a),
}


def normaliser(
    rectangle: tuple[float, float, float, float],
    boite_affichee: tuple[float, float, float, float],
    rotation: int,
) -> tuple[Cadre | None, str]:
    """Du repère de la page à celui de l'écran, et ce qu'on sait de la position.

    Rend `(cadre, position)` où `position` vaut :

    - `"exacte"` : la boîte est entièrement dans la page ;
    - `"recadree"` : elle en dépassait et a été rognée. La mesure reste
      utilisable, mais le surlignage ne couvre pas tout le texte ;
    - `"inconnue"` : elle est hors de la page, ou plate. Le cadre vaut alors
      `None`, et c'est **la seule réponse honnête** — une boîte inventée
      désignerait un pixel au hasard, et l'écran sait afficher « position
      inconnue ».

    **Trois défauts corrigés ici, chacun reproduit par construction.**

    1. *L'origine de la boîte était ignorée.* `page.get_size()` rend des
       DIMENSIONS, pas une origine ; les rectangles de texte, eux, arrivent en
       coordonnées absolues de la page. Sur une page de `MediaBox
       [100 50 300 150]`, un texte posé au même endroit relatif qu'une page
       commençant à (0,0) ressortait à `x0=0,60` au lieu de 0,10 — et avec
       `y0 = y1 = 0`, donc une boîte PLATE, que la contrainte
       `ck_source_citation_bbox` refuse.
    2. *`/Rotate` était ignoré.* `get_size()` rend la taille AFFICHÉE — (100,
       200) pour une page de 200 × 100 tournée de 90° — tandis que les
       rectangles de texte restent dans le repère non tourné. Diviser l'un par
       l'autre pose la boîte n'importe où.
    3. *Le rognage se faisait en silence*, par `max(0, min(1, …))`, et c'est
       lui qui rendait le premier défaut invisible : une boîte hors cadre
       devenait une boîte plate au bord, et rien ne le disait.
    """
    gauche, bas, droite, haut = boite_affichee
    largeur = droite - gauche
    hauteur = haut - bas
    if largeur <= 0 or hauteur <= 0:
        return None, "inconnue"

    transformer = _VERS_L_ECRAN.get(rotation % 360 if rotation else 0)
    if transformer is None:
        # Une rotation qui n'est pas un multiple de 90 n'existe pas dans le
        # format. Plutôt que d'en inventer une, on dit qu'on ne sait pas.
        return None, "inconnue"

    coins = [
        transformer((x - gauche) / largeur, (y - bas) / hauteur)  # type: ignore[operator]
        for x in (rectangle[0], rectangle[2])
        for y in (rectangle[1], rectangle[3])
    ]
    xs = [coin[0] for coin in coins]
    ys = [coin[1] for coin in coins]
    brut = (min(xs), min(ys), max(xs), max(ys))

    # L'intersection avec la page, et ce qu'elle apprend.
    x0, y0 = max(0.0, brut[0]), max(0.0, brut[1])
    x1, y1 = min(1.0, brut[2]), min(1.0, brut[3])
    if x1 <= x0 or y1 <= y0:
        return None, "inconnue"

    position = "exacte"
    if any(valeur < -TOLERANCE_DE_BORD or valeur > 1.0 + TOLERANCE_DE_BORD for valeur in brut):
        position = "recadree"
    return Cadre(x0=x0, y0=y0, x1=x1, y1=y1), position


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
        # Le CODE d'erreur de PDFium, pas son message : 4 est « mot de passe
        # incorrect ». Chercher « password » dans le texte marchait, et aurait
        # cessé de marcher au premier message traduit ou reformulé — sans
        # bruit, en reclassant un PDF protégé en « PDF invalide ».
        chiffre = getattr(erreur, "err_code", None) == CODE_MOT_DE_PASSE
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

    recadres = 0
    sans_position = 0
    pages_tournees: list[int] = []

    for numero in range(nombre):
        page = document[numero]
        # Les DIMENSIONS affichées, pour le rapport de forme de l'aperçu.
        largeur, hauteur = page.get_size()
        constat.dimensions.append((float(largeur), float(hauteur)))

        # Et la BOÎTE, avec son origine, pour situer un texte dedans. Les deux
        # ne sont pas la même chose : `get_size()` ne dit pas OÙ la page
        # commence, et les rectangles de texte arrivent en coordonnées
        # absolues. Une page de `MediaBox [100 50 300 150]` mesure bien
        # 200 × 100, et son coin bas gauche est à (100, 50).
        boite = tuple(float(valeur) for valeur in page.get_bbox())
        rotation = int(page.get_rotation() or 0)
        constat.boites.append(boite)  # type: ignore[arg-type]
        constat.rotations.append(rotation)
        if rotation:
            pages_tournees.append(numero + 1)

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
            cadre, position = normaliser(
                (gauche, bas, droite, haut),
                boite,  # type: ignore[arg-type]
                rotation,
            )
            if position == "recadree":
                recadres += 1
            elif position == "inconnue":
                sans_position += 1
            # Un fragment sans position est CONSERVÉ : son texte reste une
            # information — une échelle écrite dans un cartouche hors cadre
            # vaut toujours d'être lue. C'est son emplacement qui manque, et
            # `position` le dit au lieu de le taire.
            constat.fragments.append(
                Fragment(texte=texte, page=numero + 1, cadre=cadre, position=position)
            )
        if trop_de_fragments:
            break

    if pages_tournees:
        constat.anomalies.append(
            Anomalie(
                "pages_tournees",
                f"{len(pages_tournees)} page(s) portent une rotation d'affichage. "
                "Elle est appliquée aux positions, et l'aperçu la suit aussi. "
                "C'est signalé parce qu'un surlignage qui tomberait à côté "
                "viendrait de là.",
            )
        )
    if recadres:
        constat.anomalies.append(
            Anomalie(
                "fragments_recadres",
                f"{recadres} fragment(s) de texte dépassent du bord de la page "
                "et ont été rognés : leur surlignage ne couvrira pas tout le "
                "texte. La valeur lue, elle, est complète.",
            )
        )
    if sans_position:
        constat.anomalies.append(
            Anomalie(
                "fragments_sans_position",
                f"{sans_position} fragment(s) de texte sont hors de la page "
                "affichée : leur texte est conservé, leur emplacement est "
                "inconnu. Ils ne peuvent pas être montrés sur l'aperçu.",
            )
        )

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
