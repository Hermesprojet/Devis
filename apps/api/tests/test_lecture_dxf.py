"""Que rend la lecture d'un DXF, et que refuse-t-elle ?

Chaque test FABRIQUE son propre fichier, avec ezdxf, et dit en une phrase ce
qu'il met dedans. C'est voulu : le sujet de la plupart de ces cas est la valeur
exacte d'un attribut — `-1` plutôt qu'absent, `0` plutôt que `4` — et cela se
lit dans trois lignes de code, pas dans deux cents lignes de DXF commitées.

**Deux de ces cas viennent de plans d'exécution réels**, et pas de la
documentation. Ils sont signalés comme tels : ce sont ceux qui auraient produit
une fausse quantité en production.
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


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


def _document(insunits: int | None = 4):
    """Un document neuf, en millimètres par défaut.

    `insunits=None` laisse l'en-tête sans la variable, ce qu'un export
    d'ancien logiciel produit.
    """
    doc = ezdxf.new("R2013", setup=True)
    if insunits is None:
        del doc.header["$INSUNITS"]
    else:
        doc.header["$INSUNITS"] = insunits
    return doc


def _avec_cotation(
    chemin: Path,
    *,
    insunits: int | None = 4,
    longueur: float = 5000.0,
    cote_stockee: float | None = None,
    texte: str | None = None,
) -> Path:
    """Un DXF portant UNE cotation linéaire, et rien d'autre d'utile."""
    doc = _document(insunits)
    msp = doc.modelspace()
    cote = msp.add_linear_dim(
        base=(0, -1000),
        p1=(0, 0),
        p2=(longueur, 0),
        dxfattribs={"layer": "COTATIONS"},
    )
    # `render()` n'est pas décoratif : sans lui la cotation n'a pas de bloc
    # géométrique, et l'audit la retire au rechargement. Mesuré : l'espace
    # modèle revenait VIDE, et chaque test de cotation échouait sur un
    # dépaquetage de liste vide plutôt que sur ce qu'il voulait prouver.
    cote.render()
    dimension = cote.dimension
    if cote_stockee is not None:
        dimension.dxf.actual_measurement = cote_stockee
    if texte is not None:
        dimension.dxf.text = texte
    doc.saveas(chemin)
    return chemin


# ---------------------------------------------------------------------------
# L'unité du document : sans elle, aucune mesure
# ---------------------------------------------------------------------------


def test_the_drawing_unit_is_read_from_the_file_and_never_assumed(tmp_path: Path) -> None:
    constat = lecture_dxf.lire(_avec_cotation(tmp_path / "mm.dxf", insunits=4))
    assert constat.insunits == 4
    assert constat.unite_source == "mm"
    assert constat.mesurable is True


@pytest.mark.parametrize(
    ("insunits", "code_attendu"),
    [
        (0, "unite_sans_valeur"),
        (None, "unite_absente"),
        (20, "unite_non_prise_en_charge"),
    ],
)
def test_without_a_usable_unit_nothing_is_measurable_and_the_reason_is_named(
    tmp_path: Path, insunits: int | None, code_attendu: str
) -> None:
    """Un 5000 sans unité peut valoir 5 mètres comme 5 kilomètres.

    Le fichier n'est pas refusé pour autant : la visualisation et l'archivage
    ne demandent aucune unité. C'est la MESURE qui est interdite.
    """
    chemin = _avec_cotation(tmp_path / "sans-unite.dxf", insunits=insunits)
    constat = lecture_dxf.lire(chemin)

    assert constat.mesurable is False
    assert constat.unite_source is None
    assert constat.refuse is False, "le fichier reste consultable"
    assert code_attendu in [a.code for a in constat.anomalies]


# ---------------------------------------------------------------------------
# Les cotations — et le piège rencontré sur de vrais plans
# ---------------------------------------------------------------------------


def test_a_stored_measurement_of_minus_one_is_not_trusted(tmp_path: Path) -> None:
    """**Cas relevé sur des plans d'exécution réels.**

    Sur deux plans d'un même projet, 1 410 cotations sur 1 410 portent
    l'attribut `actual_measurement` — et il vaut `-1` pour toutes. Un contrôle
    de simple présence, celui que la documentation d'ezdxf suggère en
    avertissant que l'attribut est « souvent absent », l'aurait accepté et
    produit des quantités de -1 mm.

    La valeur n'est retenue que si elle est présente ET positive.
    """
    chemin = _avec_cotation(tmp_path / "sentinelle.dxf", longueur=5000.0, cote_stockee=-1.0)
    constat = lecture_dxf.lire(chemin)

    (cotation,) = constat.cotations
    assert cotation.origine == "recalcul", "la sentinelle a été prise pour une mesure"
    assert cotation.valeur == pytest.approx(Decimal("5000"), abs=Decimal("0.01"))
    assert cotation.fiabilite == "mesurable"
    assert "cote_stockee_sentinelle" in [a.code for a in cotation.anomalies]


def test_a_positive_stored_measurement_is_used_as_is(tmp_path: Path) -> None:
    """Le pendant du test précédent : la cote stockée sert quand elle vaut
    quelque chose, et l'origine le dit."""
    chemin = _avec_cotation(tmp_path / "cote-utile.dxf", longueur=5000.0, cote_stockee=4999.5)
    constat = lecture_dxf.lire(chemin)

    (cotation,) = constat.cotations
    assert cotation.origine == "cote_42"
    assert cotation.valeur == Decimal("4999.5")
    assert [a.code for a in cotation.anomalies] == []


def test_a_hand_typed_label_is_kept_beside_the_measurement_not_instead_of_it(
    tmp_path: Path,
) -> None:
    """Le texte d'une cotation est saisi par un dessinateur.

    Il écrase la mesure à l'écran sans toucher la géométrie. Quelqu'un qui a
    tapé « 3500 » sur une cote qui mesure 5000 est une information, pas un
    détail — et ce n'est pas à un lecteur de trancher.
    """
    chemin = _avec_cotation(
        tmp_path / "texte.dxf", longueur=5000.0, cote_stockee=-1.0, texte="3500"
    )
    constat = lecture_dxf.lire(chemin)

    (cotation,) = constat.cotations
    assert cotation.texte_impose == "3500"
    assert cotation.valeur == pytest.approx(Decimal("5000"), abs=Decimal("0.01"))
    assert cotation.fiabilite == "a_confirmer", "une divergence n'est pas mesurable seule"
    assert "texte_impose" in [a.code for a in cotation.anomalies]


@pytest.mark.parametrize("marqueur", ["", "<>", "  "])
def test_an_empty_label_means_the_measurement_is_shown_as_is(tmp_path: Path, marqueur: str) -> None:
    """Vide, `<>` ou un espace : rien n'est imposé, donc rien à signaler."""
    chemin = _avec_cotation(
        tmp_path / f"vide-{len(marqueur)}.dxf", cote_stockee=-1.0, texte=marqueur
    )
    (cotation,) = lecture_dxf.lire(chemin).cotations
    assert cotation.texte_impose is None
    assert cotation.fiabilite == "mesurable"


def test_a_degenerate_measurement_is_refused_rather_than_counted(tmp_path: Path) -> None:
    """**Cas relevé sur des plans réels** : onze cotations mesurant entre
    0,0 et 0,6 mm, résidus d'édition. Comptées, elles ajouteraient des lignes
    de bordereau à zéro."""
    chemin = _avec_cotation(tmp_path / "degenere.dxf", longueur=0.1, cote_stockee=-1.0)
    (cotation,) = lecture_dxf.lire(chemin).cotations

    assert cotation.fiabilite == "inexploitable"
    assert "mesure_degeneree" in [a.code for a in cotation.anomalies]


def test_a_measurement_beyond_the_plausible_envelope_is_refused(tmp_path: Path) -> None:
    """`1e308` est un flottant valide : il passe l'audit du fichier, puis rend
    `inf` dans un calcul d'aire, puis `nan` dans un total de devis. Le
    garde-fou doit intervenir AVANT le calcul."""
    chemin = _avec_cotation(tmp_path / "enorme.dxf", longueur=lecture_dxf.ENVELOPPE_PLAUSIBLE * 10)
    (cotation,) = lecture_dxf.lire(chemin).cotations

    assert cotation.fiabilite == "inexploitable"
    assert "mesure_hors_enveloppe" in [a.code for a in cotation.anomalies]


def test_every_dimension_carries_its_layer_and_its_object_handle(tmp_path: Path) -> None:
    """Sans provenance, une mesure n'est pas reprenable : on ne peut plus la
    retrouver dans le plan pour la vérifier."""
    chemin = _avec_cotation(tmp_path / "provenance.dxf", cote_stockee=-1.0)
    (cotation,) = lecture_dxf.lire(chemin).cotations

    assert cotation.calque == "COTATIONS"
    assert cotation.object_ref, "le handle de l'entité est la seule désignation stable"


# ---------------------------------------------------------------------------
# Les refus
# ---------------------------------------------------------------------------


def test_a_block_reference_cycle_is_refused_before_any_traversal(tmp_path: Path) -> None:
    """ezdxf SIGNALE le cycle sans le casser, et ni `explode()` ni
    `virtual_entities()` ne portent de garde de profondeur. Parcourir un tel
    fichier est une récursion sans fin : il est refusé."""
    doc = _document(4)
    premier = doc.blocks.new("PREMIER")
    second = doc.blocks.new("SECOND")
    premier.add_blockref("SECOND", (0, 0))
    second.add_blockref("PREMIER", (0, 0))
    doc.modelspace().add_blockref("PREMIER", (0, 0))
    chemin = tmp_path / "cycle.dxf"
    doc.saveas(chemin)

    constat = lecture_dxf.lire(chemin)

    assert constat.refuse is True
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "cycle_de_blocs"
    assert constat.mesurable is False
    assert constat.cotations == [], "aucun parcours n'a eu lieu"


def test_a_file_that_is_not_a_drawing_is_refused_without_raising(tmp_path: Path) -> None:
    """Un lecteur qui lève sur un fichier abîmé fait tomber son appelant.
    Celui-ci rend un refus, que l'appelant peut présenter."""
    chemin = tmp_path / "pas-un-plan.dxf"
    chemin.write_text("ceci n'est pas un plan\n", encoding="utf-8")

    constat = lecture_dxf.lire(chemin)

    assert constat.refuse is True
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code in ("fichier_invalide", "fichier_illisible")


@pytest.mark.parametrize(
    ("nom", "octets"),
    [
        (
            "octet indefini dans la page de code",
            b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC10\x81\x9d\n  0\nENDSEC\n  0\nEOF\n",
        ),
        (
            "page de code utf-8 annoncee, octets invalides",
            b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1027\n"
            b"  9\n$DWGCODEPAGE\n  3\nUTF-8\n  9\n$PROJECTNAME\n  1\n\xff\xfe\xff\n"
            b"  0\nENDSEC\n  0\nEOF\n",
        ),
    ],
)
def test_undecodable_bytes_are_refused_rather_than_half_read(
    tmp_path: Path, nom: str, octets: bytes
) -> None:
    """Le mode tolérant d'ezdxf charge des fichiers portant des erreurs de
    décodage et les journalise SANS lever, en prévenant dans sa documentation
    qu'un tel fichier « perd de l'information ». On l'appelle donc en mode
    strict, et on refuse plutôt que de mesurer sur un contenu amputé.

    Les deux jeux d'octets ci-dessous ont été TROUVÉS par essai, pas devinés :
    une première version de ce test posait des octets qui, eux, se décodaient
    parfaitement — le fichier était lu comme un AC1009 vide, sans refus. Un
    test qui exige un refus doit d'abord provoquer la condition qui le
    déclenche.
    """
    chemin = tmp_path / "indecodable.dxf"
    chemin.write_bytes(octets)

    constat = lecture_dxf.lire(chemin)

    assert constat.refuse is True, nom
    assert constat.motif_du_refus is not None
    assert constat.motif_du_refus.code == "fichier_illisible"
    assert constat.mesurable is False


def test_a_file_that_parses_but_says_nothing_is_not_refused_and_not_measurable(
    tmp_path: Path,
) -> None:
    """Le pendant du test précédent, et la frontière entre les deux.

    Un DXF syntaxiquement valide mais vide n'est pas une erreur : un plan peut
    être vide. Il n'est simplement pas mesurable, faute d'unité et de contenu.
    Refuser ici reviendrait à refuser un fichier correct.
    """
    chemin = tmp_path / "vide.dxf"
    chemin.write_bytes(
        b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1009\n  0\nENDSEC\n  0\nEOF\n"
    )

    constat = lecture_dxf.lire(chemin)

    assert constat.refuse is False
    assert constat.mesurable is False
    assert "unite_absente" in [a.code for a in constat.anomalies]
    assert constat.cotations == []


# ---------------------------------------------------------------------------
# L'inventaire
# ---------------------------------------------------------------------------


def test_layers_sheets_and_entity_counts_are_reported(tmp_path: Path) -> None:
    """Ce qui permet à un humain de reconnaître son plan avant de mesurer."""
    doc = _document(4)
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "MURS"})
    msp.add_line((0, 0), (0, 1000), dxfattribs={"layer": "MURS"})
    msp.add_circle((500, 500), 100, dxfattribs={"layer": "POTEAUX"})
    chemin = tmp_path / "inventaire.dxf"
    doc.saveas(chemin)

    constat = lecture_dxf.lire(chemin)

    assert constat.calques["MURS"] == 2
    assert constat.calques["POTEAUX"] == 1
    assert constat.entites["LINE"] == 2
    assert constat.entites["CIRCLE"] == 1
    assert "Model" in constat.feuilles
    assert constat.version_dxf == "AC1027"
