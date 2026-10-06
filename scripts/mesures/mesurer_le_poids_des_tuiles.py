#!/usr/bin/env python3
"""Mesure ce que pèse une tuile, sur une GRILLE de zones et non sur une seule.

    python scripts/mesures/mesurer_le_poids_des_tuiles.py <dossier>

**Pourquoi une grille.** Le premier chiffre écrit dans les docstrings de
`services/tuiles.py` — « 8 à 30 Ko » — venait d'une seule zone par plan,
centrée sur un fragment de texte, donc sur du blanc. Une zone dense n'a aucune
raison de peser la même chose, et c'est ce poids-là qui décide du plafond du
cache. Mesuré sur vingt-cinq zones par plan, le vrai intervalle est de 26 à
246 Ko, de médiane 92 Ko — soit, à cinq cents tuiles, 45 Mo par révision au
poids médian et 120 Mo au pire. D'où le plafond en OCTETS de l'ADR 0008, que le
plafond en nombre seul n'aurait pas borné.

**Les plans.** Le dossier passé en argument contient des PDF fournis par
l'exploitant. Aucun plan n'est versionné : un plan de chantier appartient à un
client.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

#: Le nombre de zones par côté. Vingt-cinq zones par plan suffisent à montrer
#: l'écart entre une marge et un détail dense, et tiennent en une minute.
PAS = 5

#: La taille d'une zone, en fraction de page. La MÊME que la loupe de l'écran
#: (`TAILLE_DE_LA_LOUPE` dans `LecturePdf.tsx`) : mesurer autre chose que ce
#: que l'écran demande ne dirait rien du volume réellement consommé.
COTE = 0.05


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print(__doc__)
        return 2
    prive = Path(arguments[0])
    if not prive.is_dir():
        print(f"« {prive} » n'est pas un dossier.", file=sys.stderr)
        return 2

    from metreo_api.services import rendu_pdf
    from metreo_api.services.tuiles import PLAFOND_DE_TUILES_PAR_REVISION

    print(f"{'plan':20} {'zones':>6} {'min':>8} {'médian':>8} {'moyen':>8} {'max':>8} {'total':>9}")
    tous: list[float] = []
    for chemin in sorted(prive.glob("*.pdf")):
        poids: list[float] = []
        for i in range(PAS):
            for j in range(PAS):
                x = 0.05 + i * (0.90 - COTE) / (PAS - 1)
                y = 0.05 + j * (0.90 - COTE) / (PAS - 1)
                tuile = rendu_pdf.rendre_une_zone(chemin, page=1, zone=(x, y, x + COTE, y + COTE))
                poids.append(len(tuile.png) / 1024)
        tous += poids
        print(
            f"{chemin.name:20} {len(poids):6} {min(poids):7.1f}K "
            f"{statistics.median(poids):7.1f}K {statistics.fmean(poids):7.1f}K "
            f"{max(poids):7.1f}K {sum(poids) / 1024:8.1f}Mo"
        )
    if not tous:
        print("Aucun PDF dans ce dossier.", file=sys.stderr)
        return 1
    median = statistics.median(tous)
    print()
    print(
        f"  -> sur {len(tous)} tuiles : min {min(tous):.1f} Ko, "
        f"médian {median:.1f} Ko, max {max(tous):.1f} Ko"
    )
    for libelle, valeur in (("MÉDIAN", median), ("MAXIMAL", max(tous))):
        total = PLAFOND_DE_TUILES_PAR_REVISION * valeur / 1024
        print(
            f"  -> {PLAFOND_DE_TUILES_PAR_REVISION} tuiles au poids {libelle:8} "
            f"= {total:.0f} Mo par révision"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - outil de mesure
    raise SystemExit(main())
