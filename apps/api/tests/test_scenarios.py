"""Scénarios bas / probable / haut : le contrat, et ce qu'il ne fait pas.

Les valeurs attendues de ce fichier sont **calculées à la main** et écrites en
dur, jamais reprises du code : un test qui compare le moteur à lui-même passe
quoi qu'il arrive.

Le bordereau de référence, construit une fois et réutilisé :

* 100 m³, sous-détail à quatre composants ;
* `consumption` — 100 × 0,35 t/m³ × 1,05 = **36,75 t** × 18 EUR = **661,50** ;
* `output_rate` — 100 ÷ 12 = 8,3333… h × 2 = 16,6666… h × 45 = **750,00** ;
* `rotation` — 100 ÷ 8 = 12,5 → **13 rotations** × (85 + 30 × 1,20 = 121) = **1 573,00** ;
* `lump_sum` — **450,00**, quelle que soit la quantité.

Déboursé sec : 661,50 + 750,00 + 1 573,00 + 450,00 = **3 434,50 EUR**.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import scenarios
from metreo_api.services.estimating import inputs_from_specs
from metreo_domain.errors import OutOfBoundsError
from metreo_domain.estimate import compute_estimate
from metreo_domain.pricing import MarkupPolicy

from .conftest import login

#: Le sous-détail de référence, dans la forme que le moteur reçoit vraiment.
COMPOSANTS: list[dict[str, Any]] = [
    {
        "component_type": "consumption",
        "label": "Grave 0/32",
        "resource_kind": "material",
        "consumption": "0.35",
        "resource_unit_code": "t",
        "unit_price": "18",
        "loss_ratio": "0.05",
        "convert_boq_quantity": False,
        "density_value": None,
        "density_source": None,
    },
    {
        "component_type": "output_rate",
        "label": "Équipe de pose",
        "resource_kind": "labor",
        "output_rate": "12",
        "hourly_rate": "45",
        "crew_size": "2",
    },
    {
        "component_type": "rotation",
        "label": "Camion 8 m³",
        "resource_kind": "transport",
        "payload_value": "8",
        "payload_unit_code": "m3",
        "cost_per_rotation": "85",
        "round_up": True,
        "distance_km": "30",
        "rate_per_km": "1.20",
        "density_value": None,
        "density_source": None,
    },
    {
        "component_type": "lump_sum",
        "label": "Installation de chantier",
        "resource_kind": "other",
        "lump_sum_amount": "450",
    },
]

SPECS: list[dict[str, Any]] = [
    {
        "line_id": "L1",
        "position": "01.10",
        "code": "01.10",
        "designation": "Déblai et évacuation",
        "unit_code": "m3",
        "quantity": "100",
        "kind": "item",
        "status": "approved",
        "pricing": {
            "mode": "composite",
            "composite_code": "SD-TER",
            "composite_label": "Déblai mécanique",
            "components": COMPOSANTS,
        },
    }
]

#: Déboursé sec de référence, posé à la main d'après le docstring.
REFERENCE = Decimal("3434.50")


def _total(hyp: scenarios.Hypotheses, specs: list[dict[str, Any]] | None = None) -> Decimal:
    """Le total HT d'un scénario, sans frais ni marge, pour isoler l'effet."""
    resultat = compute_estimate(
        inputs_from_specs(scenarios.appliquer(specs or SPECS, hyp), "EUR"),
        currency="EUR",
        markup=MarkupPolicy(),
    )
    return resultat.total_selling_price_ht.amount


# --------------------------------------------------------------------------
# 1. Le neutre reproduit la référence — par construction
# --------------------------------------------------------------------------


def test_1_le_scenario_neutre_reproduit_exactement_la_reference() -> None:
    """Le socle de tout le reste. Sans hypothèse, aucune entrée n'est touchée.

    `appliquer` rend alors la liste REÇUE, sans copie ni arithmétique : le
    scénario neutre n'est pas une reproduction fidèle du calcul de référence,
    c'est le calcul de référence.
    """
    neutre = scenarios.Hypotheses()
    assert scenarios.appliquer(SPECS, neutre) is SPECS
    assert _total(neutre) == REFERENCE


def test_1bis_trois_scenarios_neutres_donnent_trois_fois_la_meme_chose() -> None:
    chiffrages = scenarios.evaluer(
        SPECS,
        {nom: scenarios.Hypotheses() for nom in scenarios.SCENARIOS},
        currency="EUR",
        markup=MarkupPolicy(),
        taxes=(),
        missing_price_policy=None,
    )
    totaux = {c.nom: c.resultat.total_selling_price_ht.amount for c in chiffrages}
    assert totaux == {"bas": REFERENCE, "probable": REFERENCE, "haut": REFERENCE}


# --------------------------------------------------------------------------
# 2. Le prix agit sur les ENTRÉES, pas sur le total
# --------------------------------------------------------------------------


def test_2_une_hausse_de_prix_touche_les_entrees_et_epargne_le_forfait() -> None:
    """+10 % sur les prix unitaires, et le forfait ne bouge pas.

    Attendu, calculé à la main :
      matériau 661,50 × 1,10 = 727,65  (+66,15)
      main-d'œuvre 750,00 × 1,10 = 825,00  (+75,00)
      transport 1 573,00 × 1,10 = 1 730,30  (+157,30)
      forfait 450,00 INCHANGÉ  (+0)
      total 3 732,95, soit +298,45

    Un total multiplié par 1,10 aurait donné 3 777,95 — 45,00 de plus, soit
    exactement les 10 % que le forfait n'a pas subis. C'est cet écart qui
    distingue « agir sur les entrées » de « majorer le résultat ».
    """
    total = _total(scenarios.Hypotheses(prix=Decimal("0.10")))
    assert total == Decimal("3732.95")
    assert total - REFERENCE == Decimal("298.45")
    assert total != REFERENCE * Decimal("1.10")


def test_2bis_une_variation_ciblee_ne_touche_que_sa_categorie() -> None:
    """+10 % sur les matériaux SEULS : 661,50 × 0,10 = 66,15, et rien d'autre."""
    total = _total(
        scenarios.Hypotheses(prix=Decimal("0.10"), prix_categories=("material",))
    )
    assert total - REFERENCE == Decimal("66.15")


@pytest.mark.parametrize(
    ("categorie", "attendu"),
    [
        ("material", Decimal("66.15")),
        ("labor", Decimal("75.00")),
        ("transport", Decimal("157.30")),
        # Un forfait n'a pas de prix unitaire : le viser ne produit rien.
        ("other", Decimal("0")),
    ],
)
def test_5_chaque_categorie_ne_deplace_que_sa_propre_part(
    categorie: str, attendu: Decimal
) -> None:
    """Et la somme des quatre parts vaut la variation générale.

    C'est le contrôle qui ferme la porte à un facteur qui « fuirait » d'une
    catégorie à l'autre : 66,15 + 75,00 + 157,30 + 0 = 298,45.
    """
    ecart = _total(
        scenarios.Hypotheses(prix=Decimal("0.10"), prix_categories=(categorie,))
    ) - REFERENCE
    assert ecart == attendu


# --------------------------------------------------------------------------
# 3. La productivité : le seul axe dont le signe s'inverse
# --------------------------------------------------------------------------


def test_3_une_hausse_de_productivite_fait_BAISSER_le_cout() -> None:
    """+10 % de rendement = moins d'heures = moins cher.

    Attendu, calculé à la main :
      rendement 12 × 1,10 = 13,2 m³/h
      heures 100 ÷ 13,2 = 7,5757…  × 2 (équipe) = 15,1515… h
      main-d'œuvre 15,1515… × 45 = 681,8181… EUR  (contre 750,00)
      écart -68,1818…

    C'est le seul endroit du contrat où l'hypothèse et son effet ont des signes
    opposés. Un lecteur qui supposerait « +10 % = plus cher » se tromperait, et
    c'est pour cela que ce test existe plutôt qu'un commentaire.
    """
    total = _total(scenarios.Hypotheses(productivite=Decimal("0.10")))
    assert total < REFERENCE, "une meilleure productivité doit coûter MOINS cher"

    attendu = Decimal("100") / (Decimal("12") * Decimal("1.10")) * Decimal("2") * Decimal("45")
    ecart = total - REFERENCE
    assert ecart.quantize(Decimal("0.0001")) == (attendu - Decimal("750")).quantize(
        Decimal("0.0001")
    )


def test_3bis_une_baisse_de_productivite_rencherit() -> None:
    """Et l'inverse tient : -20 % de rendement, donc plus d'heures.

    rendement 12 × 0,80 = 9,6 ; heures 100 ÷ 9,6 × 2 = 20,8333… ;
    main-d'œuvre 937,50 EUR, soit +187,50.
    """
    total = _total(scenarios.Hypotheses(productivite=Decimal("-0.20")))
    assert total - REFERENCE == Decimal("187.50")


def test_3ter_la_productivite_ne_touche_ni_le_materiau_ni_le_transport() -> None:
    """Elle ne concerne que les composants qui ont un rendement.

    Un sous-détail SANS `output_rate` ne bouge pas d'un centime, quelle que
    soit l'hypothèse de productivité.
    """
    sans_rendement = [
        {
            **SPECS[0],
            "pricing": {
                **SPECS[0]["pricing"],
                "components": [
                    c for c in COMPOSANTS if c["component_type"] != "output_rate"
                ],
            },
        }
    ]
    reference = _total(scenarios.Hypotheses(), sans_rendement)
    assert reference == REFERENCE - Decimal("750.00")
    assert _total(scenarios.Hypotheses(productivite=Decimal("0.50")), sans_rendement) == reference


# --------------------------------------------------------------------------
# 4. La distance traverse un nombre ENTIER de rotations
# --------------------------------------------------------------------------


def test_4_la_distance_passe_par_l_arrondi_des_rotations() -> None:
    """+10 % de distance, et l'effet n'est PAS proportionnel.

    Attendu, calculé à la main :
      rotations 100 ÷ 8 = 12,5 → 13 (arrondi au camion supérieur)
      distance 30 × 1,10 = 33 km
      coût par rotation 85 + 33 × 1,20 = 124,60  (contre 121,00)
      transport 13 × 124,60 = 1 619,80  (contre 1 573,00), soit +46,80

    +10 % de distance donne +2,98 % sur le transport, et +1,36 % sur le total.
    Rien de proportionnel : la partie fixe du coût de rotation ne bouge pas, et
    le nombre de rotations est un entier qui ne se met pas à l'échelle.
    """
    total = _total(scenarios.Hypotheses(distance=Decimal("0.10")))
    assert total - REFERENCE == Decimal("46.80")

    # 13 × (85 + 33 × 1,20), posé en toutes lettres.
    assert Decimal("13") * (Decimal("85") + Decimal("33") * Decimal("1.20")) == Decimal("1619.80")
    # La preuve du non-proportionnel : 10 % du transport vaudrait 157,30.
    assert Decimal("46.80") != Decimal("1573.00") * Decimal("0.10")


def test_4bis_l_arrondi_des_rotations_reste_un_palier() -> None:
    """La quantité qui fait basculer d'un camion se voit dans le résultat.

    96 m³ tiennent en 12 rotations pile ; 97 m³ en demandent 13. Le coût du
    transport fait donc un PALIER de 121 EUR entre les deux, et c'est bien ce
    palier que la distance multiplie ensuite.
    """

    def transport(quantite: str, hyp: scenarios.Hypotheses) -> Decimal:
        specs = [{**SPECS[0], "quantity": quantite}]
        return _total(hyp, specs)

    neutre = scenarios.Hypotheses()
    saut = transport("97", neutre) - transport("96", neutre)
    # Un camion de plus (121,00) ET la part variable des autres composants.
    assert saut > Decimal("121.00")

    # À 96 m³ — douze rotations pile — +10 % de distance vaut 12 × 3,60 = 43,20.
    ecart = transport("96", scenarios.Hypotheses(distance=Decimal("0.10"))) - transport(
        "96", neutre
    )
    assert ecart == Decimal("43.20")
