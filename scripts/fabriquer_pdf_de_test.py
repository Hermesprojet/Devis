#!/usr/bin/env python3
"""Fabrique les PDF de fixture, à la main et sans aucune bibliothèque.

Pourquoi à la main, alors que le dépôt pourrait commiter quatre PDF : parce
que **le sujet de ces tests est une coordonnée**. Un PDF a son origine en bas
à gauche, l'écran en haut à gauche, et l'inversion est le défaut le plus
probable de tout le lecteur. Pour l'attraper, il faut un fichier dont on
connaisse la position exacte du texte — pas « vers le haut », mais
« 79,8 points au-dessus du bord bas d'une page de 100 points ».

Un PDF produit par un traitement de texte ne dit pas cela. Celui-ci le dit en
une ligne : `_contenu_texte(texte="5000", x=20, y=80)`. Le fichier fait 530
octets et son code tient sur un écran ; c'est le code qui documente le
fichier, exactement comme pour `fabriquer_plans_de_test.py`.

Deuxième raison, la même qu'en face : aucun plan réel n'entre dans le dépôt.
Un PDF « d'exemple » téléchargé quelque part porte un auteur, des polices
embarquées et parfois des métadonnées de client.

**Aucune dépendance, pas même pypdfium2.** Ce module n'écrit que des octets :
il tourne donc dans l'image avant même qu'on sache si la bibliothèque de
lecture y est installée — ce qui est précisément ce que l'épreuve d'image
vérifie.

    python3 scripts/fabriquer_pdf_de_test.py            # décrit ce qu'il sait faire
"""

from __future__ import annotations

import hashlib
import sys

# ---------------------------------------------------------------------------
# L'assembleur : des objets numérotés, une table d'offsets, un trailer
# ---------------------------------------------------------------------------

#: La version annoncée. 1.4 est la dernière avant les flux d'objets
#: compressés : tout tient donc en objets lisibles, et un `grep` sur la
#: fixture montre ce qu'elle contient.
ENTETE = b"%PDF-1.4\n"


def assembler(objets: list[bytes], racine: int, *, extra_trailer: bytes = b"") -> bytes:
    """Des corps d'objets numérotés depuis 1, et la table d'offsets qui va avec.

    La table `xref` est la seule partie d'un PDF qu'on ne peut pas écrire à la
    main : chaque entrée est l'offset en octets du début de son objet, sur
    exactement dix chiffres. Une erreur d'un octet et le fichier est illisible
    — c'est pour cela qu'on la CALCULE ici au lieu de la recopier.
    """
    sortie = bytearray(ENTETE)
    offsets: list[int] = []
    for numero, corps in enumerate(objets, start=1):
        offsets.append(len(sortie))
        sortie += f"{numero} 0 obj\n".encode("ascii") + corps + b"\nendobj\n"

    depart = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n".encode("ascii")
    # L'entrée 0 est toujours libre et pointe la fin de la liste chaînée.
    sortie += b"0000000000 65535 f \n"
    for offset in offsets:
        sortie += f"{offset:010d} 00000 n \n".encode("ascii")
    sortie += (
        f"trailer\n<< /Size {len(objets) + 1} /Root {racine} 0 R ".encode("ascii")
        + extra_trailer
        + b">>\nstartxref\n"
        + str(depart).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(sortie)


def _flux(contenu: bytes) -> bytes:
    """Un objet flux. `/Length` est calculé : un flux dont la longueur ment
    n'est pas lu par PDFium, et le test échouerait pour la mauvaise raison."""
    return (
        b"<< /Length "
        + str(len(contenu)).encode("ascii")
        + b" >>\nstream\n"
        + contenu
        + b"\nendstream"
    )


def _contenu_texte(texte: str, x: float, y: float, corps: float = 12.0) -> bytes:
    """Le flux de contenu d'une page portant UN texte, placé exactement.

    `x` et `y` sont en points PostScript depuis le coin **bas gauche** : c'est
    le repère du PDF, et c'est celui que le lecteur doit inverser.
    """
    return f"BT /F1 {corps:g} Tf {x:g} {y:g} Td ({texte}) Tj ET\n".encode("ascii")


# ---------------------------------------------------------------------------
# Les fixtures
# ---------------------------------------------------------------------------

#: La page de référence : 200 × 100 points, et le texte « 5000 » dont on
#: connaît la position attendue à l'écran. Petite exprès — une page A0
#: rendrait le calcul d'inversion illisible à la lecture du test.
LARGEUR_REFERENCE = 200.0
HAUTEUR_REFERENCE = 100.0
TEXTE_REFERENCE = "5000"
X_REFERENCE = 20.0
Y_REFERENCE = 80.0


def une_page_avec_texte(
    texte: str = TEXTE_REFERENCE,
    *,
    x: float = X_REFERENCE,
    y: float = Y_REFERENCE,
    largeur: float = LARGEUR_REFERENCE,
    hauteur: float = HAUTEUR_REFERENCE,
) -> bytes:
    """Une page, un texte, une position connue. La fixture de référence.

    La police est Helvetica **non embarquée** : c'est l'une des quatorze
    polices que tout lecteur de PDF doit connaître, donc le fichier reste
    autonome sans porter un seul octet de fonte.
    """
    contenu = _contenu_texte(texte, x, y)
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largeur:g} {hauteur:g}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(contenu),
        ],
        racine=1,
    )


#: Ce que porte `plan_minimal` : des textes de cote, placés comme sur un
#: cartouche et une façade. Les valeurs ressemblent à celles d'un plan
#: d'exécution — en millimètres, sans unité écrite — et le total dépasse le
#: seuil de texte maigre, ce qui est le but : cette fixture représente un plan
#: VECTORIEL, pas un scan.
PLACEMENTS_DU_PLAN: tuple[tuple[str, float, float], ...] = (
    ("5000", 40.0, 180.0),
    ("2500", 40.0, 120.0),
    ("3250", 180.0, 180.0),
    ("1200", 40.0, 150.0),
    ("4800", 180.0, 150.0),
    ("Facade sud", 40.0, 30.0),
    ("Ech. 1:50", 180.0, 30.0),
    ("NIV +0.00", 180.0, 120.0),
    ("Coupe A-A", 40.0, 60.0),
)


def page_avec_plusieurs_textes(
    placements: tuple[tuple[str, float, float], ...] = PLACEMENTS_DU_PLAN,
    *,
    largeur: float = 300.0,
    hauteur: float = 220.0,
) -> bytes:
    """Une page portant plusieurs textes, chacun à une position connue.

    Sert à deux choses qu'une page à un seul texte ne peut pas prouver : que
    chaque fragment garde SA position (et non celle du précédent), et que le
    document est reconnu comme porteur de texte — le seuil de texte maigre ne
    se franchit pas avec quatre caractères.
    """
    contenu = b"".join(_contenu_texte(texte, x, y) for texte, x, y in placements)
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largeur:g} {hauteur:g}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(contenu),
        ],
        racine=1,
    )


def deux_pages_de_tailles_differentes() -> bytes:
    """Deux pages, deux formats, un texte identifiable sur chacune.

    Le format qui change d'une page à l'autre est le cas réel d'un dossier :
    un plan A1 et sa note A4 dans le même fichier. Il éprouve deux choses que
    deux pages identiques ne diraient pas — que `dimensions` suit bien la page
    et non la première, et que la normalisation divise par la hauteur de LA
    page du fragment.
    """
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(_contenu_texte("PREMIERE", 20.0, 80.0)),
            _flux(_contenu_texte("SECONDE", 20.0, 320.0)),
        ],
        racine=1,
    )


def page_avec_boite_decalee(
    texte: str = TEXTE_REFERENCE, *, origine_x: float = 100.0, origine_y: float = 50.0
) -> bytes:
    """Une page dont le coin bas gauche n'est PAS (0, 0).

    Le format l'autorise, et un plan exporté depuis un logiciel de mise en page
    le fait. Le texte est posé au même endroit RELATIF que sur la fixture de
    référence : les deux doivent donc rendre le même cadre normalisé.

    Sans cette fixture, un lecteur qui divise une coordonnée absolue par une
    DIMENSION passe tous les autres tests, et pose la boîte ailleurs — ou,
    quand le décalage est grand, l'écrase sur un bord en une boîte plate, que
    la contrainte `ck_source_citation_bbox` refuse.
    """
    contenu = _contenu_texte(texte, origine_x + X_REFERENCE, origine_y + Y_REFERENCE)
    boite = (
        f"[{origine_x:g} {origine_y:g} "
        f"{origine_x + LARGEUR_REFERENCE:g} {origine_y + HAUTEUR_REFERENCE:g}]"
    ).encode("ascii")
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox " + boite + b" "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(contenu),
        ],
        racine=1,
    )


def page_tournee(rotation: int, texte: str = TEXTE_REFERENCE) -> bytes:
    """La page de référence, avec `/Rotate`. Le texte ne bouge pas, l'affichage si.

    `get_size()` rend alors la taille AFFICHÉE — (100, 200) pour une page de
    200 × 100 tournée de 90° — tandis que les rectangles de texte restent dans
    le repère non tourné. Diviser l'un par l'autre pose la boîte n'importe où.
    """
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox "
            f"[0 0 {LARGEUR_REFERENCE:g} {HAUTEUR_REFERENCE:g}] /Rotate {rotation:d} "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(_contenu_texte(texte, X_REFERENCE, Y_REFERENCE)),
        ],
        racine=1,
    )


def page_avec_texte_hors_cadre() -> bytes:
    """Un texte posé en dehors de la page. Le format ne l'interdit pas.

    Son emplacement est inconnu — pas « au bord » : l'écraser sur un bord
    inventerait une position, et le propriétaire chercherait la cote là.
    """
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _flux(_contenu_texte(TEXTE_REFERENCE, -500.0, -500.0)),
        ],
        racine=1,
    )


def page_sans_texte(*, largeur: float = 300.0, hauteur: float = 200.0) -> bytes:
    """Une page qui porte un TRAIT et pas un caractère : l'image d'un scan.

    Un scan est une page sans texte extractible. Celle-ci l'est aussi, et
    sans embarquer une image — ce qui suffit : le lecteur compte des
    caractères, il ne regarde pas s'il y a un bitmap.
    """
    contenu = b"1 w 10 10 m 290 190 l S\n"
    return assembler(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largeur:g} {hauteur:g}] "
            f"/Contents 4 0 R >>".encode("ascii"),
            _flux(contenu),
        ],
        racine=1,
    )


def beaucoup_de_pages(combien: int) -> bytes:
    """`combien` pages identiques et vides, pour éprouver le plafond.

    Chaque page partage le même flux de contenu : le fichier reste petit quel
    que soit le nombre, et c'est bien le COMPTE qui est éprouvé, pas la taille.
    """
    premier_numero_de_page = 4
    enfants = " ".join(f"{premier_numero_de_page + index} 0 R" for index in range(combien))
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{enfants}] /Count {combien} >>".encode("ascii"),
        _flux(b"1 w 5 5 m 95 95 l S\n"),
    ]
    objets += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 3 0 R >>"] * combien
    return assembler(objets, racine=1)


# ---------------------------------------------------------------------------
# Le PDF chiffré : écrit depuis la spécification, pas simulé
# ---------------------------------------------------------------------------

#: Le bourrage de 32 octets de la spécification PDF (algorithme 2, §7.6.3.3).
#: Ces octets sont une constante du format, pas un choix : ils complètent tout
#: mot de passe plus court que 32 caractères.
#:
#: La disposition en quatre lignes de huit est celle du tableau de la
#: spécification : c'est ainsi qu'on relit ces octets pour les vérifier, et
#: trente-deux lignes d'un octet ne se relisent pas. D'où le `fmt: off`.
# fmt: off
BOURRAGE = bytes([
    0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41,
    0x64, 0x00, 0x4E, 0x56, 0xFF, 0xFA, 0x01, 0x08,
    0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
    0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A,
])
# fmt: on

#: Un identifiant de fichier FIXE. Un `os.urandom` rendrait la fixture
#: différente à chaque fabrication, et un échec ne serait plus reproductible.
IDENTIFIANT = bytes(range(16))

#: Les permissions. -1 autorise tout : ce fichier éprouve le CHIFFREMENT,
#: pas la politique de permissions, et restreindre brouillerait le sujet.
PERMISSIONS = -1


def _rc4(cle: bytes, donnees: bytes) -> bytes:
    """RC4, en douze lignes, parce qu'il a disparu des bibliothèques standard.

    Il a disparu pour une bonne raison — il est cassé — et c'est précisément
    pourquoi il est écrit ICI et nulle part ailleurs : ce code sert à
    FABRIQUER un fichier à refuser, jamais à protéger quoi que ce soit.
    """
    etat = list(range(256))
    j = 0
    for i in range(256):
        j = (j + etat[i] + cle[i % len(cle)]) % 256
        etat[i], etat[j] = etat[j], etat[i]
    sortie = bytearray()
    i = j = 0
    for octet in donnees:
        i = (i + 1) % 256
        j = (j + etat[i]) % 256
        etat[i], etat[j] = etat[j], etat[i]
        sortie.append(octet ^ etat[(etat[i] + etat[j]) % 256])
    return bytes(sortie)


def _bourrer(mot_de_passe: bytes) -> bytes:
    return (mot_de_passe + BOURRAGE)[:32]


def chiffre(mot_de_passe: str = "metreo-fixture") -> bytes:
    """Un PDF réellement chiffré (RC4 40 bits, révision 2), à refuser.

    Révision 2 et pas 4 : c'est la variante la plus courte à écrire
    correctement, et le lecteur ne la distingue pas des autres — il constate
    qu'un mot de passe est demandé, et s'arrête là. Un AES-256 éprouverait le
    même chemin pour trente lignes de plus.

    Le mot de passe est en clair dans ce fichier, et c'est voulu : **ce n'est
    pas un secret**, c'est la graine d'une fixture. Il n'ouvre rien d'autre
    que ces 700 octets, et le lecteur de Metreo ne le demande jamais.
    """
    utilisateur = _bourrer(mot_de_passe.encode("latin-1"))

    # Algorithme 3 : la valeur /O, dérivée du mot de passe propriétaire —
    # absent ici, donc c'est celui de l'utilisateur qui sert, comme le dit la
    # spécification.
    cle_o = hashlib.md5(utilisateur, usedforsecurity=False).digest()[:5]
    valeur_o = _rc4(cle_o, utilisateur)

    # Algorithme 2 : la clé de chiffrement du document.
    empreinte = hashlib.md5(usedforsecurity=False)
    empreinte.update(utilisateur)
    empreinte.update(valeur_o)
    empreinte.update(PERMISSIONS.to_bytes(4, "little", signed=True))
    empreinte.update(IDENTIFIANT)
    cle = empreinte.digest()[:5]

    # Algorithme 4 : la valeur /U, qui permet au lecteur de vérifier un mot de
    # passe sans l'avoir.
    valeur_u = _rc4(cle, BOURRAGE)

    def _par_objet(numero: int) -> bytes:
        """La clé d'un objet : la clé du document, salée par son numéro."""
        graine = cle + numero.to_bytes(3, "little") + (0).to_bytes(2, "little")
        return hashlib.md5(graine, usedforsecurity=False).digest()[: len(cle) + 5]

    # Le flux de contenu est chiffré avec la clé de SON objet (le numéro 5).
    contenu = _rc4(_par_objet(5), _contenu_texte("1234", 20.0, 80.0))

    def _litteral(donnees: bytes) -> bytes:
        """Une chaîne PDF entre parenthèses, échappée. `/O` et `/U` sont des
        octets quelconques : sans échappement, une parenthèse fermante au
        milieu couperait la chaîne et le fichier serait illisible."""
        sortie = bytearray(b"(")
        for octet in donnees:
            if octet in (0x28, 0x29, 0x5C):  # ( ) \
                sortie += b"\\"
            sortie.append(octet)
        sortie += b")"
        return bytes(sortie)

    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        _flux(contenu),
        b"<< /Filter /Standard /V 1 /R 2 /O "
        + _litteral(valeur_o)
        + b" /U "
        + _litteral(valeur_u)
        + f" /P {PERMISSIONS} >>".encode("ascii"),
    ]
    identifiant = _litteral(IDENTIFIANT)
    return assembler(
        objets,
        racine=1,
        extra_trailer=b"/Encrypt 6 0 R /ID [" + identifiant + identifiant + b"] ",
    )


def pas_un_pdf() -> bytes:
    """Des octets qui ne commencent pas par `%PDF-`. Le refus doit NOMMER ça.

    Le contenu ressemble à du DXF exprès : un fichier mal rangé par
    l'utilisateur est le cas réel, pas un fichier aléatoire.
    """
    return b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"


def tronque() -> bytes:
    """Un PDF dont la table d'offsets a été coupée : la signature est bonne,
    la structure non. Le refus ne doit donc pas être `pas_un_pdf`."""
    complet = une_page_avec_texte()
    return complet[: complet.index(b"xref")]


def main() -> int:
    """Décrit les fixtures et leur taille. Ne les écrit nulle part.

    Volontairement : ces PDF sont consommés en mémoire par les tests et par
    l'épreuve d'image. Les poser sur le disque créerait des fichiers que rien
    ne nettoie et que `git status` montrerait — le défaut exact qui a déjà
    fait commiter des fixtures générées par mégarde.
    """
    for nom, octets in (
        ("une_page_avec_texte", une_page_avec_texte()),
        ("page_avec_plusieurs_textes", page_avec_plusieurs_textes()),
        ("deux_pages_de_tailles_differentes", deux_pages_de_tailles_differentes()),
        ("page_avec_boite_decalee", page_avec_boite_decalee()),
        ("page_tournee(90)", page_tournee(90)),
        ("page_avec_texte_hors_cadre", page_avec_texte_hors_cadre()),
        ("page_sans_texte", page_sans_texte()),
        ("beaucoup_de_pages(51)", beaucoup_de_pages(51)),
        ("chiffre", chiffre()),
        ("pas_un_pdf", pas_un_pdf()),
        ("tronque", tronque()),
    ):
        print(f"{nom:34} {len(octets):6d} octets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
