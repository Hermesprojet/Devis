#!/usr/bin/env python3
"""Mesure ce qu'une tuile montre VRAIMENT, en interceptant l'appel à PDFium.

    python scripts/mesures/mesurer_l_etendue_d_une_tuile.py <dossier>

**Pourquoi par interception, et pourquoi c'est le point entier de ce script.**

Une tuile n'affiche pas la zone demandée : elle l'élargit d'une marge, puis
borne cette marge par le gain que la tuile doit encore apporter sur l'aperçu.
La première mesure de sa finesse RECALCULAIT cette marge au lieu de l'observer,
et retrouvait donc exactement le chiffre qu'elle voulait trouver — pendant que
le code, lui, rendait 85 % de la page sur 512 pixels pour une demande de 5 %,
soit plus grossier que l'aperçu. Une vérification qui réimplémente ce qu'elle
vérifie ne vérifie rien.

Ce script remplace donc `PdfPage.render` par un observateur, lit le `crop` et
le `scale` que `rendre_une_zone` lui passe, et n'en déduit rien d'autre.

**Les plans.** Le dossier passé en argument contient des PDF fournis par
l'exploitant. Aucun plan n'est versionné : un plan de chantier appartient à un
client.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

#: Les deux cas qui comptent, et ils ne se comportent pas pareil.
#:
#: La boîte d'un texte fait quelques millièmes de page : la marge de huit fois
#: sa hauteur y est l'intention. La loupe de l'écran demande 5 % — c'est le cas
#: que la marge faisait dérailler.
CAS = (
    ("loupe 5 %", (0.40, 0.40, 0.45, 0.45)),
    ("boîte de texte 0,3 %", (0.400, 0.400, 0.430, 0.403)),
)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print(__doc__)
        return 2
    prive = Path(arguments[0])
    if not prive.is_dir():
        print(f"« {prive} » n'est pas un dossier.", file=sys.stderr)
        return 2

    import pypdfium2 as pdfium

    from metreo_api.services import rendu_pdf

    observe: dict[str, Any] = {}
    rendu_original = pdfium.PdfPage.render

    def rendu_observe(self: Any, *args: Any, **kwargs: Any) -> Any:
        observe["scale"] = kwargs.get("scale")
        observe["crop"] = kwargs.get("crop")
        observe["taille"] = self.get_size()
        return rendu_original(self, *args, **kwargs)

    pdfium.PdfPage.render = rendu_observe  # type: ignore[method-assign]

    for libelle, zone in CAS:
        print(f"### {libelle} : zone demandée {zone}")
        for chemin in sorted(prive.glob("*.pdf")):
            tuile = rendu_pdf.rendre_une_zone(chemin, page=1, zone=zone)
            largeur_pt, hauteur_pt = observe["taille"]
            gauche, bas, droite, haut = observe["crop"]
            etendue_x = largeur_pt - gauche - droite
            etendue_y = hauteur_pt - bas - haut
            grand = max(etendue_x, etendue_y)
            mm_par_px = (grand / 72 * 25.4) / max(tuile.largeur, tuile.hauteur)
            print(
                f"    {chemin.name:20} rendue {etendue_x / largeur_pt * 100:5.1f}% "
                f"x {etendue_y / hauteur_pt * 100:5.1f}% de la page, "
                f"facteur {observe['scale']:6.3f}, "
                f"tuile {tuile.largeur}x{tuile.hauteur}, {mm_par_px:6.3f} mm/px, "
                f"zone {tuile.hauteur_de_la_zone_px or 0:5.1f} px"
            )
        print()
    return 0


if __name__ == "__main__":  # pragma: no cover - outil de mesure
    raise SystemExit(main())
