#!/usr/bin/env python3
"""Fabrique les classeurs XLSX de `fixtures/imports/` depuis les CSV voisins.

**Pourquoi fabriquer plutôt que commiter.** Un `.xlsx` est une archive binaire :
commité, il devient un bloc opaque que personne ne relit et que rien ne
protège d'une dérive silencieuse. Fabriqué par ce script, on voit exactement
quelles cellules et quels TYPES le test emploie, et le dépôt reste sans octets
qu'on ne sache expliquer. C'est la même règle que le PNG du parcours navigateur.

**Pourquoi des types natifs.** Le CSV porte « 41,50 » et « 01/01/2026 » : du
texte à la française. Le classeur, lui, porte un nombre et une date — ce qu'un
tableur écrit réellement. Recopier les chaînes du CSV dans les cellules
rendrait l'équivalence triviale et ne prouverait rien : c'est précisément parce
que les deux fichiers portent la même valeur sous deux formes DIFFÉRENTES que
leur convergence après normalisation vaut démonstration.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "fixtures" / "imports"

#: Colonnes que le classeur porte en nombre plutôt qu'en texte.
NOMBRES = {"prix_unitaire", "quantite_min", "delai", "confiance"}
#: Colonnes que le classeur porte en date.
DATES = {"valide_du", "valide_au"}

FORMATS_DE_DATE = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def _valeur_de_cellule(colonne: str, texte: str) -> object:
    """Traduit une cellule CSV vers ce qu'un tableur y aurait mis.

    Une valeur que ce script ne sait pas convertir reste du TEXTE, telle
    quelle : c'est le cas des lignes volontairement fautives, dont l'unité ou
    le prix sont invalides. Les convertir de force les réparerait, et le
    scénario d'acceptation n° 2 cesserait d'avoir des erreurs à montrer.
    """
    brut = (texte or "").strip()
    if not brut:
        return None
    if colonne in NOMBRES:
        try:
            return float(brut.replace(",", "."))
        except ValueError:
            return brut
    if colonne in DATES:
        for format_ in FORMATS_DE_DATE:
            try:
                return datetime.strptime(brut, format_).date()
            except ValueError:
                continue
        return brut
    return brut


def fabriquer(source: Path, cible: Path, *, feuille: str = "Prix") -> tuple[int, int]:
    """Écrit le classeur et rend (lignes, colonnes) pour que l'appelant le dise."""
    from openpyxl import Workbook

    with source.open(encoding="utf-8") as fichier:
        lecteur = csv.reader(fichier, delimiter=";")
        lignes = [ligne for ligne in lecteur if any(cellule.strip() for cellule in ligne)]

    if not lignes:
        raise SystemExit(f"{source} est vide : rien à fabriquer.")

    entetes = lignes[0]
    classeur = Workbook()
    onglet = classeur.active
    onglet.title = feuille
    onglet.append(entetes)
    for ligne in lignes[1:]:
        onglet.append(
            [
                _valeur_de_cellule(entetes[index] if index < len(entetes) else "", cellule)
                for index, cellule in enumerate(ligne)
            ]
        )

    cible.parent.mkdir(parents=True, exist_ok=True)
    classeur.save(cible)
    return len(lignes) - 1, len(entetes)


#: Chaque classeur, la source dont il vient, et la feuille qu'il porte.
CLASSEURS = (
    ("modele_import_prix.csv", "modele_import_prix.xlsx", "Prix"),
    ("prix_valides_5_lignes.csv", "prix_valides_5_lignes.xlsx", "Prix"),
    ("prix_5_valides_2_erreurs.csv", "prix_5_valides_2_erreurs.xlsx", "Prix"),
)


def main() -> int:
    for nom_csv, nom_xlsx, feuille in CLASSEURS:
        source = FIXTURES / nom_csv
        if not source.exists():
            print(f"absent : {source}", file=sys.stderr)
            return 1
        lignes, colonnes = fabriquer(source, FIXTURES / nom_xlsx, feuille=feuille)
        print(f"{nom_xlsx} : {lignes} ligne(s), {colonnes} colonne(s), feuille « {feuille} »")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
