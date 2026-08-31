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
#: Largeur MOYENNE d'un caractère Helvetica, pour tronquer un libellé trop
#: long — jamais pour aligner. Une approximation suffit à décider où couper ;
#: elle ne suffirait pas à placer une colonne.
CHASSE_HELVETICA_APPROX = 0.5


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


def tronquer(texte: str, largeur: float, taille: float) -> str:
    """Coupe un libellé qui déborderait de sa colonne, en le disant.

    Les points de suspension sont pris DANS le budget, pas ajoutés après :
    `texte[:maximum - 1] + "..."` rendait `maximum + 2` caractères, et le
    libellé dépassait encore de deux caractères la colonne qu'on venait de
    lui mesurer. Mesuré sur le cadre de signature, où les deux caractères de
    trop mordaient sur le cadre voisin.
    """
    maximum = max(1, int(largeur / (taille * CHASSE_HELVETICA_APPROX)))
    if len(texte) <= maximum:
        return texte
    if maximum <= 3:
        return texte[:maximum]
    return texte[: maximum - 3] + "..."


def replier(texte: str, largeur: float, taille: float) -> list[str]:
    """Replie un texte sur la largeur disponible, sans couper les mots.

    Sauf un mot qui, à lui seul, dépasse la ligne : une raison sociale
    d'intercommunale, une référence de marché, une URL. Le laisser entier le
    ferait sortir de la page — le texte serait dans le fichier, mais pas sur la
    feuille. On le césure alors franchement : un mot coupé se lit, un mot hors
    du papier ne s'imprime pas.

    La largeur est estimée à la chasse MOYENNE d'Helvetica. C'est une
    approximation, et elle suffit ici : il s'agit de décider où passer à la
    ligne, pas de placer une colonne au point près.
    """
    maximum = max(1, int(largeur / (taille * CHASSE_HELVETICA_APPROX)))
    if not texte.strip():
        return []
    lignes: list[str] = []
    courante = ""
    for mot_entier in texte.split():
        mot = mot_entier
        while len(mot) > maximum:
            if courante:
                lignes.append(courante)
                courante = ""
            lignes.append(mot[:maximum])
            mot = mot[maximum:]
        if courante and len(courante) + 1 + len(mot) > maximum:
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    if courante:
        lignes.append(courante)
    return lignes


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
        for ligne in replier(contenu, largeur, taille):
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

    def flux(self) -> bytes:
        return b"\n".join(self.commandes)


def assembler(pages: Sequence[Page], *, titre: str, auteur: str, date_pdf: str) -> bytes:
    """Écrit le fichier : objets, table de références croisées, fin.

    `date_pdf` est au format PDF (`D:AAAAMMJJHHmmSS`) et vient de la date
    d'émission. C'est ce qui rend deux générations identiques au bit près.
    """
    objets: list[bytes] = []

    def ajouter(corps: bytes) -> int:
        objets.append(corps)
        return len(objets)  # les numéros d'objet commencent à 1

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
        + b" >> >>"
    )

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


def extraire_le_texte(pdf: bytes) -> str:
    """Relit les chaînes littérales du document — pour les tests, et pour eux seuls.

    Ce n'est pas un analyseur PDF : il suffit à vérifier qu'un numéro de devis,
    un nom de client ou un total figurent dans le fichier produit, ce que ne
    disent ni sa taille ni son code de retour HTTP.
    """
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
