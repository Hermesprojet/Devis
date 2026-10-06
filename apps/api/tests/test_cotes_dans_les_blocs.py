"""Les cotations rangées dans des blocs : valeurs, emplacements, et un seul compte.

Un dessinateur range rarement ses cotes à plat dans l'espace modèle. Il fait un
bloc « façade », un bloc « porte », et les insère — une fois, dix fois, en
grille, les uns dans les autres. **Vérifié par exécution avant d'écrire une
ligne de code** : un fichier dont les trois cotations vivent dans des blocs
rendait `0 cotation`. Pas une erreur, pas une anomalie : zéro, en silence. Le
propriétaire aurait conclu que Metreo ne sait pas lire son plan.

Ce fichier éprouve les trois choses que le propriétaire a nommées, et une
quatrième qui est le revers de la troisième.

1. **Les valeurs.** Une cotation de 5 000 dans un bloc inséré à l'échelle 2
   mesure 10 000 dans l'ouvrage. Lire la cotation sans la transformer donne une
   quantité deux fois trop petite — c'est le défaut le plus coûteux de tout ce
   module, parce qu'il produit un nombre plausible.
2. **Les emplacements.** Le même bloc inséré deux fois donne deux cotations à
   deux endroits. Si les deux se montrent au même endroit, le propriétaire ne
   peut pas retrouver celle qu'il vérifie.
3. **L'absence de double comptage.** `document.blocks` contient
   `*Model_Space` : parcourir « tous les blocs » compte chaque cotation de
   premier niveau deux fois. C'est le piège, il est documenté ici, et un test
   le ferme.
4. **L'absence de SOUS-comptage**, qui est le même défaut par l'autre bout. Une
   cotation dans un bloc inséré trois fois porte TROIS fois le même handle, et
   la base déduplique les citations sur ce champ : deux mesures sur trois
   disparaîtraient. Chaque instance porte donc son chemin d'insertion.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from metreo_api.services import lecture_dxf

ezdxf = pytest.importorskip(
    "ezdxf",
    reason="l'extra « plans » n'est pas installé : pip install './apps/api[plans]'",
)

#: La cotation que portent tous les blocs de ce fichier, en unités de dessin.
COTE = Decimal("5000")


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


def _document():
    """Un document en millimètres, avec une entité à plat pour l'étendue.

    La ligne du modelspace n'est pas décorative : sans aucune entité de
    premier niveau, `bbox.extents(modelspace)` pourrait rendre une étendue
    vide, et les tests de position porteraient sur `None`.
    """
    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line((0, 0), (1, 0))
    return doc


def _bloc_cote(doc, nom: str = "COTE_MUR", longueur: float = float(COTE), calque: str = "0"):
    """Un bloc portant UNE cotation linéaire, et rien d'autre.

    `render()` n'est pas décoratif : sans lui la cotation n'a pas de bloc
    géométrique, l'audit la retire au rechargement, et le bloc revient vide.
    """
    bloc = doc.blocks.new(nom)
    cote = bloc.add_linear_dim(
        base=(0, -800), p1=(0, 0), p2=(longueur, 0), dxfattribs={"layer": calque}
    )
    cote.render()
    return bloc


def _ecrire(doc, chemin: Path) -> Path:
    doc.saveas(chemin)
    return chemin


def _lire(doc, tmp_path: Path, *, situer: bool = True) -> lecture_dxf.LecturePlan:
    constat = lecture_dxf.lire(_ecrire(doc, tmp_path / "plan.dxf"), situer=situer)
    assert not constat.refuse, (
        f"le plan est refusé : {constat.motif_du_refus.code if constat.motif_du_refus else '?'}"
    )
    return constat


# ---------------------------------------------------------------------------
# 1. Trouver la cotation — la régression qui rendait zéro
# ---------------------------------------------------------------------------


def test_a_dimension_that_lives_in_a_block_is_found(tmp_path: Path) -> None:
    """Le cas de base, et la régression qu'il ferme.

    `modelspace.query("DIMENSION")` ne voit que le premier niveau : sur ce
    fichier, il rend zéro. Le lecteur doit descendre par l'insertion.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0))

    constat = _lire(doc, tmp_path)

    assert len(constat.cotations) == 1
    (cotation,) = constat.cotations
    assert cotation.valeur == COTE
    assert cotation.fiabilite == "mesurable"

    # Et la preuve que le piège est réel : la requête de premier niveau, elle,
    # ne trouve rien sur ce même fichier.
    document, _ = ezdxf.recover.readfile(str(tmp_path / "plan.dxf"))
    assert len(document.modelspace().query("DIMENSION")) == 0, (
        "si cette requête trouve quelque chose, la fixture ne range plus sa "
        "cotation dans un bloc et ce test ne prouve plus rien"
    )


def test_the_layer_of_a_dimension_in_a_block_is_kept(tmp_path: Path) -> None:
    """Le calque reste celui du dessin : c'est par lui qu'on retrouve un lot.

    Sur un plan réel, les calques nomment le chantier ou le lot. Une cotation
    qui perdrait son calque en traversant un bloc deviendrait impossible à
    rattacher à une ligne de bordereau.
    """
    doc = _document()
    _bloc_cote(doc, calque="COTATIONS-LOT-02")
    doc.modelspace().add_blockref("COTE_MUR", (0, 0))

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.calque == "COTATIONS-LOT-02"


# ---------------------------------------------------------------------------
# 2. Les valeurs sous transformation
# ---------------------------------------------------------------------------


def test_a_scaled_insertion_scales_the_measurement(tmp_path: Path) -> None:
    """Une cotation de 5 000 insérée à l'échelle 2 mesure 10 000.

    **C'est le test le plus important de ce fichier.** La mesure de la
    cotation d'origine, dans son bloc, vaut 5 000 et ne change jamais. Lire
    cette valeur-là produirait une quantité deux fois trop petite — un nombre
    parfaitement plausible, qu'aucun contrôle d'ordre de grandeur n'attrape,
    et qui part dans un devis.

    Le lecteur doit donc transformer avant de mesurer. Vérifié à l'exécution
    sur ezdxf 1.4.4 : `entite.copy()` puis `.transform(matrice)` rend 10 000.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0), dxfattribs={"xscale": 2, "yscale": 2})

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.valeur == COTE * 2
    assert cotation.fiabilite == "mesurable", (
        "une échelle uniforme ne fragilise rien : la mesure est exacte"
    )


def test_a_rotated_insertion_moves_the_dimension_without_changing_its_length(
    tmp_path: Path,
) -> None:
    """Une rotation déplace, elle ne redimensionne pas.

    Le contre-exemple du test précédent, et il est nécessaire : un lecteur qui
    tirerait la longueur d'une projection sur un axe donnerait zéro pour une
    cote tournée de 90°.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0), dxfattribs={"rotation": 90})

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.valeur == COTE
    assert cotation.fiabilite == "mesurable"


def test_a_mirrored_insertion_keeps_the_length(tmp_path: Path) -> None:
    """Un miroir non plus ne change pas une longueur.

    Une façade symétrique s'insère ainsi, avec un facteur négatif. Un lecteur
    qui multiplierait par le facteur brut rendrait une longueur NÉGATIVE, que
    le lecteur déclarerait alors inexploitable — une mesure juste perdue par un
    signe.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0), dxfattribs={"xscale": -1})

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.valeur == COTE
    assert cotation.fiabilite == "mesurable"


def test_a_non_uniform_scale_keeps_the_measurement_to_be_verified(
    tmp_path: Path,
) -> None:
    """Étirer x de 2 et y de 3 rend la longueur dépendante de la direction.

    Une cote à 45° sous cette insertion ne vaut ni le double ni le triple :
    elle vaut quelque chose entre les deux, qui dépend de son angle exact.
    ezdxf rendra bien un nombre — celui de la direction où la cote est
    dessinée — et ce nombre n'est pas faux, il est indécidable à distance.

    La mesure est donc **conservée** avec le statut « à vérifier », et la
    réserve est nommée. L'écarter priverait le propriétaire d'une cote qui est
    peut-être juste ; la présenter comme sûre lui ferait signer un nombre que
    personne n'a vérifié.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0), dxfattribs={"xscale": 2, "yscale": 3})

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.valeur is not None, "la mesure est conservée, pas écartée"
    assert cotation.fiabilite == "a_confirmer"

    codes = [a.code for a in cotation.anomalies]
    assert "echelle_de_bloc_non_uniforme" in codes
    reserve = next(a for a in cotation.anomalies if a.code == "echelle_de_bloc_non_uniforme")
    assert "direction" in reserve.message
    assert "COTE_MUR" in reserve.message, "la réserve nomme le bloc concerné"


def test_a_degenerate_matrix_is_named_rather_than_measured() -> None:
    """Un facteur d'échelle nul aplatit le bloc : il n'en sort aucune longueur.

    **Ce test porte sur la fonction et non sur un fichier, et c'est une
    conclusion, pas un raccourci.** Vérifié à l'exécution sur ezdxf 1.4.4 :
    une insertion de facteur nul est ramenée à 1 — à l'affectation de
    l'attribut, à l'écriture du fichier et à sa relecture. Même `1e-180` est
    ramené à 1. Aucun DXF passant par ezdxf ne peut donc porter une insertion
    dégénérée, et un test qui en fabriquerait un éprouverait la normalisation
    d'ezdxf, pas la garde.

    Ce que la garde borne réellement est la matrice **composée** : une
    imbrication de facteurs extrêmes mais acceptés peut sous-déborder vers
    zéro, et c'est là qu'un lecteur rendrait une longueur nulle sans le dire.
    `Matrix44.scale(0, 1, 1)` reproduit exactement cet état.
    """
    from ezdxf.math import Matrix44

    reserve = lecture_dxf._reserve_d_echelle(Matrix44.scale(0.0, 1.0, 1.0), ("FACADE",))

    assert reserve is not None
    assert reserve.code == "echelle_de_bloc_degeneree"
    assert "FACADE" in reserve.message

    # Et la composition qui sous-déborde, qui est le cas réellement atteignable.
    minuscule = Matrix44.scale(1e-180, 1e-180, 1.0)
    reserve = lecture_dxf._reserve_d_echelle(minuscule @ minuscule, ("A", "B"))
    assert reserve is not None
    assert reserve.code == "echelle_de_bloc_degeneree"


def test_a_uniform_scale_raises_no_reserve_at_all() -> None:
    """Le contre-exemple des deux réserves d'échelle.

    Sans lui, une fonction qui réserverait sur TOUTE insertion passerait les
    deux tests ci-dessus, et « à vérifier » perdrait son sens en s'appliquant
    à tout.
    """
    from ezdxf.math import Matrix44

    assert lecture_dxf._reserve_d_echelle(Matrix44.scale(2.0, 2.0, 2.0), ("A",)) is None
    assert lecture_dxf._reserve_d_echelle(Matrix44.translate(10, 20, 0), ("A",)) is None
    # Une rotation ne change aucune magnitude.
    assert lecture_dxf._reserve_d_echelle(Matrix44.z_rotate(1.2), ("A",)) is None


# ---------------------------------------------------------------------------
# 3. Les emplacements, et l'imbrication
# ---------------------------------------------------------------------------


def test_a_block_inserted_twice_gives_two_measurements_at_two_places(
    tmp_path: Path,
) -> None:
    """Deux insertions, deux cotations, deux positions distinctes.

    Les positions comptent autant que les valeurs : le propriétaire doit
    pouvoir retrouver sur le plan LA cotation qu'il vérifie. Deux mesures
    posées au même endroit lui en désignent une au hasard.
    """
    doc = _document()
    _bloc_cote(doc)
    espace = doc.modelspace()
    espace.add_blockref("COTE_MUR", (0, 0))
    espace.add_blockref("COTE_MUR", (0, 40000))

    constat = _lire(doc, tmp_path)

    assert len(constat.cotations) == 2
    assert {c.valeur for c in constat.cotations} == {COTE}

    cadres = [c.cadre for c in constat.cotations]
    assert all(cadre is not None for cadre in cadres), "les deux sont situées"
    hauteurs = sorted(cadre.y0 for cadre in cadres if cadre is not None)
    assert hauteurs[1] - hauteurs[0] > 0.5, (
        "les deux insertions sont à 40 000 unités l'une de l'autre : des "
        "positions voisines signifient que la transformation n'est pas appliquée"
    )


def test_a_nested_block_composes_its_transformations(tmp_path: Path) -> None:
    """Deux niveaux : la cotation est déplacée par les DEUX insertions.

    Le bloc « façade » est inséré à (10 000, 0) et contient le bloc de cote à
    (1 000, 2 000). La cotation est donc à (11 000, 2 000) dans le dessin. Un
    lecteur qui n'appliquerait que la dernière matrice la poserait à
    (1 000, 2 000) — à dix mètres de là.

    Le test compare au cas plat : la MÊME cotation posée directement à
    (11 000, 2 000) doit tomber au même endroit, à un pouillème près.
    """
    doc = _document()
    _bloc_cote(doc)
    facade = doc.blocks.new("FACADE")
    facade.add_blockref("COTE_MUR", (1000, 2000))
    espace = doc.modelspace()
    espace.add_blockref("FACADE", (10000, 0))
    # Le témoin, à plat, à la position que la composition doit donner.
    espace.add_blockref("COTE_MUR", (11000, 2000))

    constat = _lire(doc, tmp_path)
    assert len(constat.cotations) == 2

    par_profondeur = {c.object_ref.count("/"): c for c in constat.cotations}
    imbriquee = par_profondeur[2]
    temoin = par_profondeur[1]

    assert imbriquee.valeur == temoin.valeur == COTE
    assert imbriquee.cadre is not None and temoin.cadre is not None
    assert imbriquee.cadre.x0 == pytest.approx(temoin.cadre.x0, abs=1e-6)
    assert imbriquee.cadre.y0 == pytest.approx(temoin.cadre.y0, abs=1e-6)


def test_nested_scales_multiply(tmp_path: Path) -> None:
    """Un bloc à l'échelle 2 dans un bloc à l'échelle 3 : la cote est ×6.

    Les transformations se composent, elles ne se remplacent pas. Un lecteur
    qui ne garderait que la dernière rendrait 15 000 au lieu de 30 000.
    """
    doc = _document()
    _bloc_cote(doc)
    enveloppe = doc.blocks.new("ENVELOPPE")
    enveloppe.add_blockref("COTE_MUR", (0, 0), dxfattribs={"xscale": 2, "yscale": 2})
    doc.modelspace().add_blockref("ENVELOPPE", (0, 0), dxfattribs={"xscale": 3, "yscale": 3})

    (cotation,) = _lire(doc, tmp_path).cotations
    assert cotation.valeur == COTE * 6
    assert cotation.fiabilite == "mesurable", (
        "deux échelles uniformes composent une échelle uniforme"
    )


def test_an_array_insertion_yields_one_measurement_per_grid_element(
    tmp_path: Path,
) -> None:
    """Une insertion en grille 3 × 2 vaut six exemplaires, pas un.

    C'est un piège d'ezdxf, pas du format : sur une grille 3 × 2,
    `virtual_entities()` rend **une** DIMENSION — vérifié à l'exécution — là
    où `multi_insert()` rend bien les six, chacune à sa place. S'en remettre à
    la première aurait compté une cote sur six.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref(
        "COTE_MUR",
        (0, 0),
        dxfattribs={
            "row_count": 3,
            "column_count": 2,
            "row_spacing": 9000,
            "column_spacing": 7000,
        },
    )

    constat = _lire(doc, tmp_path)

    assert len(constat.cotations) == 6
    assert {c.valeur for c in constat.cotations} == {COTE}
    positions = {
        (round(c.cadre.x0, 6), round(c.cadre.y0, 6))
        for c in constat.cotations
        if c.cadre is not None
    }
    assert len(positions) == 6, f"six exemplaires à six endroits, obtenu {positions}"


# ---------------------------------------------------------------------------
# 4. Ni double comptage, ni sous-comptage
# ---------------------------------------------------------------------------


def test_the_model_space_is_not_counted_twice(tmp_path: Path) -> None:
    """Le piège nommé, et refermé.

    `document.blocks` contient `*Model_Space`. Un lecteur qui parcourrait
    « tous les blocs du document » compterait donc chaque cotation de premier
    niveau DEUX fois : une fois par l'espace modèle, une fois par le bloc du
    même nom. Le test le vérifie en deux temps — d'abord que le piège existe,
    ensuite que le lecteur n'y tombe pas.
    """
    doc = _document()
    doc.modelspace().add_linear_dim(base=(0, -2000), p1=(0, -1000), p2=(3000, -1000)).render()

    chemin = _ecrire(doc, tmp_path / "plan.dxf")
    document, _ = ezdxf.recover.readfile(str(chemin))
    noms = [bloc.name for bloc in document.blocks]
    assert "*Model_Space" in noms, (
        "si l'espace modèle n'est plus un bloc du document, ce piège a disparu "
        "et ce test peut être retiré"
    )

    constat = lecture_dxf.lire(chemin, situer=True)
    assert len(constat.cotations) == 1, (
        f"une seule cotation existe, {len(constat.cotations)} ont été comptées"
    )


def test_a_block_that_is_never_inserted_contributes_nothing(tmp_path: Path) -> None:
    """Une définition n'est pas une instance : un bloc non inséré est un fantôme.

    Un fichier de travail porte souvent des blocs abandonnés. Leurs cotes ne
    sont pas dans l'ouvrage, et les proposer ferait apparaître des quantités
    qui ne correspondent à rien de dessiné.
    """
    doc = _document()
    _bloc_cote(doc, "UTILISE")
    _bloc_cote(doc, "ABANDONNE", longueur=9999.0)
    doc.modelspace().add_blockref("UTILISE", (0, 0))

    constat = _lire(doc, tmp_path)

    assert len(constat.cotations) == 1
    assert constat.cotations[0].valeur == COTE
    assert Decimal("9999") not in {c.valeur for c in constat.cotations}


def test_every_instance_of_the_same_block_can_be_told_apart(tmp_path: Path) -> None:
    """Trois insertions du même bloc : trois désignations distinctes.

    **C'est le sous-comptage, et il est aussi grave que le double comptage.**
    La cotation n'existe qu'une fois dans le fichier : les trois instances
    portent donc le MÊME handle. Or `services/mesures_de_plan.py` déduplique
    les citations sur `object_id` — trois mesures de même désignation
    n'écriraient qu'une proposition, et deux cotes sur trois disparaîtraient
    sans un mot.

    Chaque instance porte donc son chemin d'insertion.
    """
    doc = _document()
    _bloc_cote(doc)
    espace = doc.modelspace()
    for rang in range(3):
        espace.add_blockref("COTE_MUR", (0, rang * 40000))

    constat = _lire(doc, tmp_path)

    assert len(constat.cotations) == 3
    references = [c.object_ref for c in constat.cotations]
    assert len(set(references)) == 3, f"désignations en doublon : {references}"
    assert all(reference.strip() for reference in references), (
        "une désignation vide ne cite rien, et la base la refuse"
    )
    # Et la désignation reste courte : la base la borne à 120 caractères.
    assert all(len(reference) <= 120 for reference in references)


def test_a_top_level_dimension_keeps_the_designation_it_always_had(
    tmp_path: Path,
) -> None:
    """Une cotation de premier niveau garde son handle nu, sans préfixe.

    Ce n'est pas une préférence d'écriture : des citations sont déjà écrites en
    base avec le handle seul. Si une relecture désignait désormais la même
    cotation autrement, elle ne reconnaîtrait plus sa propre proposition et en
    créerait une seconde — y compris quand un humain a déjà tranché la
    première.
    """
    doc = _document()
    cote = doc.modelspace().add_linear_dim(base=(0, -2000), p1=(0, -1000), p2=(3000, -1000))
    cote.render()

    chemin = _ecrire(doc, tmp_path / "plan.dxf")
    (cotation,) = lecture_dxf.lire(chemin, situer=True).cotations

    assert "/" not in cotation.object_ref
    document, _ = ezdxf.recover.readfile(str(chemin))
    handles = {entite.dxf.handle for entite in document.modelspace().query("DIMENSION")}
    assert cotation.object_ref in handles


# ---------------------------------------------------------------------------
# 5. Les bornes : profondeur, cycle, nombre
# ---------------------------------------------------------------------------


def test_nesting_deeper_than_the_ceiling_stops_and_says_so(tmp_path: Path) -> None:
    """Au-delà du plafond, la descente s'arrête — et l'annonce.

    Ce qui est vérifié n'est pas l'arrêt, c'est qu'il se DISE. Une lecture
    silencieusement incomplète serait présentée comme complète, et un métré
    manquerait des cotes sans que rien ne l'indique.
    """
    doc = _document()
    _bloc_cote(doc, "N0")
    for niveau in range(1, lecture_dxf.PLAFOND_IMBRICATION + 4):
        doc.blocks.new(f"N{niveau}").add_blockref(f"N{niveau - 1}", (0, 0))
    doc.modelspace().add_blockref(f"N{lecture_dxf.PLAFOND_IMBRICATION + 3}", (0, 0))

    constat = _lire(doc, tmp_path, situer=False)

    assert constat.cotations == []
    codes = [a.code for a in constat.anomalies]
    assert "imbrication_trop_profonde" in codes
    message = next(a.message for a in constat.anomalies if a.code == "imbrication_trop_profonde")
    assert str(lecture_dxf.PLAFOND_IMBRICATION) in message


def test_nesting_up_to_the_ceiling_is_read(tmp_path: Path) -> None:
    """Le plafond est atteignable : juste en dessous, la cote est lue.

    Sans ce contre-exemple, un plafond fixé à zéro passerait le test précédent
    et ne lirait plus aucun bloc.
    """
    doc = _document()
    _bloc_cote(doc, "N0")
    dernier = lecture_dxf.PLAFOND_IMBRICATION - 1
    for niveau in range(1, dernier + 1):
        doc.blocks.new(f"N{niveau}").add_blockref(f"N{niveau - 1}", (0, 0))
    doc.modelspace().add_blockref(f"N{dernier}", (0, 0))

    constat = _lire(doc, tmp_path, situer=False)

    assert len(constat.cotations) == 1
    assert constat.cotations[0].valeur == COTE
    assert "imbrication_trop_profonde" not in [a.code for a in constat.anomalies]


def test_a_block_cycle_does_not_loop_forever() -> None:
    """La seconde ligne de défense contre un cycle, éprouvée directement.

    Un cycle de référence de bloc est normalement refusé en amont : ezdxf le
    signale à l'audit (code 104), et `lire` refuse le fichier. Ce test
    court-circuite ce refus et appelle le parcours sur un document construit en
    mémoire, où aucun audit ne tourne.

    Pourquoi s'en soucier alors que l'audit refuse déjà : parce qu'un audit qui
    cesserait de signaler ce cas — un changement de version d'ezdxf suffit —
    transformerait le parcours en récursion infinie. Le processus ne rendrait
    pas d'erreur : il mangerait la mémoire du conteneur. Cette garde est donc
    éprouvée pour elle-même, et non à travers le refus qui la masque.
    """
    doc = ezdxf.new("R2013", setup=True)
    premier = doc.blocks.new("A")
    second = doc.blocks.new("B")
    premier.add_blockref("B", (0, 0))
    second.add_blockref("A", (0, 0))
    second.add_linear_dim(base=(0, -800), p1=(0, 0), p2=(float(COTE), 0)).render()
    doc.modelspace().add_blockref("A", (0, 0))

    anomalies: list[lecture_dxf.Anomalie] = []
    instances = lecture_dxf._instances_de_cotation(doc, anomalies)

    # La cotation accessible avant le cycle est bien récoltée.
    assert len(instances) == 1
    assert "cycle_de_blocs_evite" in [a.code for a in anomalies]


def test_a_file_with_a_block_cycle_is_still_refused_outright(tmp_path: Path) -> None:
    """Et le refus en amont n'a pas été affaibli par la garde ci-dessus.

    Les deux coexistent, et l'ordre compte : un fichier qui porte un cycle est
    refusé AVANT tout parcours. La garde du parcours ne sert que si ce refus
    disparaît.
    """
    doc = _document()
    premier = doc.blocks.new("A")
    second = doc.blocks.new("B")
    premier.add_blockref("B", (0, 0))
    second.add_blockref("A", (0, 0))
    doc.modelspace().add_blockref("A", (0, 0))

    constat = lecture_dxf.lire(_ecrire(doc, tmp_path / "cycle.dxf"))

    assert constat.refuse
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "cycle_de_blocs"


def test_too_many_instances_stops_the_harvest_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Au plafond d'instances, on s'arrête en le disant.

    Le plafond réel est à vingt mille ; une fixture de cette taille coûterait
    des secondes pour éprouver une comparaison. Il est donc abaissé à 3 le
    temps du test, ce qui éprouve la même ligne.
    """
    monkeypatch.setattr(lecture_dxf, "PLAFOND_INSTANCES", 3)

    doc = _document()
    _bloc_cote(doc)
    espace = doc.modelspace()
    for rang in range(10):
        espace.add_blockref("COTE_MUR", (0, rang * 9000))

    constat = _lire(doc, tmp_path, situer=False)

    assert len(constat.cotations) == 3
    codes = [a.code for a in constat.anomalies]
    assert "trop_de_cotations" in codes
    message = next(a.message for a in constat.anomalies if a.code == "trop_de_cotations")
    assert "complète" in message


# ---------------------------------------------------------------------------
# 6. Les présentations : comptées, non mesurées
# ---------------------------------------------------------------------------


def test_dimensions_in_a_paper_space_layout_are_announced_but_not_measured(
    tmp_path: Path,
) -> None:
    """Une présentation annote une feuille : ses cotes redoublent le dessin.

    Les reprendre donnerait deux mesures pour un seul ouvrage — un double
    comptage au sens métier, et non au sens du parcours. Elles ne sont donc
    pas lues. Mais elles sont COMPTÉES et annoncées : un silence se lirait
    « il n'y en a pas », ce qui serait faux.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0))

    feuille = doc.layouts.new("FEUILLE A3")
    feuille.add_linear_dim(base=(0, -50), p1=(0, 0), p2=(100, 0)).render()

    constat = _lire(doc, tmp_path)

    # Une seule mesure : celle de l'espace modèle.
    assert len(constat.cotations) == 1
    assert constat.cotations[0].valeur == COTE

    codes = [a.code for a in constat.anomalies]
    assert "cotations_en_presentation" in codes
    message = next(a.message for a in constat.anomalies if a.code == "cotations_en_presentation")
    assert "1 cotation" in message


def test_a_plan_without_any_layout_dimension_says_nothing_about_layouts(
    tmp_path: Path,
) -> None:
    """Le contre-exemple : pas d'anomalie quand il n'y a rien à annoncer.

    Sans lui, un lecteur qui annoncerait des cotes de présentation sur TOUS
    les plans passerait le test précédent, et l'anomalie ne voudrait plus rien
    dire.
    """
    doc = _document()
    _bloc_cote(doc)
    doc.modelspace().add_blockref("COTE_MUR", (0, 0))
    doc.layouts.new("FEUILLE VIDE")

    constat = _lire(doc, tmp_path)

    assert "cotations_en_presentation" not in [a.code for a in constat.anomalies]
