#!/usr/bin/env python3
"""Fabrique les fixtures de plans qui ne se RELISENT pas, et ne sont donc pas commitées.

Les DXF ASCII de `fixtures/plans/` sont du texte : commités, ils se relisent
en quelques lignes. Trois fichiers échappent à cette règle, pour deux raisons
différentes.

**Deux sont des octets** — un DXF binaire et un faux DWG — et un binaire
commité devient un bloc que personne n'ouvre.

**Le troisième est du texte, mais 3 300 lignes de texte.** `mur_cote.dxf`
porte une vraie cotation, et une cotation a besoin de son bloc géométrique :
sans lui, l'audit la retire au rechargement et l'espace modèle revient vide.
Ce bloc fait trois mille lignes qu'aucun relecteur ne lira. Le code qui le
fabrique, lui, tient en dix lignes et dit exactement ce que le fichier
contient — ce qu'un diff de trois mille lignes ne dirait pas.

Dans les trois cas, c'est le code qui est la documentation du fichier.

    python3 scripts/fabriquer_plans_de_test.py

Aucun des deux n'est un plan réel, et `faux.dwg` n'est même pas un DWG
valide : il porte l'en-tête d'un DWG, puis des octets quelconques. C'est
exactement ce qu'il faut pour prouver que le refus intervient **sur
l'en-tête**, avant toute tentative de lecture — un vrai DWG prouverait moins,
puisqu'on ne saurait plus si le refus vient de l'en-tête ou de la suite.
"""

from __future__ import annotations

import json
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


#: Ce que la ligne de commande doit AVOIR produit pour rendre 0.
ATTENDUS: tuple[str, ...] = (
    "binaire.dxf",
    "faux.dwg",
    "mur_cote.dxf",
    "plan_cote.pdf",
    "plan_batiment.pdf",
    "plan_batiment.json",
)


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

    cote = _mur_cote()
    if cote is not None:
        ecrits.append(cote)

    ecrits.append(_plan_pdf())
    ecrits.append(_plan_de_batiment())

    return ecrits


def _plan_pdf() -> Path:
    """Un plan PDF de deux pages, pour le parcours de mesure à l'écran.

    **Non commité pour la même raison que les deux fixtures binaires** : un PDF
    est des octets, et un binaire commité devient un bloc que personne
    n'ouvre. Ici le code qui l'écrit EST sa documentation — deux pages de
    300 × 220 points, neuf textes sur la première, cinq sur la seconde, chacun
    à une position connue.

    Aucune bibliothèque : `fabriquer_pdf_de_test` écrit les octets à la main,
    table d'offsets comprise. C'est ce qui lui permet de tourner dans l'image
    avant qu'on sache si la bibliothèque de lecture y est installée — et c'est
    la même fabrique que les tests du dépôt, pour qu'il n'y ait pas deux
    vérités sur ce que contient la fixture.
    """
    sys.path.insert(0, str(RACINE / "scripts"))
    import fabriquer_pdf_de_test

    chemin = SORTIE / "plan_cote.pdf"
    chemin.write_bytes(fabriquer_pdf_de_test.plan_de_deux_pages())
    return chemin


def _plan_de_batiment() -> Path:
    """Un plan portant une GÉOMÉTRIE dont les dimensions sont connues d'avance.

    La différence avec `plan_cote.pdf`, et c'est toute la raison d'être de ce
    second fichier : l'autre porte des textes isolés, sur lesquels un parcours
    peut éprouver les gestes mais rien mesurer de vérifiable. Celui-ci porte une
    ligne de cote à extrémités pointables et une pièce fermée, dont la longueur
    et l'aire sont posées dans `fabriquer_pdf_de_test` en points de papier et
    converties par une échelle déclarée — jamais lues sur le dessin.

    Un parcours peut donc calibrer sur la cote, mesurer la façade et le contour
    de la pièce, et **comparer à une vérité qui ne vient pas de Metreo**.
    """
    sys.path.insert(0, str(RACINE / "scripts"))
    import fabriquer_pdf_de_test

    chemin = SORTIE / "plan_batiment.pdf"
    chemin.write_bytes(fabriquer_pdf_de_test.plan_de_batiment())

    # La VÉRITÉ du dessin, écrite à côté de lui.
    #
    # Elle est posée dans `fabriquer_pdf_de_test` en points de papier ; un
    # parcours de navigateur, lui, raisonne en fractions d'écran et ne peut pas
    # importer du Python. Recopier les chiffres dans le TypeScript ferait deux
    # sources, et la seconde finirait par mentir. Ce fichier est donc la seule
    # passerelle, et il est fabriqué par celui qui dessine.
    verite = {
        "millimetres_par_point": fabriquer_pdf_de_test.MILLIMETRES_PAR_POINT,
        "page": [fabriquer_pdf_de_test.LARGEUR_DU_PLAN, fabriquer_pdf_de_test.HAUTEUR_DU_PLAN],
        "cote": {
            "premier": [fabriquer_pdf_de_test.COTE_X0, fabriquer_pdf_de_test.COTE_Y],
            "second": [fabriquer_pdf_de_test.COTE_X1, fabriquer_pdf_de_test.COTE_Y],
            "longueur_mm": fabriquer_pdf_de_test.LONGUEUR_DE_LA_COTE_EN_MM,
        },
        "piece": {
            "coins": [
                [fabriquer_pdf_de_test.PIECE_X0, fabriquer_pdf_de_test.PIECE_Y0],
                [fabriquer_pdf_de_test.PIECE_X1, fabriquer_pdf_de_test.PIECE_Y0],
                [fabriquer_pdf_de_test.PIECE_X1, fabriquer_pdf_de_test.PIECE_Y1],
                [fabriquer_pdf_de_test.PIECE_X0, fabriquer_pdf_de_test.PIECE_Y1],
            ],
            "largeur_mm": fabriquer_pdf_de_test.LARGEUR_DE_LA_PIECE_EN_MM,
            "profondeur_mm": fabriquer_pdf_de_test.PROFONDEUR_DE_LA_PIECE_EN_MM,
            "surface_m2": fabriquer_pdf_de_test.SURFACE_DE_LA_PIECE_EN_M2,
        },
    }
    (SORTIE / "plan_batiment.json").write_text(
        json.dumps(verite, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return chemin


def _mur_cote() -> Path | None:
    """Un mur de 5 m, coté, dans un fichier qu'un parcours complet peut déposer.

    Pourquoi il ne peut pas être `mur_simple.dxf` : celle-ci porte deux LIGNES
    et **aucune cotation**. Le plan se lit — unité, calques, entités — mais il
    ne propose RIEN à mesurer. Un parcours de bout en bout qui cherche une
    mesure à corriger n'y trouve donc rien, et c'est exactement ce qui a fait
    échouer le premier essai dans un navigateur.

    `render()` n'est pas décoratif : sans lui, la cotation n'a pas de bloc
    géométrique, l'audit la retire au rechargement, et l'espace modèle revient
    VIDE. Dix tests ont déjà échoué sur un dépaquetage de liste vide, pour
    cette seule ligne manquante.

    Rend `None` si ezdxf n'est pas installé — l'extra « plans » est optionnel,
    et les deux fixtures binaires, elles, n'en ont pas besoin.
    """
    try:
        import ezdxf
    except ModuleNotFoundError:
        return None

    # R2000 et non R12 : R12 n'exporte PAS `$INSUNITS`, et un plan sans unité
    # n'est pas mesurable. Vérifié — ezdxf le dit à l'enregistrement.
    document = ezdxf.new("R2000", setup=False)
    document.header["$INSUNITS"] = 4  # millimètres
    document.layers.add("MURS")
    document.layers.add("COTATIONS")
    espace = document.modelspace()
    espace.add_line((0, 0), (5000, 0), dxfattribs={"layer": "MURS"})
    espace.add_line((0, 0), (0, 2500), dxfattribs={"layer": "MURS"})
    cotation = espace.add_linear_dim(
        base=(0, -800), p1=(0, 0), p2=(5000, 0), dxfattribs={"layer": "COTATIONS"}
    )
    cotation.render()

    chemin = SORTIE / "mur_cote.dxf"
    document.saveas(chemin)
    return chemin


def main() -> int:
    """Fabrique tout, et ÉCHOUE si quelque chose manque.

    La différence avec `fabriquer()` est voulue. Appelée depuis une suite de
    tests, la fonction rend ce qu'elle a pu écrire : les neuf tests de
    détection n'ont pas besoin d'ezdxf, et les faire échouer faute d'un extra
    optionnel serait punir le mauvais appelant.

    Appelée en ligne de commande — ce que fait le banc Playwright — elle doit
    au contraire crier. Mesuré : sans ce refus, le banc écrivait deux fichiers
    sur trois, et le parcours de plan échouait DIX-SEPT scénarios plus tard
    sur « no such file or directory », loin de la cause. Un fabricant
    silencieux déplace l'erreur au lieu de la dire.
    """
    ecrits = fabriquer()
    for chemin in ecrits:
        print(f"{chemin.relative_to(RACINE)} — {chemin.stat().st_size} octets")

    manquants = [nom for nom in ATTENDUS if not (SORTIE / nom).exists()]
    if manquants:
        print(
            "NON FABRIQUÉ : "
            + ", ".join(manquants)
            + "\n  L'extra « plans » est probablement absent : une cotation se "
            "fabrique avec ezdxf.\n  pip install './apps/api[plans]'",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
