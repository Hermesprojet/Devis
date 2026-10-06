#!/usr/bin/env python3
"""Fabrique les fixtures de plans BINAIRES, qui ne sont pas commitées.

Les DXF ASCII de `fixtures/plans/` sont du texte : commités, ils se relisent.
Les deux fichiers ci-dessous ne se relisent pas — ce sont des octets — et un
binaire commité devient un bloc que personne n'ouvre. On les fabrique, et le
code qui les fabrique dit ce qu'ils contiennent, ce qu'une capture d'écran de
leur contenu hexadécimal ne dirait pas.

    python3 scripts/fabriquer_plans_de_test.py

Aucun des deux n'est un plan réel, et `faux.dwg` n'est même pas un DWG
valide : il porte l'en-tête d'un DWG, puis des octets quelconques. C'est
exactement ce qu'il faut pour prouver que le refus intervient **sur
l'en-tête**, avant toute tentative de lecture — un vrai DWG prouverait moins,
puisqu'on ne saurait plus si le refus vient de l'en-tête ou de la suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
SORTIE = RACINE / "fixtures" / "plans"

#: La sentinelle que les logiciels de dessin posent en tête d'un DXF binaire.
#: Vingt-deux octets : la phrase, puis CR LF SUB NUL.
SENTINELLE_DXF_BINAIRE = b"AutoCAD Binary DXF\r\n\x1a\x00"

#: Un en-tête DWG : « AC » suivi de quatre chiffres de version. AC1032 est
#: celle d'AutoCAD 2018. La valeur exacte importe peu — ce qui compte est la
#: forme, puisque c'est elle que la détection reconnaît.
ENTETE_DWG = b"AC1032"

#: Des octets qui ne veulent rien dire, mais qui sont STABLES : un
#: `os.urandom` rendrait la fixture différente à chaque fabrication, et un
#: échec ne serait plus reproductible.
REMPLISSAGE = bytes((i * 7 + 13) % 256 for i in range(256))


def fabriquer() -> list[Path]:
    SORTIE.mkdir(parents=True, exist_ok=True)
    ecrits: list[Path] = []

    binaire = SORTIE / "binaire.dxf"
    # Après la sentinelle, un DXF binaire porte des paires codées. On n'en met
    # aucune : ce fichier sert à la DÉTECTION, pas à la lecture.
    binaire.write_bytes(SENTINELLE_DXF_BINAIRE + REMPLISSAGE)
    ecrits.append(binaire)

    faux_dwg = SORTIE / "faux.dwg"
    faux_dwg.write_bytes(ENTETE_DWG + REMPLISSAGE)
    ecrits.append(faux_dwg)

    return ecrits


def main() -> int:
    for chemin in fabriquer():
        print(f"{chemin.relative_to(RACINE)} — {chemin.stat().st_size} octets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
