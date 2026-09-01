"""Lire un PNG et n'en garder que ce qu'un PDF sait dessiner.

Ce module est la frontière entre un fichier reçu et une image que le moteur PDF
acceptera. Comme `document_storage`, il ne croit **rien** de ce que l'appelant
annonce : ni l'extension, ni le type MIME, ni les dimensions déclarées. Ce qui
fait foi est ce que les octets disent d'eux-mêmes.

**Pourquoi PNG seul.** Le logo d'une entreprise est un aplat de couleurs et du
texte : PNG le rend sans perte et porte la transparence. C'est aussi le seul
format que ce dépôt peut prouver de bout en bout — décodé ici, réencodé en flux
PDF, et relu octet par octet dans un test. Déclarer JPEG « pris en charge »
sans fixture capable de l'établir serait une promesse que rien ne vérifie.
Ouvrir SVG serait pire : c'est un document XML exécutable, avec entités
externes et scripts, servi ensuite à des navigateurs — un logo n'a pas besoin
d'un langage de programmation.

**Ce qui est refusé, et pourquoi.** L'entrelacement Adam7 réordonne les pixels
en sept passes ; le décoder demanderait un second chemin de code qu'aucun logo
réel n'emprunte. Les profondeurs de 16 bits sont ramenées à 8 — un PDF n'en
demande pas plus pour un logo, et l'octet de poids fort suffit. Tout le reste
— type de couleur inconnu, palette absente, index hors palette, données
tronquées — est un refus nommé, jamais une image approximative.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from metreo_domain.errors import DomainError

#: La signature d'un PNG. Huit octets, choisis pour détecter un transfert
#: qui aurait converti les fins de ligne.
SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"

#: Le type que ce module rend, et le seul qu'il accepte.
TYPE_PNG = "image/png"

#: Nombre de canaux par type de couleur PNG.
CANAUX: dict[int, int] = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

#: Bornes de dimensions. Un logo plus petit ne s'imprime pas ; un logo plus
#: grand ne sert à rien, et son décodage coûte un temps que personne n'a
#: demandé de dépenser.
LARGEUR_MINIMALE = 16
HAUTEUR_MINIMALE = 16
LARGEUR_MAXIMALE = 2000
HAUTEUR_MAXIMALE = 2000

#: Plafond de pixels, indépendant des bornes ci-dessus.
#:
#: Ce n'est pas un confort : `_defiltrer` parcourt les échantillons UN PAR UN
#: en Python, et son coût est donc linéaire en pixels. Mesuré : quatre
#: millions de pixels en RVBA occupent le décodeur plus de dix secondes, pour
#: un fichier d'entrée de quelques dizaines de kilooctets — des lignes
#: constantes se compriment presque à néant. Un million ramène le pire cas
#: sous la seconde et demie.
#:
#: Un million reste large : la boîte qui reçoit le logo sur le devis fait
#: 108 × 46 points, soit 450 × 192 pixels à 300 points par pouce — quatre-vingt
#: mille pixels. On accepte dix fois cela.
PIXELS_MAXIMUM = 1_000_000

#: Taille du fichier reçu. Un logo est un aplat : deux mégaoctets sont déjà
#: généreux, et le plafond général des dépôts (25 Mio) serait ici une porte
#: ouverte sans usage.
OCTETS_MAXIMUM = 2 * 1024 * 1024


class ImageRefusee(DomainError):
    """Un refus que l'écran doit pouvoir montrer à qui téléverse."""

    def __init__(self, code: str, message: str, **contexte: object) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True, slots=True)
class ImageDecodee:
    """Une image prête pour le PDF : échantillons 8 bits, alpha à part.

    `couleur` porte 1 octet par pixel en gris, 3 en RVB. `alpha`, quand il
    existe, porte 1 octet par pixel et deviendra le `/SMask` du XObject. Il
    vaut `None` quand l'image est entièrement opaque : un masque uniforme
    alourdirait le document sans rien changer à ce qu'on voit.
    """

    largeur: int
    hauteur: int
    espace: str
    couleur: bytes
    alpha: bytes | None

    @property
    def opaque(self) -> bool:
        return self.alpha is None


def _morceaux(donnees: bytes):
    """Parcourt les morceaux PNG, en refusant tout ce qui ne tient pas.

    La longueur annoncée par un morceau est vérifiée contre ce qui reste :
    c'est ce qui empêche un fichier tronqué — ou taillé exprès — de faire lire
    au-delà de la fin.
    """
    position = 8
    while position + 8 <= len(donnees):
        (longueur,) = struct.unpack(">I", donnees[position : position + 4])
        if longueur > len(donnees):
            raise ImageRefusee("png_tronque", "Ce PNG annonce un morceau plus long que le fichier.")
        type_ = donnees[position + 4 : position + 8]
        corps = donnees[position + 8 : position + 8 + longueur]
        if len(corps) != longueur:
            raise ImageRefusee("png_tronque", "Ce PNG est tronqué.")
        yield type_, corps
        position += 12 + longueur


def _defiltrer(brut: bytes, hauteur: int, octets_par_pixel: int, octets_par_ligne: int) -> bytes:
    """Annule les filtres par ligne du PNG.

    Chaque ligne porte son filtre en premier octet et se reconstruit à partir
    de la précédente. C'est le cœur du format : sans cette étape, les octets
    décompressés ne sont pas des couleurs mais des différences.
    """
    sortie = bytearray()
    precedente = bytearray(octets_par_ligne)
    position = 0
    for _ in range(hauteur):
        if position >= len(brut):
            raise ImageRefusee("png_tronque", "Ce PNG annonce plus de lignes qu'il n'en porte.")
        filtre = brut[position]
        position += 1
        ligne = bytearray(brut[position : position + octets_par_ligne])
        position += octets_par_ligne
        if len(ligne) != octets_par_ligne:
            raise ImageRefusee("png_tronque", "Une ligne de ce PNG est incomplète.")
        pas = octets_par_pixel
        if filtre == 0:
            pass  # aucune transformation : la ligne est déjà ses couleurs
        elif filtre == 1:  # Sub
            for k in range(pas, octets_par_ligne):
                ligne[k] = (ligne[k] + ligne[k - pas]) & 0xFF
        elif filtre == 2:  # Up
            for k in range(octets_par_ligne):
                ligne[k] = (ligne[k] + precedente[k]) & 0xFF
        elif filtre == 3:  # Average
            for k in range(octets_par_ligne):
                gauche = ligne[k - pas] if k >= pas else 0
                ligne[k] = (ligne[k] + ((gauche + precedente[k]) >> 1)) & 0xFF
        elif filtre == 4:  # Paeth
            for k in range(octets_par_ligne):
                a = ligne[k - pas] if k >= pas else 0
                b = precedente[k]
                c = precedente[k - pas] if k >= pas else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predit = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                ligne[k] = (ligne[k] + predit) & 0xFF
        else:
            raise ImageRefusee("png_filtre_inconnu", f"Filtre PNG inconnu ({filtre}).")
        sortie += ligne
        precedente = ligne
    return bytes(sortie)


def _echantillons(ligne: bytes, largeur: int, canaux: int, profondeur: int) -> list[int]:
    """Les échantillons d'UNE ligne, ramenés à des entiers.

    Sous 8 bits, plusieurs échantillons partagent un octet, gros-boutiste. À
    16 bits, seul l'octet de poids fort est retenu : un logo ne demande pas
    davantage, et un PDF à 8 bits par composante non plus.
    """
    if profondeur == 8:
        return list(ligne[: largeur * canaux])
    if profondeur == 16:
        return [ligne[i] for i in range(0, largeur * canaux * 2, 2)]
    total = largeur * canaux
    par_octet = 8 // profondeur
    masque = (1 << profondeur) - 1
    valeurs: list[int] = []
    for i in range(total):
        octet = ligne[i // par_octet]
        decalage = 8 - profondeur * (i % par_octet + 1)
        valeurs.append((octet >> decalage) & masque)
    return valeurs


def lire_png(donnees: bytes) -> ImageDecodee:
    """Décode un PNG, ou explique pourquoi il est refusé.

    Palette et transparence sont **développées** en RVB plus alpha plutôt que
    portées telles quelles dans le PDF : un espace `/Indexed` et un `/Mask` par
    index sont deux chemins de plus à écrire et à éprouver, pour un fichier qui
    pèse quelques kilooctets de toute façon.
    """
    if not donnees.startswith(SIGNATURE_PNG):
        raise ImageRefusee(
            "format_non_supporte",
            "Seul le format PNG est accepté pour un logo. "
            "Ces octets ne portent pas la signature d'un PNG.",
        )

    entete: tuple[int, ...] | None = None
    palette: bytes | None = None
    transparence: bytes | None = None
    donnees_image = bytearray()
    for type_, corps in _morceaux(donnees):
        if type_ == b"IHDR":
            if len(corps) != 13:
                raise ImageRefusee("png_entete_invalide", "L'en-tête de ce PNG est invalide.")
            entete = struct.unpack(">IIBBBBB", corps)
        elif type_ == b"PLTE":
            palette = corps
        elif type_ == b"tRNS":
            transparence = corps
        elif type_ == b"IDAT":
            donnees_image += corps
        elif type_ == b"IEND":
            break

    if entete is None:
        raise ImageRefusee("png_entete_invalide", "Ce PNG n'a pas d'en-tête IHDR.")
    largeur, hauteur, profondeur, type_couleur, compression, filtre, entrelacement = entete

    if largeur < LARGEUR_MINIMALE or hauteur < HAUTEUR_MINIMALE:
        raise ImageRefusee(
            "image_trop_petite",
            f"Ce logo mesure {largeur}×{hauteur} pixels. "
            f"Le minimum est {LARGEUR_MINIMALE}×{HAUTEUR_MINIMALE} : "
            "en dessous, il ne serait pas lisible à l'impression.",
            width=largeur,
            height=hauteur,
        )
    if largeur > LARGEUR_MAXIMALE or hauteur > HAUTEUR_MAXIMALE:
        raise ImageRefusee(
            "image_trop_grande",
            f"Ce logo mesure {largeur}×{hauteur} pixels. "
            f"Le maximum est {LARGEUR_MAXIMALE}×{HAUTEUR_MAXIMALE}.",
            width=largeur,
            height=hauteur,
        )
    if largeur * hauteur > PIXELS_MAXIMUM:
        raise ImageRefusee(
            "image_trop_grande",
            f"Ce logo porte {largeur * hauteur} pixels, au-delà des {PIXELS_MAXIMUM} acceptés.",
            width=largeur,
            height=hauteur,
        )
    if entrelacement != 0:
        raise ImageRefusee(
            "png_entrelace",
            "Ce PNG est entrelacé. Réenregistrez-le sans entrelacement : "
            "c'est une option de votre outil de dessin, et elle ne sert à rien ici.",
        )
    if compression != 0 or filtre != 0:
        raise ImageRefusee("png_methode_inconnue", "Ce PNG utilise une méthode non normalisée.")
    if type_couleur not in CANAUX:
        raise ImageRefusee("png_type_couleur", f"Type de couleur PNG inconnu ({type_couleur}).")
    if profondeur not in (1, 2, 4, 8, 16):
        raise ImageRefusee("png_profondeur", f"Profondeur PNG inconnue ({profondeur}).")
    if type_couleur in (2, 4, 6) and profondeur < 8:
        raise ImageRefusee(
            "png_profondeur",
            f"Profondeur {profondeur} incompatible avec ce type de couleur.",
        )
    if type_couleur == 3 and palette is None:
        raise ImageRefusee("png_palette_absente", "Ce PNG à palette n'en porte aucune.")
    if not donnees_image:
        raise ImageRefusee("png_sans_donnees", "Ce PNG ne porte aucune donnée d'image.")

    canaux = CANAUX[type_couleur]
    bits_par_pixel = canaux * profondeur
    octets_par_ligne = (largeur * bits_par_pixel + 7) // 8
    octets_par_pixel = max(1, bits_par_pixel // 8)

    # La décompression est bornée à ce que l'IHDR — déjà validé — autorise.
    #
    # Le plafond de 2 Mio sur le FICHIER ne borne pas la mémoire développée :
    # DEFLATE atteint un rapport d'environ 1030 pour 1, si bien que deux
    # mégaoctets d'IDAT portent deux gigaoctets de zéros. Mesuré : un « PNG »
    # de 204 Ko dont l'en-tête annonce 16 × 16 faisait croître le processus de
    # 200 Mio, et rien ne l'interdisait — `_defiltrer` ne lit que les premières
    # lignes, mais APRÈS que tout a été développé.
    #
    # `max_length` fait ce que le plafond d'octets ne pouvait pas faire : il
    # borne l'allocation. Et un reste non consommé prouve que l'IDAT ment sur
    # l'IHDR — c'est un refus nommé, pas une image approximative.
    attendu = hauteur * (1 + octets_par_ligne)
    decodeur = zlib.decompressobj()
    try:
        brut = decodeur.decompress(bytes(donnees_image), attendu)
    except zlib.error as refus:
        raise ImageRefusee("png_illisible", "Les données de ce PNG sont illisibles.") from refus
    if decodeur.unconsumed_tail:
        raise ImageRefusee(
            "png_incoherent",
            "Ce PNG porte plus de données d'image que ses dimensions n'en admettent.",
        )

    lignes = _defiltrer(brut, hauteur, octets_par_pixel, octets_par_ligne)

    couleur = bytearray()
    alpha = bytearray()
    maximum = (1 << profondeur) - 1

    # `tRNS` ne veut pas dire la même chose selon le type de couleur : un index
    # par entrée de palette, une valeur de gris, ou un triplet RVB. Les trois
    # sont lus ici pour que la transparence ne se perde dans aucun cas.
    gris_transparent: int | None = None
    rvb_transparent: tuple[int, ...] | None = None
    if transparence is not None and type_couleur == 0 and len(transparence) >= 2:
        # Deux octets gros-boutistes ; on ne garde que l'octet de poids fort,
        # comme pour les échantillons.
        gris_transparent = transparence[0] if profondeur == 16 else transparence[1]
    if transparence is not None and type_couleur == 2 and len(transparence) >= 6:
        indices = (0, 2, 4) if profondeur == 16 else (1, 3, 5)
        rvb_transparent = tuple(transparence[i] for i in indices)
    for y in range(hauteur):
        ligne = lignes[y * octets_par_ligne : (y + 1) * octets_par_ligne]
        valeurs = _echantillons(ligne, largeur, canaux, profondeur)
        if type_couleur == 0:
            couleur += bytes(v * 255 // maximum if profondeur < 8 else v for v in valeurs)
            if gris_transparent is not None:
                # `tRNS` désigne UNE valeur de gris à rendre transparente. La
                # collecter sans l'appliquer aurait fait perdre au logo sa
                # transparence en silence — un fond blanc là où l'entreprise
                # avait dessiné du vide.
                alpha += bytes(
                    # `v` est l'échantillon BRUT : `tRNS` désigne la valeur
                    # dans la profondeur d'origine, pas la valeur mise à
                    # l'échelle sur huit bits.
                    0 if v == gris_transparent else 255
                    for v in valeurs
                )
        elif type_couleur == 2:
            couleur += bytes(valeurs)
            if rvb_transparent is not None:
                for i in range(0, len(valeurs), 3):
                    alpha.append(0 if tuple(valeurs[i : i + 3]) == rvb_transparent else 255)
        elif type_couleur == 3:
            assert palette is not None
            for index in valeurs:
                if index * 3 + 3 > len(palette):
                    raise ImageRefusee(
                        "png_index_hors_palette",
                        "Ce PNG désigne une couleur absente de sa palette.",
                    )
                couleur += palette[index * 3 : index * 3 + 3]
                alpha.append(
                    transparence[index]
                    if transparence is not None and index < len(transparence)
                    else 255
                )
        elif type_couleur == 4:
            for i in range(0, len(valeurs), 2):
                gris = valeurs[i] * 255 // maximum if profondeur < 8 else valeurs[i]
                couleur.append(gris)
                alpha.append(valeurs[i + 1])
        else:  # 6 — RVB + alpha
            for i in range(0, len(valeurs), 4):
                couleur += bytes(valeurs[i : i + 3])
                alpha.append(valeurs[i + 3])

    espace = "DeviceGray" if type_couleur in (0, 4) else "DeviceRGB"
    # Un masque entièrement opaque n'apprend rien au lecteur de PDF : on ne le
    # garde que s'il porte réellement une transparence.
    masque = bytes(alpha) if alpha and any(v != 255 for v in alpha) else None
    return ImageDecodee(
        largeur=largeur, hauteur=hauteur, espace=espace, couleur=bytes(couleur), alpha=masque
    )


def verifier_un_logo(donnees: bytes) -> ImageDecodee:
    """Le contrôle complet d'un logo reçu : taille du fichier, puis contenu.

    Le plafond d'octets écarte l'envoi manifestement démesuré, et rien de plus :
    il ne borne PAS la mémoire développée, DEFLATE comprimant jusqu'à mille
    pour un. C'est `lire_png` qui borne l'allocation, à la taille que l'en-tête
    de l'image autorise. Les deux gardes sont nécessaires et aucune ne remplace
    l'autre.
    """
    if not donnees:
        raise ImageRefusee("fichier_vide", "Ce fichier est vide.")
    if len(donnees) > OCTETS_MAXIMUM:
        raise ImageRefusee(
            "fichier_trop_volumineux",
            f"Ce fichier pèse {len(donnees)} octets. Le maximum est "
            f"{OCTETS_MAXIMUM} octets ({OCTETS_MAXIMUM // (1024 * 1024)} Mio).",
            byte_size=len(donnees),
            maximum=OCTETS_MAXIMUM,
        )
    return lire_png(donnees)
