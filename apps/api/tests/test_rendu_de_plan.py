"""L'image d'un plan : ce qu'elle porte, ce qu'elle pèse, et ce qu'on refuse.

Chaque test FABRIQUE son DXF dans `tmp_path`. Aucun plan réel n'est commité,
et aucun octet n'est écrit dans l'arbre du dépôt : le sujet de ces cas est
toujours trois lignes de géométrie, pas deux cents lignes de DXF.

**Trois de ces cas ont été trouvés PAR L'EXÉCUTION**, et non en relisant la
documentation d'ezdxf. Ils sont signalés comme tels, avec la mesure qui les a
révélés :

* un plan dont toutes les entités sont sur un calque éteint faisait lever
  `ValueError: empty bounding box` — donc un 500 nu au lieu d'un refus nommé ;
* une SEULE entité de hachure produisait 17,94 Mo de SVG en 10,8 s ;
* `ezdxf` dimensionnait la page sur l'étendue du dessin, ce qui a donné
  `width="383696mm"` sur un plan réel — quatre cents mètres de large.

Le dernier test du fichier, lui, ne garde pas contre un défaut constaté mais
contre un défaut POSSIBLE : l'axe vertical s'inverse entre le DXF et l'image,
et une inversion oubliée surlignerait le bas du plan en croyant montrer le
haut, sans qu'aucune erreur ne soit levée.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from metreo_api.services import lecture_dxf, rendu_de_plan

ezdxf = pytest.importorskip(
    "ezdxf",
    reason="l'extra « plans » n'est pas installé : pip install './apps/api[plans]'",
)

#: La balise racine du SVG, jusqu'à son premier `>`. Les attributs physiques
#: ne doivent être cherchés QUE là : le fond du dessin est un `<rect>` qui
#: porte légitimement `width` et `height`, et un contrôle sur tout le document
#: le prendrait pour la page.
_BALISE_RACINE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


def _document(insunits: int | None = 4):
    """Un document neuf, en millimètres par défaut."""
    doc = ezdxf.new("R2013", setup=True)
    if insunits is None:
        del doc.header["$INSUNITS"]
    else:
        doc.header["$INSUNITS"] = insunits
    return doc


def _mur_simple(chemin: Path) -> Path:
    """Deux murs et un poteau : le plus petit dessin qui ait une étendue."""
    doc = _document()
    msp = doc.modelspace()
    msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "MURS"})
    msp.add_line((5000, 0), (5000, 3000), dxfattribs={"layer": "MURS"})
    msp.add_circle((2500, 1500), 200, dxfattribs={"layer": "POTEAUX"})
    doc.saveas(chemin)
    return chemin


def _balise_racine(svg: str) -> str:
    trouvee = _BALISE_RACINE.search(svg)
    assert trouvee is not None, f"le rendu ne commence pas par une balise <svg> : {svg[:120]!r}"
    return trouvee.group(0)


# ---------------------------------------------------------------------------
# Ce que l'image porte
# ---------------------------------------------------------------------------


def test_a_drawing_renders_to_an_svg_that_carries_no_executable_construction(
    tmp_path: Path,
) -> None:
    """**Un SVG est un document, pas une image** : il peut porter du script.

    Servi par une balise `<img>` il est inerte, mais atteint directement par
    son URL — dans l'origine de l'application, là où vit le jeton de session —
    il exécuterait ce qu'il contient. Le rendu d'ezdxf 1.4.4 n'en produit
    aucun, vérifié sur deux plans réels. Ce test fixe que l'absence est
    CONSTATÉE et non supposée : une version d'ezdxf qui changerait d'avis le
    ferait échouer ici, avant que le fichier n'atteigne un navigateur.
    """
    rendu = rendu_de_plan.rendre(_mur_simple(tmp_path / "mur.dxf"))
    bas = rendu.svg.lower()

    for motif in ("<script", "<foreignobject", "<!entity", "<iframe", "javascript:"):
        assert motif not in bas, f"le rendu porte {motif}"
    # Aucune requête sortante : ni image externe, ni lien, ni référence.
    for motif in ("<image", "xlink:href", "href="):
        assert motif not in bas, f"le rendu porte {motif}"
    assert rendu_de_plan.MOTIF_GESTIONNAIRE.search(rendu.svg) is None, (
        "le rendu porte un gestionnaire d'événement"
    )
    assert rendu.entites_rendues == 3


def test_the_rendered_svg_keeps_its_viewbox_and_carries_a_size_in_pixels(
    tmp_path: Path,
) -> None:
    """**Les deux erreurs sont OPPOSÉES, et ce test les ferme toutes les deux.**

    ezdxf dimensionne la page sur l'étendue du dessin, en millimètres. Mesuré
    sur un plan réel : `width="383696mm"` — le navigateur réserverait quatre
    cents mètres de largeur avant la moindre mise en page.

    Les RETIRER produit l'autre défaut, celui qu'un parcours de bout en bout a
    attrapé : un SVG sans dimension intrinsèque, chargé dans une balise `<img>`
    dont la largeur vaut `auto`, n'a plus de taille du tout. L'image est bien
    dans la page, avec sa source — et elle est invisible.

    La promesse est donc : le `viewBox` reste INTACT, parce que c'est lui qui
    fait tomber au bon endroit un surlignage posé en [0,1] ; et les dimensions
    sont des pixels, sans unité physique, dans le rapport de forme du
    `viewBox`. Une assertion qui exigerait seulement la présence de `width`
    laisserait revenir les millimètres ; une qui exigerait son absence
    rendrait l'image invisible.

    Le contrôle porte sur la BALISE RACINE seule : le fond du dessin est un
    `<rect width=… height=…>` parfaitement légitime, qu'un contrôle sur tout
    le document confondrait avec la page.
    """
    rendu = rendu_de_plan.rendre(_mur_simple(tmp_path / "mur.dxf"))
    racine = _balise_racine(rendu.svg)

    cadre = re.search(r'\sviewBox="([^"]+)"', racine)
    assert cadre is not None, racine
    etendue_x, etendue_y = (float(v) for v in cadre.group(1).split()[2:4])
    # Le repère du dessin, et non une page : 5000 × 3000 unités, à la largeur
    # de trait près. Le `viewBox` n'a pas été réécrit.
    assert etendue_x > etendue_y > 0

    dimensions = dict(re.findall(r'\s(width|height)="([^"]*)"', racine))
    assert set(dimensions) == {"width", "height"}, (
        f"une image sans dimension intrinsèque est invisible dans un <img> : {racine}"
    )
    for nom, valeur in dimensions.items():
        assert valeur.isdigit(), f'{nom}="{valeur}" porte une unité physique'
        assert 0 < int(valeur) <= rendu_de_plan.COTE_AFFICHEE, f"{nom}={valeur}"
    assert "mm" not in racine, racine

    # Le rapport de forme est celui du dessin : une image étirée tromperait
    # l'œil sur les proportions d'un ouvrage.
    attendu = etendue_y / etendue_x
    obtenu = int(dimensions["height"]) / int(dimensions["width"])
    assert obtenu == pytest.approx(attendu, abs=0.01), (dimensions, cadre.group(1))

    # Le reste du document garde ses dimensions : la retouche est chirurgicale
    # et ne doit pas avoir emporté le fond.
    assert "<rect" in rendu.svg


# ---------------------------------------------------------------------------
# Ce qui est refusé, et nommé
# ---------------------------------------------------------------------------


def test_a_drawing_whose_entities_all_sit_on_a_hidden_layer_is_refused_by_name(
    tmp_path: Path,
) -> None:
    """**Défaut réel, reproduit avant correctif : un 500 nu.**

    Un plan dont toutes les entités sont sur un calque éteint est un cas
    parfaitement ORDINAIRE — un fond de plan masqué, une variante gelée. Les
    deux gardes qui précèdent ne le voient ni l'une ni l'autre :

    * le compte d'entités n'est pas nul — il y a bien une entité ;
    * `bbox.extents` en rend une étendue, parce qu'il compte l'entité
      INVISIBLE que le frontend de dessin, lui, saute.

    Le backend n'avait donc rien enregistré, et `get_string` levait
    `ValueError: empty bounding box` : une exception nue, c'est-à-dire un 500
    de l'API au lieu d'un refus que l'écran peut présenter. Les deux
    assertions sur l'étendue ci-dessous ne sont pas décoratives : elles
    prouvent que les deux gardes existantes laissent bien passer ce cas, donc
    que la troisième est nécessaire.
    """
    doc = _document()
    calque = doc.layers.add("FOND-MASQUE")
    calque.off()
    doc.modelspace().add_line((0, 0), (10000, 8000), dxfattribs={"layer": "FOND-MASQUE"})
    chemin = tmp_path / "calque-eteint.dxf"
    doc.saveas(chemin)

    constat = lecture_dxf.lire(chemin, situer=True)
    assert constat.entites == {"LINE": 1}, "la première garde — le compte — ne mord pas"
    assert constat.etendue is not None, "la deuxième garde — l'étendue — ne mord pas non plus"

    with pytest.raises(rendu_de_plan.RenduRefuse) as refus:
        rendu_de_plan.rendre(chemin)
    assert refus.value.code == "plan_sans_trace"
    # Le message dit ce qui reste possible : le fichier n'est pas perdu.
    assert "mesurable" in refus.value.message


def test_a_single_hatch_cannot_amplify_the_drawing_into_megabytes(tmp_path: Path) -> None:
    """**Mesure réelle : une seule hachure, 17,94 Mo de SVG en 10,8 s.**

    Ni le plafond d'entités ni un plafond de tracés ne bornent cela : l'entité
    est UNE, et c'est son motif de remplissage qui explose. La seule borne qui
    attaque l'amplification à sa racine est la politique de hachure, réglée
    sur `SHOW_OUTLINE` : la même hachure rend alors 0,00 Mo en 0,0 s, et un
    plan d'exécution réel passe de 4,49 à 4,38 Mo — 2,4 % de perte.

    **Ce que cette politique coûte, et il faut le savoir** : un plan qui
    distingue deux matériaux par deux MOTIFS de hachure différents ne les
    distinguera plus à l'écran — les deux n'auront que leur contour. Le
    fichier, lui, garde tout : la distinction reste lisible en téléchargeant
    l'original, et `HatchPolicy.SHOW_APPROXIMATE_PATTERN` est l'intermédiaire
    à éprouver si le cas se présente.

    La borne est franche et non proportionnelle : cent mille octets, soit deux
    cents fois le rendu observé et cent quatre-vingts fois moins que le rendu
    avec remplissage. Un test qui comparerait deux politiques entre elles
    passerait au vert le jour où les deux seraient aussi lourdes.
    """
    doc = _document()
    hachure = doc.modelspace().add_hatch(color=2, dxfattribs={"layer": "DALLES"})
    hachure.set_pattern_fill("ANSI31", scale=0.05)
    hachure.paths.add_polyline_path(
        [(0, 0), (60000, 0), (60000, 60000), (0, 60000)], is_closed=True
    )
    chemin = tmp_path / "hachure.dxf"
    doc.saveas(chemin)

    rendu = rendu_de_plan.rendre(chemin)

    assert rendu.entites_rendues == 1, "une seule entité, et c'est tout le propos"
    octets = len(rendu.svg.encode("utf-8"))
    assert octets < 100_000, (
        f"la hachure a produit {octets} octets : la politique de contour n'est plus appliquée"
    )
    # Le contour EST dessiné : une image vide passerait aussi la borne.
    assert "<path" in rendu.svg


def test_a_file_that_is_not_a_drawing_is_refused_without_raising(tmp_path: Path) -> None:
    """Refusé par un code stable, et non par une exception de bibliothèque.

    « Sans lever » veut dire ici : sans laisser remonter une erreur d'ezdxf.
    `RenduRefuse` porte un code que l'API traduit ; une `ValueError` nue
    deviendrait un 500, et l'utilisateur n'apprendrait rien de son fichier.
    """
    chemin = tmp_path / "pas-un-plan.dxf"
    chemin.write_text("ceci n'est pas un plan\n", encoding="utf-8")

    with pytest.raises(rendu_de_plan.RenduRefuse) as refus:
        rendu_de_plan.rendre(chemin)
    assert refus.value.code in ("fichier_invalide", "fichier_illisible")


def test_a_block_reference_cycle_is_refused_before_any_drawing(tmp_path: Path) -> None:
    """ezdxf SIGNALE le cycle (code d'audit 104) sans le casser.

    Ni `explode()` ni `virtual_entities()` ne portent de garde de profondeur :
    dessiner un tel fichier est une récursion sans fin. L'audit passe donc
    AVANT le rendu, comme il passe avant la lecture — même ordre, même raison.
    """
    doc = _document()
    premier = doc.blocks.new("PREMIER")
    second = doc.blocks.new("SECOND")
    premier.add_blockref("SECOND", (0, 0))
    second.add_blockref("PREMIER", (0, 0))
    doc.modelspace().add_blockref("PREMIER", (0, 0))
    chemin = tmp_path / "cycle.dxf"
    doc.saveas(chemin)

    with pytest.raises(rendu_de_plan.RenduRefuse) as refus:
        rendu_de_plan.rendre(chemin)
    assert refus.value.code == "cycle_de_blocs"


def test_an_empty_model_space_is_refused_rather_than_rendered_blank(tmp_path: Path) -> None:
    """Une image vide se confondrait avec un plan qui n'a pas encore été rendu.

    Le refus nomme la cause, ce qu'un SVG de zéro tracé ne saurait pas faire.
    """
    doc = _document()
    chemin = tmp_path / "vide.dxf"
    doc.saveas(chemin)

    with pytest.raises(rendu_de_plan.RenduRefuse) as refus:
        rendu_de_plan.rendre(chemin)
    assert refus.value.code == "plan_vide"


def test_a_drawing_beyond_the_entity_cap_is_refused_before_anything_is_drawn(
    tmp_path: Path,
) -> None:
    """Le plafond est ABAISSÉ au lieu de fabriquer deux cents mille entités.

    Ce qui est éprouvé est la borne, pas sa valeur : `rendre` accepte un
    plafond en paramètre précisément pour que ce test coûte trois lignes. Le
    plafond livré, lui, est documenté comme une garde de dernier ressort —
    il ne protège pas de l'amplification, et le test de la hachure le dit.
    """
    chemin = _mur_simple(tmp_path / "trois-entites.dxf")

    with pytest.raises(rendu_de_plan.RenduRefuse) as refus:
        rendu_de_plan.rendre(chemin, plafond_entites=2)
    assert refus.value.code == "plan_trop_charge"
    # Sous le plafond, le même fichier passe : sans cela, la borne pourrait
    # être un refus inconditionnel.
    assert rendu_de_plan.rendre(chemin, plafond_entites=3).entites_rendues == 3


# ---------------------------------------------------------------------------
# Le repère commun : ce qui fait tenir « situer une cotation sur le plan »
# ---------------------------------------------------------------------------


def _plan_cote(chemin: Path) -> Path:
    """Un dessin varié ET coté : lignes, cercle, hachure, cotation en haut."""
    doc = _document()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10000, 0), dxfattribs={"layer": "MURS"})
    msp.add_line((0, 0), (0, 10000), dxfattribs={"layer": "MURS"})
    msp.add_line((0, 10000), (10000, 10000), dxfattribs={"layer": "MURS"})
    msp.add_circle((5000, 2000), 400, dxfattribs={"layer": "POTEAUX"})
    hachure = msp.add_hatch(color=3, dxfattribs={"layer": "DALLES"})
    hachure.paths.add_polyline_path(
        [(1000, 1000), (4000, 1000), (4000, 4000), (1000, 4000)], is_closed=True
    )
    cote = msp.add_linear_dim(
        base=(0, 10500), p1=(0, 10000), p2=(10000, 10000), dxfattribs={"layer": "COTATIONS"}
    )
    cote.render()
    doc.saveas(chemin)
    return chemin


def test_the_rendering_frame_and_the_reading_frame_are_the_same(tmp_path: Path) -> None:
    """**Rien dans le code n'impose cette égalité, et tout en dépend.**

    `rendu_de_plan.rendre` et `lecture_dxf.lire(situer=True)` appellent chacun
    `bbox.extents(modelspace)`, séparément, dans deux passages distincts sur
    le même fichier. C'est uniquement parce que les deux repères coïncident
    qu'un cadre normalisé dans [0,1] tombe au bon endroit sur l'image — le
    navigateur ne recalcule rien.

    Si l'un des deux se mettait à borner autrement — en ignorant les entités
    invisibles, en ajoutant une marge, en se limitant à un calque — les
    surlignages se décaleraient en silence sur TOUT le produit : aucune erreur,
    aucune exception, juste des cotations désignées à côté. C'est le test qui
    tient toute la fonction « situer une cotation sur le plan ».
    """
    chemin = _plan_cote(tmp_path / "cote.dxf")

    cadre_du_rendu = rendu_de_plan.rendre(chemin).cadre
    etendue_de_la_lecture = lecture_dxf.lire(chemin, situer=True).etendue

    assert etendue_de_la_lecture is not None
    assert cadre_du_rendu == etendue_de_la_lecture, (
        "le rendu et la lecture ne bornent plus le même dessin : "
        f"rendu={cadre_du_rendu} lecture={etendue_de_la_lecture}"
    )


def test_a_dimension_is_located_in_the_upper_half_when_it_sits_at_the_top_of_the_drawing(
    tmp_path: Path,
) -> None:
    """**Le piège de l'axe inversé**, et le seul moyen de le voir.

    L'axe vertical d'un DXF MONTE, celui d'une image DESCEND. L'origine d'un
    cadre de citation est donc en haut à gauche, et `y` s'inverse au passage.
    La cotation fabriquée ici est clairement en HAUT du dessin — son `y` DXF
    est le plus grand du fichier — et son cadre doit donc avoir un `y0`
    PROCHE DE ZÉRO.

    Sans ce test, une inversion oubliée surlignerait le BAS du plan en croyant
    montrer le haut. Rien ne tomberait : les bornes resteraient dans [0,1], la
    contrainte de base serait satisfaite, et l'écran mentirait silencieusement.
    Un contrôle qui vérifierait seulement « le cadre est dans [0,1] » ne
    distinguerait pas les deux conventions.
    """
    chemin = _plan_cote(tmp_path / "cote-en-haut.dxf")
    constat = lecture_dxf.lire(chemin, situer=True)

    assert constat.etendue is not None
    _, _, _, y_max_du_dessin = constat.etendue
    (cotation,) = constat.cotations
    assert cotation.cadre is not None, "sans cadre, il n'y a rien à surligner"

    # La cotation est bien le point le plus haut du DXF : la fixture tient.
    # Sans ce contrôle, un dessin mal fabriqué rendrait le test tautologique.
    from ezdxf import bbox, recover

    document, _ = recover.readfile(str(chemin))
    cotations = document.modelspace().query("DIMENSION")
    assert bbox.extents(cotations).extmax.y == pytest.approx(y_max_du_dessin)

    assert cotation.cadre.y0 < 0.25, (
        "une cotation du HAUT du dessin est située dans le BAS de l'image : "
        f"l'inversion de l'axe y a été perdue — cadre={cotation.cadre}"
    )
    assert cotation.cadre.y1 < 0.5
    assert 0.0 <= cotation.cadre.y0 < cotation.cadre.y1 <= 1.0
    assert 0.0 <= cotation.cadre.x0 < cotation.cadre.x1 <= 1.0
