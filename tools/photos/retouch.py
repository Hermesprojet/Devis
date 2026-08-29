#!/usr/bin/env python3
"""Retouche par lot de photos de véhicule pour une annonce mobile.de.

Trois traitements, choisis automatiquement ou imposés par fichier :

  studio      détourage du véhicule et incrustation sur un fond studio uni
  harmonise   décor conservé mais flouté/désaturé, sujet net
  correction  aucune découpe, uniquement exposition/balance des blancs/cadrage

Toutes les sorties partagent le même format (4:3, 1600x1200 par défaut), la
même colorimétrie et le même fond, ce qui donne une galerie homogène.

Usage :
    python3 tools/photos/retouch.py photos/ -o photos_retouchees/
    python3 tools/photos/retouch.py photos/ -o out/ --mode studio
    python3 tools/photos/retouch.py photos/IMG_01.jpg -o out/ --mode harmonise
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from scipy import ndimage

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# Fond studio : dégradé vertical clair, plancher légèrement plus soutenu.
CIEL_HAUT = (247, 248, 249)
CIEL_BAS = (214, 217, 221)
SOL_HAUT = (206, 209, 213)
SOL_BAS = (168, 172, 178)


@dataclass
class Reglages:
    largeur: int = 1600
    hauteur: int = 1200
    qualite: int = 92
    # Part de la largeur du cadre occupée par le véhicule détouré.
    emprise: float = 0.88
    # Position verticale du bas du véhicule dans le cadre.
    ligne_de_sol: float = 0.80
    modele: str = "isnet-general-use"


# --------------------------------------------------------------------------
# Corrections colorimétriques
# --------------------------------------------------------------------------

def balance_des_blancs(img: Image.Image, force: float = 0.8) -> Image.Image:
    """Gray-world tempéré : neutralise la dominante sans virer les couleurs."""
    arr = np.asarray(img, dtype=np.float32)
    # On ignore les pixels brûlés, qui fausseraient les moyennes.
    valides = arr.max(axis=2) < 250
    if valides.sum() < arr.shape[0] * arr.shape[1] * 0.05:
        return img
    moyennes = np.array([arr[..., c][valides].mean() for c in range(3)])
    if (moyennes <= 1).any():
        return img
    gains = moyennes.mean() / moyennes
    gains = 1.0 + (gains - 1.0) * force
    gains = np.clip(gains, 0.85, 1.18)
    return Image.fromarray(np.clip(arr * gains, 0, 255).astype(np.uint8))


def etirement_des_niveaux(
    img: Image.Image, bas: float = 0.4, haut: float = 99.6
) -> Image.Image:
    """Recale le point noir et le point blanc sur des percentiles robustes."""
    arr = np.asarray(img, dtype=np.float32)
    luma = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    p_bas, p_haut = np.percentile(luma, [bas, haut])
    if p_haut - p_bas < 20:
        return img
    # Marge de sécurité : on ne colle jamais complètement aux extrêmes.
    p_bas = max(0.0, p_bas - 4.0)
    p_haut = min(255.0, p_haut + 4.0)
    arr = (arr - p_bas) * (255.0 / (p_haut - p_bas))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def eclaircir_les_ombres(img: Image.Image, force: float = 0.35) -> Image.Image:
    """Déboucher les noirs — utile sur les carrosseries sombres et les intérieurs."""
    arr = np.asarray(img, dtype=np.float32) / 255.0
    luma = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    # Masque qui ne joue que dans les tons sombres.
    masque = np.clip(1.0 - luma / 0.55, 0.0, 1.0)[..., None]
    releve = np.power(arr, 1.0 / (1.0 + force))
    arr = arr * (1 - masque) + releve * masque
    return Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8))


def finition(img: Image.Image, contraste=1.06, saturation=1.08, nettete=1.25) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(contraste)
    img = ImageEnhance.Color(img).enhance(saturation)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=int(nettete * 55), threshold=3))
    return img


def corriger(img: Image.Image) -> Image.Image:
    img = balance_des_blancs(img)
    img = etirement_des_niveaux(img)
    img = eclaircir_les_ombres(img)
    return finition(img)


# --------------------------------------------------------------------------
# Détourage
# --------------------------------------------------------------------------

def masque_du_sujet(img: Image.Image, session) -> np.ndarray:
    """Renvoie un masque flottant 0..1 du sujet principal, nettoyé."""
    from rembg import remove

    decoupe = remove(
        img,
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=250,
        alpha_matting_background_threshold=15,
        alpha_matting_erode_size=8,
    )
    alpha = np.asarray(decoupe.split()[-1], dtype=np.float32) / 255.0

    binaire = alpha > 0.5
    if not binaire.any():
        return alpha

    # Une voiture est un bloc unique : on écarte les fragments parasites.
    etiquettes, nombre = ndimage.label(binaire)
    if nombre > 1:
        tailles = ndimage.sum(binaire, etiquettes, range(1, nombre + 1))
        binaire = etiquettes == (int(np.argmax(tailles)) + 1)
    binaire = ndimage.binary_fill_holes(binaire)

    # On garde les demi-teintes d'origine (vitres, jantes) là où le bloc est retenu.
    alpha = np.where(binaire, np.maximum(alpha, 0.0), alpha * 0.0)
    return ndimage.gaussian_filter(alpha, sigma=0.8)


def type_de_photo(alpha: np.ndarray) -> str:
    """Devine s'il s'agit d'une vue extérieure détourable."""
    couverture = float((alpha > 0.5).mean())
    if not 0.06 <= couverture <= 0.80:
        # Presque rien, ou presque tout : intérieur, moteur, coffre, compteur.
        return "correction"
    # Un sujet extérieur repose sur le sol : il touche le bas du cadre ou s'en approche.
    bande_basse = alpha[int(alpha.shape[0] * 0.75):, :] > 0.5
    if bande_basse.mean() < 0.10:
        return "correction"
    return "studio"


# --------------------------------------------------------------------------
# Fond studio
# --------------------------------------------------------------------------

def fond_studio(largeur: int, hauteur: int, horizon: float) -> Image.Image:
    """Cyclorama : mur en dégradé, plancher plus soutenu, halo derrière le sujet."""
    y = np.arange(hauteur, dtype=np.float32)[:, None]
    ligne = int(hauteur * horizon)

    fond = np.zeros((hauteur, largeur, 3), dtype=np.float32)
    for canal in range(3):
        mur = np.linspace(CIEL_HAUT[canal], CIEL_BAS[canal], max(ligne, 1), dtype=np.float32)
        sol = np.linspace(SOL_HAUT[canal], SOL_BAS[canal], max(hauteur - ligne, 1), dtype=np.float32)
        fond[:ligne, :, canal] = mur[:, None]
        fond[ligne:, :, canal] = sol[:, None]

    # Raccord mur/plancher : un cyclorama n'a pas d'arête, il a un galbe.
    galbe = max(int(hauteur * 0.10), 8)
    haut_bande, bas_bande = max(ligne - galbe, 0), min(ligne + galbe, hauteur)
    if bas_bande - haut_bande > 2:
        bande = fond[haut_bande:bas_bande]
        fond[haut_bande:bas_bande] = ndimage.gaussian_filter1d(
            bande, sigma=galbe / 2.2, axis=0, mode="nearest"
        )

    # Halo lumineux centré, qui décolle le véhicule du fond.
    xx = np.linspace(-1, 1, largeur, dtype=np.float32)[None, :]
    yy = ((y / hauteur) - horizon * 0.75) * 2.0
    halo = np.exp(-((xx**2) / 0.55 + (yy**2) / 0.65))
    fond += (halo * 14.0)[..., None]

    # Vignettage discret sur les bords.
    vignette = 1.0 - 0.10 * np.clip((xx**2 + ((y / hauteur - 0.5) * 2) ** 2) / 2.2, 0, 1)
    fond *= vignette[..., None]

    return Image.fromarray(np.clip(fond, 0, 255).astype(np.uint8))


def ombre_portee(largeur: int, hauteur: int, boite: tuple[int, int, int, int]) -> Image.Image:
    """Ellipse floutée sous le véhicule, pour qu'il ne flotte pas."""
    x1, _, x2, y2 = boite
    ombre = np.zeros((hauteur, largeur), dtype=np.float32)

    cx = (x1 + x2) / 2.0
    demi_l = (x2 - x1) * 0.52
    demi_h = max((x2 - x1) * 0.055, 8.0)
    cy = y2 - demi_h * 0.35

    xx = np.arange(largeur, dtype=np.float32)[None, :]
    yy = np.arange(hauteur, dtype=np.float32)[:, None]
    d = ((xx - cx) / demi_l) ** 2 + ((yy - cy) / demi_h) ** 2
    ombre[d < 1.0] = 1.0
    ombre = ndimage.gaussian_filter(ombre, sigma=max(demi_h * 0.55, 6.0))
    ombre = np.clip(ombre * 0.52, 0, 1)
    return Image.fromarray((ombre * 255).astype(np.uint8), mode="L")


def composer_studio(img: Image.Image, alpha: np.ndarray, r: Reglages) -> Image.Image:
    lignes, colonnes = np.where(alpha > 0.4)
    if lignes.size == 0:
        return recadrer(corriger(img), r)

    haut, bas = int(lignes.min()), int(lignes.max())
    gauche, droite = int(colonnes.min()), int(colonnes.max())

    sujet = corriger(img).convert("RGBA")
    sujet.putalpha(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), mode="L"))
    sujet = sujet.crop((gauche, haut, droite + 1, bas + 1))

    # Mise à l'échelle : le véhicule occupe une part fixe de la largeur du cadre.
    cible_l = int(r.largeur * r.emprise)
    ratio = cible_l / sujet.width
    cible_h = int(sujet.height * ratio)
    plafond = int(r.hauteur * (r.ligne_de_sol - 0.06))
    if cible_h > plafond:
        ratio *= plafond / cible_h
        cible_l, cible_h = int(sujet.width * ratio), int(sujet.height * ratio)
    sujet = sujet.resize((max(cible_l, 1), max(cible_h, 1)), Image.LANCZOS)

    x = (r.largeur - sujet.width) // 2
    y = int(r.hauteur * r.ligne_de_sol) - sujet.height

    fond = fond_studio(r.largeur, r.hauteur, r.ligne_de_sol).convert("RGB")
    boite = (x, y, x + sujet.width, y + sujet.height)
    fond = Image.composite(Image.new("RGB", fond.size, (58, 60, 64)), fond, ombre_portee(r.largeur, r.hauteur, boite))
    fond.paste(sujet, (x, y), sujet)
    return fond


def composer_harmonise(img: Image.Image, alpha: np.ndarray, r: Reglages) -> Image.Image:
    """Décor conservé mais atténué : flou, désaturation, éclaircissement."""
    base = corriger(img)
    decor = base.filter(ImageFilter.GaussianBlur(radius=max(base.width / 110, 6)))
    decor = ImageEnhance.Color(decor).enhance(0.45)
    decor = ImageEnhance.Brightness(decor).enhance(1.12)

    masque = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), mode="L")
    masque = masque.filter(ImageFilter.GaussianBlur(radius=2.5))
    return recadrer(Image.composite(base, decor, masque), r)


# --------------------------------------------------------------------------
# Cadrage
# --------------------------------------------------------------------------

def recadrer(img: Image.Image, r: Reglages) -> Image.Image:
    """Recadre au ratio cible en rognant le moins possible, puis redimensionne."""
    ratio_cible = r.largeur / r.hauteur
    ratio = img.width / img.height
    if ratio > ratio_cible:
        largeur = int(img.height * ratio_cible)
        marge = (img.width - largeur) // 2
        img = img.crop((marge, 0, marge + largeur, img.height))
    elif ratio < ratio_cible:
        hauteur = int(img.width / ratio_cible)
        # On rogne davantage en haut : le sujet est rarement dans le ciel.
        marge = int((img.height - hauteur) * 0.6)
        img = img.crop((0, marge, img.width, marge + hauteur))
    return img.resize((r.largeur, r.hauteur), Image.LANCZOS)


# --------------------------------------------------------------------------
# Traitement par lot
# --------------------------------------------------------------------------

def fichiers_entrants(chemin: Path) -> list[Path]:
    if chemin.is_file():
        return [chemin]
    return sorted(p for p in chemin.rglob("*") if p.suffix.lower() in EXTENSIONS)


def traiter(source: Path, destination: Path, mode: str, r: Reglages, session) -> str:
    img = ImageOps.exif_transpose(Image.open(source)).convert("RGB")

    choisi = mode
    alpha = None
    if mode in {"auto", "studio", "harmonise"}:
        alpha = masque_du_sujet(img, session)
        if mode == "auto":
            choisi = type_de_photo(alpha)

    if choisi == "studio" and alpha is not None:
        sortie = composer_studio(img, alpha, r)
    elif choisi == "harmonise" and alpha is not None:
        sortie = composer_harmonise(img, alpha, r)
    else:
        choisi = "correction"
        sortie = recadrer(corriger(img), r)

    destination.parent.mkdir(parents=True, exist_ok=True)
    sortie.save(destination, "JPEG", quality=r.qualite, subsampling=1, optimize=True)
    return choisi


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("source", type=Path, help="fichier ou dossier de photos")
    parseur.add_argument("-o", "--sortie", type=Path, required=True, help="dossier de destination")
    parseur.add_argument(
        "--mode",
        choices=["auto", "studio", "harmonise", "correction"],
        default="auto",
        help="traitement imposé (défaut : auto, décidé photo par photo)",
    )
    parseur.add_argument("--largeur", type=int, default=1600)
    parseur.add_argument("--hauteur", type=int, default=1200)
    parseur.add_argument("--qualite", type=int, default=92)
    args = parseur.parse_args(argv)

    r = Reglages(largeur=args.largeur, hauteur=args.hauteur, qualite=args.qualite)

    sources = fichiers_entrants(args.source)
    if not sources:
        print(f"Aucune photo trouvée dans {args.source}", file=sys.stderr)
        return 1

    session = None
    if args.mode != "correction":
        from rembg import new_session

        session = new_session(r.modele)

    for index, source in enumerate(sources, start=1):
        destination = args.sortie / f"{index:02d}_{source.stem}.jpg"
        choisi = traiter(source, destination, args.mode, r, session)
        print(f"{source.name:40s} -> {destination.name:40s} [{choisi}]")

    print(f"\n{len(sources)} photo(s) traitée(s) dans {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
