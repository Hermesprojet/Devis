"""Un vrai PDF, écrit ici, sans bibliothèque tierce ni chaîne binaire.

Pourquoi pas une dépendance. Ce dépôt s'interdit de faire dépendre un parcours
métier d'un service externe, et une bibliothèque de mise en page apporte, pour
un document de quelques pages, une surface de dépendances et un rendu qu'on ne
contrôle plus. Le format PDF est, pour ce besoin, remarquablement simple : des
objets numérotés, un flux de commandes de dessin, une table de références.
Tout ce qui suit tient dans la bibliothèque standard.

Pourquoi pas du HTML renommé. Un `.pdf` qui contient du HTML ne s'ouvre chez
personne. Ce module écrit un vrai `%PDF-1.4`, et les tests en extraient le
texte pour vérifier ce qu'il porte.

**Déterminisme.** Deux générations du même devis rendent exactement les mêmes
octets : aucune date « maintenant », aucun identifiant tiré au hasard, aucune
compression dont la sortie dépendrait de la version de zlib. La date de
création du document est celle de l'ÉMISSION, pas celle de la génération.

**Les polices.** Helvetica pour le texte, Courier pour les nombres. Ce n'est
pas un goût : les quatorze polices de base n'ont pas à être embarquées, et
Courier étant à chasse fixe, aligner une colonne de montants à droite se
calcule exactement — sans embarquer la table des largeurs d'Helvetica, mille
lignes de données pour un seul alignement.
"""

from __future__ import annotations

import unicodedata
import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Un point PostScript. La page A4 fait 595,28 x 841,89 points.
A4 = (595.28, 841.89)
MARGE = 48.0

HELVETICA = "F1"
HELVETICA_GRAS = "F2"
COURIER = "F3"
COURIER_GRAS = "F4"

#: Chasse d'une police Courier : 600 millièmes de la taille, pour tout
#: caractère. C'est ce qui rend l'alignement des montants exact.
CHASSE_COURIER = 0.6
#: Largeur MOYENNE d'un caractère Helvetica. Conservée pour les appelants qui
#: n'ont besoin que d'un ordre de grandeur ; la mise en page, elle, mesure.
CHASSE_HELVETICA_APPROX = 0.5

#: Les largeurs RÉELLES d'Helvetica, en millièmes de la taille.
#:
#: Pourquoi elles sont devenues nécessaires. Le repliement estimait chaque
#: caractère à 0,5 em. C'est juste en moyenne pour du texte courant, et faux
#: pour des capitales : un « W » vaut 0,944 em, un « M » 0,833. Mesuré — une
#: raison sociale en capitales, « SOCIETE ANONYME DES CARRIERES DU HAINAUT »,
#: repliée sur une colonne de 155 points en occupait 179 et venait s'imprimer
#: PAR-DESSUS le numéro du devis et sa date. Le défaut ne se voyait pas : le
#: contrôle de mise en page mesurait avec la même approximation que le code,
#: et confirmait donc ce que le code croyait plutôt que ce qu'il dessinait.
#:
#: Ces valeurs sont celles des métriques Adobe des polices de base. Aucune
#: dépendance : c'est une table, et les quatorze polices de base sont
#: exactement celles qu'un lecteur de PDF possède déjà.
_LARGEURS_HELVETICA: dict[str, int] = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    "0": 556,
    "1": 556,
    "2": 556,
    "3": 556,
    "4": 556,
    "5": 556,
    "6": 556,
    "7": 556,
    "8": 556,
    "9": 556,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "A": 667,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "H": 722,
    "I": 278,
    "J": 500,
    "K": 667,
    "L": 556,
    "M": 833,
    "N": 722,
    "O": 778,
    "P": 667,
    "Q": 778,
    "R": 722,
    "S": 667,
    "T": 611,
    "U": 722,
    "V": 667,
    "W": 944,
    "X": 667,
    "Y": 667,
    "Z": 611,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "a": 556,
    "b": 556,
    "c": 500,
    "d": 556,
    "e": 556,
    "f": 278,
    "g": 556,
    "h": 556,
    "i": 222,
    "j": 222,
    "k": 500,
    "l": 222,
    "m": 833,
    "n": 556,
    "o": 556,
    "p": 556,
    "q": 556,
    "r": 333,
    "s": 500,
    "t": 278,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
    "z": 500,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
}
_LARGEURS_HELVETICA_GRAS: dict[str, int] = {
    **_LARGEURS_HELVETICA,
    "!": 333,
    '"': 474,
    "&": 722,
    "'": 238,
    ":": 333,
    ";": 333,
    "?": 611,
    "@": 975,
    "A": 722,
    "J": 556,
    "K": 722,
    "L": 611,
    "[": 333,
    "]": 333,
    "^": 584,
    "b": 611,
    "c": 556,
    "d": 611,
    "f": 333,
    "g": 611,
    "h": 611,
    "i": 278,
    "j": 278,
    "k": 556,
    "l": 278,
    "m": 889,
    "n": 611,
    "o": 611,
    "p": 611,
    "q": 611,
    "r": 389,
    "t": 333,
    "u": 611,
    "v": 556,
    "w": 778,
    "x": 556,
    "y": 556,
    "{": 389,
    "|": 280,
    "}": 389,
}


def largeur_texte(texte: str, police: str, taille: float) -> float:
    """La largeur RÉELLE d'une chaîne, en points.

    Une lettre accentuée a, dans ces polices, exactement la largeur de sa
    lettre de base : « é » mesure comme « e ». On décompose donc plutôt que
    d'inventer une valeur — c'est exact, et cela couvre tout le latin sans
    allonger la table.

    Un caractère hors table retombe sur la largeur d'un « m », la plus large
    des minuscules : mieux vaut réserver trop de place que déborder.
    """
    if police in (COURIER, COURIER_GRAS):
        return len(texte) * taille * CHASSE_COURIER
    table = _LARGEURS_HELVETICA_GRAS if police == HELVETICA_GRAS else _LARGEURS_HELVETICA
    total = 0
    for caractere in texte:
        largeur = table.get(caractere)
        if largeur is None:
            base = unicodedata.normalize("NFD", caractere)[:1]
            largeur = table.get(base, table["m"])
        total += largeur
    return total * taille / 1000.0


def _texte_pdf(valeur: str) -> bytes:
    """Encode une chaîne littérale PDF en WinAnsi, parenthèses échappées.

    WinAnsiEncoding couvre le français — accents, cédille, œ. Un caractère hors
    de ce jeu devient un point d'interrogation plutôt que de casser le fichier :
    un devis illisible serait pire qu'un devis imparfait.
    """
    brut = valeur.encode("cp1252", errors="replace")
    sortie = bytearray(b"(")
    for octet in brut:
        if octet in (0x28, 0x29, 0x5C):
            sortie += b"\\" + bytes([octet])
        elif octet < 32 or octet > 126:
            sortie += f"\\{octet:03o}".encode("ascii")
        else:
            sortie.append(octet)
    sortie += b")"
    return bytes(sortie)


def _nombre(valeur: float) -> str:
    """Un nombre PDF, sans exposant et sans zéros inutiles."""
    rendu = f"{valeur:.3f}".rstrip("0").rstrip(".")
    return rendu or "0"


def tronquer(texte: str, largeur: float, taille: float, police: str = HELVETICA) -> str:
    """Coupe un libellé qui déborderait de sa colonne, en le disant.

    Les points de suspension sont pris DANS le budget, pas ajoutés après :
    `texte[:maximum - 1] + "..."` rendait `maximum + 2` caractères, et le
    libellé dépassait encore de deux caractères la colonne qu'on venait de
    lui mesurer. Mesuré sur le cadre de signature, où les deux caractères de
    trop mordaient sur le cadre voisin.

    La largeur est MESURÉE, caractère par caractère : compter les caractères
    revenait à supposer qu'ils font tous la même chasse, ce qui est faux dès
    qu'un libellé porte des capitales.
    """
    if largeur_texte(texte, police, taille) <= largeur:
        return texte
    points = largeur_texte("...", police, taille)
    garde = ""
    for caractere in texte:
        if largeur_texte(garde + caractere, police, taille) + points > largeur:
            break
        garde += caractere
    return f"{garde}..." if garde else texte[:1]


def replier(texte: str, largeur: float, taille: float, police: str = HELVETICA) -> list[str]:
    """Replie un texte sur la largeur disponible, sans couper les mots.

    Sauf un mot qui, à lui seul, dépasse la ligne : une raison sociale
    d'intercommunale, une référence de marché, une URL. Le laisser entier le
    ferait sortir de la page — le texte serait dans le fichier, mais pas sur la
    feuille. On le césure alors franchement : un mot coupé se lit, un mot hors
    du papier ne s'imprime pas.

    La largeur est MESURÉE avec les métriques réelles de la police, et non
    estimée à une chasse moyenne. L'approximation tenait pour du texte
    courant ; elle cédait sur des capitales, où un « W » vaut presque deux
    fois la moyenne supposée. Mesuré : une raison sociale en capitales
    débordait de sa colonne et venait s'imprimer par-dessus le cartouche du
    devis.
    """
    if not texte.strip():
        return []

    def tient(essai: str) -> bool:
        return largeur_texte(essai, police, taille) <= largeur

    lignes: list[str] = []
    courante = ""
    for mot_entier in texte.split():
        mot = mot_entier
        # Un mot plus large que la colonne se césure franchement : le laisser
        # entier le ferait sortir de la feuille.
        while not tient(mot):
            if courante:
                lignes.append(courante)
                courante = ""
            morceau = ""
            for caractere in mot:
                if not tient(morceau + caractere):
                    break
                morceau += caractere
            if not morceau:  # un seul caractère plus large que la colonne
                morceau = mot[:1]
            lignes.append(morceau)
            mot = mot[len(morceau) :]
        if courante and not tient(f"{courante} {mot}"):
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    if courante:
        lignes.append(courante)
    return lignes


@dataclass(frozen=True, slots=True)
class ImagePdf:
    """Une image prête à être posée : des échantillons, et de quoi les lire.

    Le PDF ne comprend pas le PNG. Il comprend un flux d'échantillons, un
    espace de couleur et un filtre — c'est ce que `services.images` produit, et
    ce que ce type transporte jusqu'à `assembler`.

    `nom` est la clé sous laquelle la page désignera l'image dans ses
    ressources (`/Im1`). Il vient du code, jamais d'un nom de fichier.
    """

    nom: str
    largeur: int
    hauteur: int
    espace: str
    couleur: bytes
    alpha: bytes | None = None


@dataclass
class Page:
    """Les commandes de dessin d'une page, dans l'ordre où elles s'appliquent."""

    commandes: list[bytes] = field(default_factory=list)

    def texte(
        self, x: float, y: float, contenu: str, *, police: str = HELVETICA, taille: float = 10.0
    ) -> None:
        if not contenu:
            return
        self.commandes.append(
            b"BT /"
            + police.encode("ascii")
            + f" {_nombre(taille)} Tf 1 0 0 1 {_nombre(x)} {_nombre(y)} Tm ".encode("ascii")
            + _texte_pdf(contenu)
            + b" Tj ET"
        )

    def paragraphe(
        self,
        x: float,
        y: float,
        contenu: str,
        *,
        largeur: float,
        police: str = HELVETICA,
        taille: float = 9.0,
        interligne: float | None = None,
    ) -> float:
        """Écrit un texte replié sur `largeur`, et rend l'ordonnée suivante.

        Toute ligne d'en-tête passe par ici plutôt que par `texte` : une raison
        sociale d'intercommunale fait cent vingt caractères, et posée d'un
        bloc elle sortait de la marge droite.
        """
        pas = interligne if interligne is not None else taille + 2
        for ligne in replier(contenu, largeur, taille, police):
            self.texte(x, y, ligne, police=police, taille=taille)
            y -= pas
        return y

    def texte_a_droite(
        self, droite: float, y: float, contenu: str, *, police: str = COURIER, taille: float = 9.0
    ) -> None:
        """Aligné sur son bord DROIT — exact, Courier étant à chasse fixe."""
        largeur = len(contenu) * taille * CHASSE_COURIER
        self.texte(droite - largeur, y, contenu, police=police, taille=taille)

    def ligne(self, x1: float, y1: float, x2: float, y2: float, *, epaisseur: float = 0.5) -> None:
        self.commandes.append(
            f"{_nombre(epaisseur)} w {_nombre(x1)} {_nombre(y1)} m "
            f"{_nombre(x2)} {_nombre(y2)} l S".encode("ascii")
        )

    def pave(self, x: float, y: float, largeur: float, hauteur: float, gris: float) -> None:
        self.commandes.append(
            f"{_nombre(gris)} g {_nombre(x)} {_nombre(y)} {_nombre(largeur)} "
            f"{_nombre(hauteur)} re f 0 g".encode("ascii")
        )

    def image(self, nom: str, x: float, y: float, largeur: float, hauteur: float) -> None:
        """Pose une image dans un rectangle, coin INFÉRIEUR gauche en (x, y).

        Un PDF dessine une image sur le carré unité et la laisse déformer par
        la matrice courante : `largeur 0 0 hauteur x y cm` la met à l'échelle
        et la place. Le `q`/`Q` enferme cette matrice — sans lui, tout ce qui
        serait dessiné ensuite hériterait de l'échelle et sortirait de la page.
        """
        self.commandes.append(
            f"q {_nombre(largeur)} 0 0 {_nombre(hauteur)} {_nombre(x)} {_nombre(y)} cm /".encode(
                "ascii"
            )
            + nom.encode("ascii")
            + b" Do Q"
        )

    def flux(self) -> bytes:
        return b"\n".join(self.commandes)


def _objets_image(image: ImagePdf, numero_suivant: int) -> tuple[list[bytes], int]:
    """Les objets PDF d'une image, et le numéro de son XObject.

    Le masque de transparence vient EN PREMIER : le XObject doit le désigner
    par son numéro, et un numéro ne se connaît qu'une fois l'objet ajouté.

    Les échantillons sont comprimés par `zlib` avec un niveau fixe. Le niveau
    est fixé plutôt que laissé par défaut parce que le déterminisme du document
    en dépend : deux générations du même devis doivent rendre les mêmes octets,
    et un niveau par défaut est une valeur qu'une version de Python pourrait
    changer sous nos pieds.
    """
    corps: list[bytes] = []
    numero = numero_suivant
    numero_masque: int | None = None

    if image.alpha is not None:
        masque = zlib.compress(image.alpha, 9)
        corps.append(
            b"<< /Type /XObject /Subtype /Image"
            + f" /Width {image.largeur} /Height {image.hauteur}".encode("ascii")
            + b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"
            + f" /Length {len(masque)} >>\nstream\n".encode("ascii")
            + masque
            + b"\nendstream"
        )
        numero_masque = numero
        numero += 1

    echantillons = zlib.compress(image.couleur, 9)
    entete = (
        b"<< /Type /XObject /Subtype /Image"
        + f" /Width {image.largeur} /Height {image.hauteur}".encode("ascii")
        + b" /ColorSpace /"
        + image.espace.encode("ascii")
        + b" /BitsPerComponent 8 /Filter /FlateDecode"
        + f" /Length {len(echantillons)}".encode("ascii")
    )
    if numero_masque is not None:
        entete += f" /SMask {numero_masque} 0 R".encode("ascii")
    corps.append(entete + b" >>\nstream\n" + echantillons + b"\nendstream")
    return corps, numero


def assembler(
    pages: Sequence[Page],
    *,
    titre: str,
    auteur: str,
    date_pdf: str,
    images: Sequence[ImagePdf] = (),
) -> bytes:
    """Écrit le fichier : objets, table de références croisées, fin.

    `date_pdf` est au format PDF (`D:AAAAMMJJHHmmSS`) et vient de la date
    d'émission. C'est ce qui rend deux générations identiques au bit près.

    Les `images` sont déclarées dans les ressources de TOUTES les pages, comme
    les polices. Une page qui n'en dessine aucune n'en porte pas moins la
    déclaration : c'est sans effet sur le rendu, et cela évite de fabriquer un
    dictionnaire de ressources par page pour une économie de quelques octets.
    """
    objets: list[bytes] = []

    def ajouter(corps: bytes) -> int:
        objets.append(corps)
        return len(objets)  # les numéros d'objet commencent à 1

    numeros_images: dict[str, int] = {}
    for image in images:
        corps_image, _ = _objets_image(image, len(objets) + 1)
        numero_xobject = 0
        for corps in corps_image:
            numero_xobject = ajouter(corps)
        numeros_images[image.nom] = numero_xobject

    numeros_polices: dict[str, int] = {}
    for nom, base in (
        (HELVETICA, b"Helvetica"),
        (HELVETICA_GRAS, b"Helvetica-Bold"),
        (COURIER, b"Courier"),
        (COURIER_GRAS, b"Courier-Bold"),
    ):
        numeros_polices[nom] = ajouter(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /" + base + b" /Encoding /WinAnsiEncoding >>"
        )

    ressources = (
        b"<< /Font << "
        + b" ".join(
            b"/" + nom.encode("ascii") + f" {numero} 0 R".encode("ascii")
            for nom, numero in numeros_polices.items()
        )
        + b" >>"
    )
    if numeros_images:
        ressources += (
            b" /XObject << "
            + b" ".join(
                b"/" + nom.encode("ascii") + f" {numero} 0 R".encode("ascii")
                for nom, numero in numeros_images.items()
            )
            + b" >>"
        )
    ressources += b" >>"

    # Le nœud /Pages n'existe pas encore quand les pages le désignent : on
    # écrit un jeton, et on le remplace une fois son numéro connu. Réordonner
    # les objets serait l'autre solution, et elle décalerait tout le reste.
    JETON = b"/Parent PAGES 0 R"
    numeros_page: list[int] = []
    for page in pages:
        flux = page.flux()
        numero_flux = ajouter(
            f"<< /Length {len(flux)} >>\nstream\n".encode("ascii") + flux + b"\nendstream"
        )
        numeros_page.append(
            ajouter(
                b"<< /Type /Page "
                + JETON
                + f" /MediaBox [0 0 {_nombre(A4[0])} {_nombre(A4[1])}] ".encode("ascii")
                + b"/Resources "
                + ressources
                + f" /Contents {numero_flux} 0 R >>".encode("ascii")
            )
        )

    numero_pages = ajouter(
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{n} 0 R".encode("ascii") for n in numeros_page)
        + f"] /Count {len(numeros_page)} >>".encode("ascii")
    )
    for numero in numeros_page:
        objets[numero - 1] = objets[numero - 1].replace(
            JETON, f"/Parent {numero_pages} 0 R".encode("ascii")
        )

    infos = ajouter(
        b"<< /Title "
        + _texte_pdf(titre)
        + b" /Author "
        + _texte_pdf(auteur)
        + b" /Producer "
        + _texte_pdf("Metreo")
        + b" /CreationDate "
        + _texte_pdf(date_pdf)
        + b" /ModDate "
        + _texte_pdf(date_pdf)
        + b" >>"
    )
    catalogue = ajouter(f"<< /Type /Catalog /Pages {numero_pages} 0 R >>".encode("ascii"))

    sortie = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    decalages: list[int] = []
    for numero, corps in enumerate(objets, start=1):
        decalages.append(len(sortie))
        sortie += f"{numero} 0 obj\n".encode("ascii") + corps + b"\nendobj\n"

    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n".encode("ascii")
    sortie += b"0000000000 65535 f \n"
    for decalage in decalages:
        sortie += f"{decalage:010d} 00000 n \n".encode("ascii")
    sortie += (
        f"trailer\n<< /Size {len(objets) + 1} /Root {catalogue} 0 R "
        f"/Info {infos} 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(sortie)


def _sans_les_images(pdf: bytes) -> bytes:
    """Le document, ses flux d'image retirés.

    Les échantillons comprimés d'un logo sont des octets quelconques : ils
    contiennent des parenthèses, et la lecture naïve ci-dessous les prendrait
    pour des chaînes littérales. Le texte extrait se remplirait alors d'un
    charabia qui n'est imprimé nulle part — et une assertion d'ABSENCE, celle
    qui vérifie qu'aucun coût interne ne figure sur un devis client, perdrait
    tout son sens.

    On ne coupe que ce qui suit un `/Subtype /Image` : les flux de contenu des
    pages, eux, portent le vrai texte et sont conservés.
    """
    sortie = bytearray()
    position = 0
    while True:
        marque = pdf.find(b"/Subtype /Image", position)
        if marque == -1:
            sortie += pdf[position:]
            return bytes(sortie)
        debut = pdf.find(b"stream\n", marque)
        fin = pdf.find(b"\nendstream", debut) if debut != -1 else -1
        if debut == -1 or fin == -1:
            sortie += pdf[position:]
            return bytes(sortie)
        # La marque doit appartenir à un DICTIONNAIRE d'objet, pas au contenu
        # d'un flux. Sans ce contrôle, une désignation de poste ou une
        # condition qui contiendrait « /Subtype /Image » ferait couper tout ce
        # qui suit jusqu'au bout du flux de la page — une page entière de
        # texte disparaîtrait de la lecture, et un test d'absence passerait
        # pour de mauvaises raisons. Si un `endstream` s'intercale entre la
        # marque et l'ouverture trouvée, c'est que la marque était DANS un flux.
        entre_deux = pdf.find(b"endstream", marque)
        if entre_deux != -1 and entre_deux < debut:
            sortie += pdf[position : marque + len(b"/Subtype /Image")]
            position = marque + len(b"/Subtype /Image")
            continue
        sortie += pdf[position : debut + len(b"stream\n")]
        position = fin


def extraire_le_texte(pdf: bytes) -> str:
    """Relit les chaînes littérales du document — pour les tests, et pour eux seuls.

    Ce n'est pas un analyseur PDF : il suffit à vérifier qu'un numéro de devis,
    un nom de client ou un total figurent dans le fichier produit, ce que ne
    disent ni sa taille ni son code de retour HTTP.
    """
    pdf = _sans_les_images(pdf)
    morceaux: list[str] = []
    index = 0
    while True:
        debut = pdf.find(b"(", index)
        if debut == -1:
            break
        curseur = debut + 1
        brut = bytearray()
        while curseur < len(pdf):
            octet = pdf[curseur]
            if octet == 0x5C:
                suivant = pdf[curseur + 1 : curseur + 2]
                if suivant.isdigit():
                    brut.append(int(pdf[curseur + 1 : curseur + 4], 8))
                    curseur += 4
                    continue
                brut += suivant
                curseur += 2
                continue
            if octet == 0x29:
                break
            brut.append(octet)
            curseur += 1
        morceaux.append(brut.decode("cp1252", errors="replace"))
        index = curseur + 1
    return "\n".join(morceaux)


def compter_les_pages(pdf: bytes) -> int:
    """Le nombre de pages déclaré par le document."""
    marqueur = pdf.find(b"/Type /Pages")
    if marqueur == -1:
        return 0
    compte = pdf.find(b"/Count ", marqueur)
    if compte == -1:
        return 0
    chiffres = bytearray()
    for octet in pdf[compte + 7 :]:
        if not chr(octet).isdigit():
            break
        chiffres.append(octet)
    return int(chiffres or b"0")


def par_tranches(iterable: Iterable[Any], taille: int) -> Iterator[list[Any]]:
    """Découpe en tranches — la pagination du tableau des postes."""
    tranche: list[Any] = []
    for element in iterable:
        tranche.append(element)
        if len(tranche) == taille:
            yield tranche
            tranche = []
    if tranche:
        yield tranche
