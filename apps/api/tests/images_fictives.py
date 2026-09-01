"""Des PNG fabriqués ici, pour n'avoir aucun fichier binaire au dépôt.

Un logo de test n'a pas à être un fichier commité : engendré, il se relit dans
le code — on VOIT quel type de couleur, quelle profondeur, quel entrelacement
chaque cas éprouve — et le dépôt reste sans octets opaques.

Toutes les images sont fictives et sans rapport avec une marque réelle.
"""

from __future__ import annotations

import struct
import zlib


def _morceau(type_: bytes, corps: bytes) -> bytes:
    return (
        struct.pack(">I", len(corps))
        + type_
        + corps
        + struct.pack(">I", zlib.crc32(type_ + corps) & 0xFFFFFFFF)
    )


def png(
    *,
    largeur: int,
    hauteur: int,
    type_couleur: int,
    profondeur: int,
    lignes: list[bytes],
    palette: bytes | None = None,
    transparence: bytes | None = None,
    entrelacement: int = 0,
) -> bytes:
    """Assemble un PNG sans filtre (filtre 0 sur chaque ligne)."""
    sortie = b"\x89PNG\r\n\x1a\n"
    sortie += _morceau(
        b"IHDR",
        struct.pack(">IIBBBBB", largeur, hauteur, profondeur, type_couleur, 0, 0, entrelacement),
    )
    if palette is not None:
        sortie += _morceau(b"PLTE", palette)
    if transparence is not None:
        sortie += _morceau(b"tRNS", transparence)
    brut = b"".join(b"\x00" + ligne for ligne in lignes)
    sortie += _morceau(b"IDAT", zlib.compress(brut, 9))
    sortie += _morceau(b"IEND", b"")
    return sortie


def carre(cote: int = 64, *, alpha: bool = False) -> bytes:
    """Un damier carré. Avec alpha, un coin transparent."""
    lignes = []
    for y in range(cote):
        ligne = bytearray()
        for x in range(cote):
            fonce = (x // 8 + y // 8) % 2 == 0
            couleur = (30, 90, 160) if fonce else (240, 240, 240)
            ligne += bytes(couleur)
            if alpha:
                ligne.append(0 if (x < cote // 4 and y < cote // 4) else 255)
        lignes.append(bytes(ligne))
    return png(
        largeur=cote,
        hauteur=cote,
        type_couleur=6 if alpha else 2,
        profondeur=8,
        lignes=lignes,
    )


def horizontal(largeur: int = 240, hauteur: int = 60) -> bytes:
    """Un logo large et bas — la forme la plus courante d'un logotype."""
    lignes = []
    for y in range(hauteur):
        ligne = bytearray()
        for x in range(largeur):
            ligne += bytes((x * 255 // max(1, largeur - 1), 60, y * 255 // max(1, hauteur - 1)))
        lignes.append(bytes(ligne))
    return png(largeur=largeur, hauteur=hauteur, type_couleur=2, profondeur=8, lignes=lignes)


def gris(cote: int = 32) -> bytes:
    lignes = [bytes((x * 255 // max(1, cote - 1)) for x in range(cote)) for _ in range(cote)]
    return png(largeur=cote, hauteur=cote, type_couleur=0, profondeur=8, lignes=lignes)


def palette_transparente(cote: int = 32) -> bytes:
    """Palette de trois couleurs, dont une entièrement transparente."""
    lignes = [bytes((x + y) % 3 for x in range(cote)) for y in range(cote)]
    return png(
        largeur=cote,
        hauteur=cote,
        type_couleur=3,
        profondeur=8,
        lignes=lignes,
        palette=bytes([200, 20, 20, 20, 200, 20, 20, 20, 200]),
        transparence=bytes([255, 128, 0]),
    )


def un_bit(cote: int = 32) -> bytes:
    """Deux couleurs, un bit par pixel — le cas des échantillons sous-octet."""
    octets_par_ligne = (cote + 7) // 8
    lignes = [bytes([0b10110010] * octets_par_ligne) for _ in range(cote)]
    return png(
        largeur=cote,
        hauteur=cote,
        type_couleur=3,
        profondeur=1,
        lignes=lignes,
        palette=bytes([255, 255, 255, 0, 0, 0]),
    )


def seize_bits(cote: int = 32) -> bytes:
    """RVB sur 16 bits : l'octet de poids fort doit être retenu."""
    lignes = [struct.pack(">" + "H" * (cote * 3), *([65535, 0, 32768] * cote)) for _ in range(cote)]
    return png(largeur=cote, hauteur=cote, type_couleur=2, profondeur=16, lignes=lignes)


def entrelace(cote: int = 32) -> bytes:
    """Adam7 — refusé, et le refus doit le nommer."""
    lignes = [bytes([120] * (cote * 3)) for _ in range(cote)]
    return png(
        largeur=cote,
        hauteur=cote,
        type_couleur=2,
        profondeur=8,
        lignes=lignes,
        entrelacement=1,
    )


#: Un SVG qui se prétend PNG. Le contenu doit trancher, jamais l'extension.
SVG_DEGUISE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
    b'<script>fetch("https://exemple.invalid/vol")</script>'
    b'<rect width="64" height="64" fill="red"/></svg>'
)
