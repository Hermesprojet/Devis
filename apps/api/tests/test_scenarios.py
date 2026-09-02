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

from . import emission
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
    total = _total(scenarios.Hypotheses(prix=Decimal("0.10"), prix_categories=("material",)))
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
def test_5_chaque_categorie_ne_deplace_que_sa_propre_part(categorie: str, attendu: Decimal) -> None:
    """Et la somme des quatre parts vaut la variation générale.

    C'est le contrôle qui ferme la porte à un facteur qui « fuirait » d'une
    catégorie à l'autre : 66,15 + 75,00 + 157,30 + 0 = 298,45.
    """
    ecart = (
        _total(scenarios.Hypotheses(prix=Decimal("0.10"), prix_categories=(categorie,))) - REFERENCE
    )
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
                "components": [c for c in COMPOSANTS if c["component_type"] != "output_rate"],
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


# --------------------------------------------------------------------------
# 8. Zéro, négatif, extrême, hors borne
# --------------------------------------------------------------------------


def test_8_zero_est_neutre_et_le_negatif_fait_baisser() -> None:
    assert _total(scenarios.Hypotheses(prix=Decimal("0"))) == REFERENCE
    assert _total(scenarios.Hypotheses(prix=Decimal("-0.10"))) < REFERENCE
    # -10 % : l'exact miroir de +10 %, forfait toujours épargné.
    assert REFERENCE - _total(scenarios.Hypotheses(prix=Decimal("-0.10"))) == Decimal("298.45")


@pytest.mark.parametrize(
    ("axe", "valeur", "pourquoi"),
    [
        ("productivite", "-1", "un rendement nul est un diviseur nul"),
        ("prix", "-1", "la borne est la même pour les trois axes"),
        ("distance", "-1.5", "au-delà de -100 %, une distance négative"),
        ("prix", "11", "au-delà de +1000 %, c'est une faute de saisie"),
    ],
)
def test_8bis_une_hypothese_hors_borne_est_refusee_en_la_nommant(
    axe: str, valeur: str, pourquoi: str
) -> None:
    """La borne basse est STRICTEMENT exclue, et c'est le point qui compte.

    `-1` vaut « -100 % ». Appliqué à un rendement, il le met à zéro — le moteur
    lèverait alors une division par zéro trois couches plus bas, et
    l'utilisateur chercherait la faute dans son bordereau.
    """
    with pytest.raises(OutOfBoundsError) as refus:
        scenarios.hypotheses_depuis({axe: valeur})
    assert axe in str(refus.value), pourquoi


@pytest.mark.parametrize("brut", ["abc", "1,2,3", float("inf"), float("nan")])
def test_8ter_une_hypothese_illisible_est_refusee_sans_remonter_en_500(brut: Any) -> None:
    with pytest.raises(scenarios.HypotheseRefusee) as refus:
        scenarios.hypotheses_depuis({"prix": brut})
    assert refus.value.code == "hypothese_illisible"


def test_8quater_une_categorie_inconnue_est_refusee_en_listant_celles_qui_existent() -> None:
    with pytest.raises(scenarios.HypotheseRefusee) as refus:
        scenarios.hypotheses_depuis({"prix": "0.1", "prix_categories": ["beton"]})
    assert refus.value.code == "categorie_inconnue"
    assert "material" in str(refus.value.context["categories"])


def test_8quinquies_une_valeur_extreme_admise_reste_calculable() -> None:
    """+1000 % est dans la borne : la simulation doit rendre un nombre, pas une erreur."""
    total = _total(scenarios.Hypotheses(prix=Decimal("10")))
    # Les trois composants tarifés × 11, le forfait inchangé.
    attendu = (Decimal("661.50") + Decimal("750") + Decimal("1573")) * 11 + Decimal("450")
    assert total == attendu


# --------------------------------------------------------------------------
# 7. Un poste sans prix reste sans prix
# --------------------------------------------------------------------------


def test_7_un_poste_sans_prix_n_est_jamais_valorise_a_zero() -> None:
    """La simulation ne doit pas faire disparaître ce qui bloque le gel.

    Un poste sans tarif reste hors total, nommé dans `missing_price_line_ids`,
    et le drapeau `blocking` le suit. Le compter pour zéro rendrait un scénario
    optimiste qui masquerait précisément le trou du chiffrage.
    """
    avec_trou = [
        *SPECS,
        {
            "line_id": "L2",
            "position": "01.20",
            "code": "01.20",
            "designation": "Poste non chiffré",
            "unit_code": "m3",
            "quantity": "50",
            "kind": "item",
            "status": "proposed",
            "pricing": None,
        },
    ]
    for hyp in (scenarios.Hypotheses(), scenarios.Hypotheses(prix=Decimal("0.30"))):
        resultat = compute_estimate(
            inputs_from_specs(scenarios.appliquer(avec_trou, hyp), "EUR"),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        assert resultat.missing_price_line_ids == ("L2",)
        assert resultat.blocking is True

    # Et le poste sans prix n'a pas été modifié au passage.
    modifie = scenarios.appliquer(avec_trou, scenarios.Hypotheses(prix=Decimal("0.30")))
    assert modifie[1]["pricing"] is None


# --------------------------------------------------------------------------
# La route : isolation, permissions, instantané, absence de mutation
# --------------------------------------------------------------------------


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _premiere_version(client: TestClient, entetes: dict[str, str]) -> tuple[str, str]:
    estimation = client.get("/api/v1/estimates", headers=entetes).json()[0]
    version = client.get(f"/api/v1/estimates/{estimation['id']}/versions", headers=entetes).json()[
        0
    ]
    return estimation["id"], version["id"]


def _boq(client: TestClient, entetes: dict[str, str], estimation: str) -> str:
    detail = client.get(f"/api/v1/estimates/{estimation}", headers=entetes).json()
    return str(detail["boq_id"])


def _simuler(
    client: TestClient,
    entetes: dict[str, str],
    estimation: str,
    version: str,
    corps: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        f"/api/v1/estimates/{estimation}/versions/{version}/scenarios",
        headers=entetes,
        json=corps if corps is not None else {},
    )


def test_la_route_rend_trois_scenarios_et_leurs_ecarts(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    estimation, version = _premiere_version(seeded_client, admin)
    reponse = _simuler(
        seeded_client,
        admin,
        estimation,
        version,
        {
            "bas": {"prix": "-0.05"},
            "probable": {},
            "haut": {"prix": "0.05", "distance": "0.10"},
        },
    )
    assert reponse.status_code == 200, reponse.text
    rendu = reponse.json()

    assert [s["nom"] for s in rendu["scenarios"]] == ["bas", "probable", "haut"]
    par_nom = {s["nom"]: s for s in rendu["scenarios"]}

    # Le probable est la référence : son écart à lui-même est nul.
    # « 0 » et non « 0.00 » : `canonical_text` donne UNE orthographe par
    # valeur, indépendante du moteur de stockage. C'est la même règle qui fait
    # voyager une quantité PostgreSQL et SQLite sous la même forme.
    assert par_nom["probable"]["ecart"]["absolu"] == "0"
    assert par_nom["probable"]["ecart"]["pourcentage"] == "0"
    # Les deux autres s'écartent dans le sens attendu de leurs hypothèses.
    assert Decimal(par_nom["bas"]["ecart"]["absolu"]) < 0
    assert Decimal(par_nom["haut"]["ecart"]["absolu"]) > 0
    assert rendu["ordre_incoherent"] is False


def test_des_libelles_qui_mentent_sont_SIGNALES_et_non_reordonnes(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """« Bas » plus cher que « haut » : signalé, jamais corrigé.

    Réordonner les colonnes ferait disparaître l'information la plus utile :
    que les hypothèses saisies ne disent pas ce que leur nom laisse croire.
    """
    estimation, version = _premiere_version(seeded_client, admin)
    rendu = _simuler(
        seeded_client,
        admin,
        estimation,
        version,
        {"bas": {"prix": "0.30"}, "probable": {}, "haut": {"prix": "-0.30"}},
    ).json()

    assert rendu["ordre_incoherent"] is True
    # Et l'ordre RENDU reste bas / probable / haut : rien n'a été permuté.
    assert [s["nom"] for s in rendu["scenarios"]] == ["bas", "probable", "haut"]
    par_nom = {s["nom"]: s for s in rendu["scenarios"]}
    assert Decimal(par_nom["bas"]["ecart"]["absolu"]) > Decimal(par_nom["haut"]["ecart"]["absolu"])


def test_9_une_organisation_ne_simule_jamais_l_estimation_d_une_autre(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """404, pas 403 : confirmer l'existence serait déjà une fuite."""
    estimation, version = _premiere_version(seeded_client, admin)
    voisin = login(seeded_client, "admin@janssens.demo")

    refus = _simuler(seeded_client, voisin, estimation, version)
    assert refus.status_code == 404, refus.text


def test_10_un_role_sans_cost_read_ne_simule_pas(seeded_client: TestClient) -> None:
    """Comparer des scénarios, c'est lire des déboursés.

    Le refus NOMME la permission manquante : un 403 muet enverrait chercher la
    cause du côté de l'estimation.
    """
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    estimation, version = _premiere_version(seeded_client, lecteur)

    refus = _simuler(seeded_client, lecteur, estimation, version)
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "cost:read"


def test_10bis_les_totaux_rendus_ne_portent_aucun_taux_commercial(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Les taux de marge vivent dans les réglages et ne sortent pas d'ici.

    Le contrôle porte sur le texte BRUT de la réponse : une clé imbriquée
    échapperait à une vérification faite sur le seul premier niveau.
    """
    estimation, version = _premiere_version(seeded_client, admin)
    brut = _simuler(seeded_client, admin, estimation, version).text
    for interdit in ("margin_rate", "margin_method", "site_overheads_rate"):
        assert interdit not in brut, f"« {interdit} » ne doit pas sortir d'ici"


def test_11_une_version_gelee_simule_sur_SON_instantane(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """La bibliothèque peut changer : le scénario d'une version gelée, non.

    C'est l'invariant qui rend un devis remis défendable. Le prix de la
    bibliothèque est TRIPLÉ entre les deux simulations ; si le second résultat
    bougeait, la simulation lirait les tables vivantes au lieu de l'instantané.
    """
    estimation, version = _premiere_version(seeded_client, admin)
    # Le jeu de démonstration porte VOLONTAIREMENT une ligne sans prix, pour
    # que le refus de gel soit éprouvé ailleurs. Ici, elle est chiffrée : le
    # sujet est l'instantané, pas la politique de prix manquant.
    emission.prix_manquant(
        seeded_client, admin, {"id": estimation, "boq_id": _boq(seeded_client, admin, estimation)}
    )

    gel = seeded_client.post(
        f"/api/v1/estimates/{estimation}/versions/{version}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text

    avant = _simuler(seeded_client, admin, estimation, version, {"haut": {"prix": "0.10"}}).json()
    assert avant["from_snapshot"] is True

    # La bibliothèque bouge, franchement.
    livre = seeded_client.get("/api/v1/price-books", headers=admin).json()[0]
    version_prix = seeded_client.get(
        f"/api/v1/price-books/{livre['id']}/versions", headers=admin
    ).json()[0]["id"]
    hausse = "code;libelle;unite;prix_unitaire\nMAT-TUY-160;Tuyau béton;m;438,75\n".encode()
    lot = seeded_client.post(
        f"/api/v1/price-books/versions/{version_prix}/imports/preview",
        headers=admin,
        files={"file": ("hausse.csv", hausse, "text/csv")},
    ).json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{lot['batch_id']}/commit",
        headers=admin,
        json={"strategy": "replace", "confirm": True},
    )

    apres = _simuler(seeded_client, admin, estimation, version, {"haut": {"prix": "0.10"}}).json()
    assert apres["scenarios"] == avant["scenarios"], (
        "un scénario sur version gelée a bougé quand la bibliothèque a changé"
    )


def test_12_simuler_ne_modifie_AUCUNE_ligne_ni_aucun_devis(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """La preuve d'absence de mutation, prise sur les objets eux-mêmes.

    Version, totaux gelés, empreinte de l'instantané, journal d'audit, et les
    OCTETS du PDF déjà émis : tout est relevé avant, puis après une rafale de
    simulations. Une simulation qui laisserait une trace cesserait d'en être une.
    """
    from hashlib import sha256

    estimation, version = _premiere_version(seeded_client, admin)
    emission.prix_manquant(
        seeded_client, admin, {"id": estimation, "boq_id": _boq(seeded_client, admin, estimation)}
    )
    gel = seeded_client.post(
        f"/api/v1/estimates/{estimation}/versions/{version}/freeze",
        headers=admin,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text

    # Les gestes qui mènent à une émission vivent déjà dans `emission` : les
    # refaire ici en donnerait une seconde version, qui divergerait.
    detail = seeded_client.get(f"/api/v1/estimates/{estimation}", headers=admin).json()
    fiche = emission.fiche(seeded_client, admin)
    emission.rattacher(seeded_client, admin, detail["project_id"], fiche["id"])

    remise = seeded_client.post(
        f"/api/v1/estimates/{estimation}/versions/{version}/issue",
        headers=admin,
        json={"valid_until": "2027-12-31"},
    )
    assert remise.status_code == 201, remise.text
    devis = remise.json()

    def empreinte_du_pdf() -> str:
        octets = seeded_client.get(
            f"/api/v1/issued-quotes/{devis['id']}/document.pdf", headers=admin
        ).content
        return sha256(octets).hexdigest()

    # L'ordre des relevés compte. Télécharger un PDF est LUI-MÊME audité :
    # prendre l'empreinte entre les deux lectures du journal ferait constater
    # une différence que la simulation n'a pas causée — l'instrument
    # perturberait la mesure. Le PDF est donc relevé d'abord, le journal
    # ensuite, et seules les simulations séparent les deux lectures du journal.
    pdf_avant = empreinte_du_pdf()

    def etat() -> dict[str, str]:
        return {
            "version": seeded_client.get(
                f"/api/v1/estimates/{estimation}/versions", headers=admin
            ).text,
            "devis": seeded_client.get(f"/api/v1/issued-quotes/{devis['id']}", headers=admin).text,
            "audit": seeded_client.get("/api/v1/audit/events?limit=200", headers=admin).text,
        }

    avant = etat()
    for hypothese in ({"prix": "0.25"}, {"productivite": "-0.30"}, {"distance": "0.75"}):
        reponse = _simuler(seeded_client, admin, estimation, version, {"haut": hypothese})
        assert reponse.status_code == 200, reponse.text
    apres = etat()

    for quoi in avant:
        assert avant[quoi] == apres[quoi], f"la simulation a modifié : {quoi}"

    # Et les OCTETS du devis déjà remis, à l'identique.
    assert empreinte_du_pdf() == pdf_avant, "le PDF déjà émis a changé"


def test_12bis_la_chaine_d_audit_reste_valide_apres_une_rafale(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Aucun événement n'a été ajouté, et la chaîne le confirme elle-même."""
    estimation, version = _premiere_version(seeded_client, admin)
    for _ in range(5):
        _simuler(seeded_client, admin, estimation, version, {"haut": {"prix": "0.1"}})

    verif = seeded_client.get("/api/v1/audit/verify", headers=admin)
    assert verif.status_code == 200, verif.text
    assert verif.json()["valid"] is True
