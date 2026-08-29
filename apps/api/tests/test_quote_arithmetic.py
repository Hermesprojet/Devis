"""Le devis remis au client s'additionne exactement.

Convention retenue — celle de la facturation : les nombres imprimés côte à côte
doivent s'accorder, parce que c'est de tête que le lecteur les vérifie.

    Total HT       = somme des totaux de ligne imprimés et inclus
    TVA d'un taux  = arrondi(taux x base taxable IMPRIMÉE de ce taux)
    Total TTC      = Total HT imprimé + TVA imprimées

Ces tests remplacent ceux qui verrouillaient le défaut. Ils ne bornent plus un
écart : ils exigent l'égalité.

Ce qui n'est **pas** exigé ici, et ne peut pas l'être : que la somme des TVA
imprimées poste par poste égale la TVA du pied. La TVA porte sur la base d'un
taux, pas sur chaque ligne isolément — c'est le traitement fiscal, et c'est ce
que la ligne « TVA 21 % » énonce.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import ROUND_HALF_UP, Decimal

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_estimating import price_the_missing_line

CENT = Decimal("0.01")


def _verifier_les_quatre_identites(payload: dict) -> None:
    """Les quatre identités, sur la charge utile que l'API rend.

    Regroupées ici parce qu'elles doivent tenir sur *tous* les cas ci-dessous,
    et qu'une seule d'entre elles vérifiée sur un seul jeu de données ne dirait
    presque rien.
    """
    lignes_incluses = [
        poste for poste in payload["lines"] if poste["included_in_total"] and poste["price"]
    ]
    somme_des_lignes = sum(
        (Decimal(poste["price"]["selling_price_ht"]) for poste in lignes_incluses),
        Decimal(0),
    )
    total_ht = Decimal(payload["total_selling_price_ht"])

    # 1. Le Total HT est la somme des lignes imprimées.
    assert total_ht == somme_des_lignes, (
        f"Total HT imprimé {total_ht}, somme des {len(lignes_incluses)} lignes "
        f"imprimées {somme_des_lignes}"
    )

    total_tva = Decimal(0)
    for taxe in payload["taxes"]:
        base = Decimal(taxe["taxable_base"])
        montant = Decimal(taxe["amount"])
        total_tva += montant

        # 2. La base taxable suit les mêmes règles d'inclusion.
        base_attendue = sum(
            (
                Decimal(poste["price"]["selling_price_ht"])
                for poste in lignes_incluses
                if any(t["code"] == taxe["code"] for t in poste["price"]["taxes"])
            ),
            Decimal(0),
        )
        assert base == base_attendue, (
            f"base taxable de {taxe['code']} : {base} au lieu de {base_attendue}"
        )

        # 3. La TVA est celle de sa base imprimée.
        attendu = (base * Decimal(taxe["rate"])).quantize(CENT, rounding=ROUND_HALF_UP)
        assert montant == attendu, (
            f"TVA {taxe['code']} imprimée {montant}, or {base} x {taxe['rate']} = {attendu}"
        )

    # 4. Le TTC est exactement HT + TVA, tels qu'imprimés.
    assert Decimal(payload["total_ttc"]) == total_ht + total_tva, (
        f"TTC imprimé {payload['total_ttc']}, or {total_ht} + {total_tva} = {total_ht + total_tva}"
    )


@pytest.fixture()
def devis_calcule(seeded_client: TestClient) -> dict:
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    reponse = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
        headers=headers,
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["result"]


def test_the_demo_quote_adds_up(devis_calcule: dict) -> None:
    """Le jeu de démonstration : huit postes chiffrés, un taux de TVA."""
    chiffres = [
        poste for poste in devis_calcule["lines"] if poste["included_in_total"] and poste["price"]
    ]
    assert len(chiffres) >= 5, (
        f"seulement {len(chiffres)} postes chiffrés : le jeu de démonstration a "
        "changé et ce test ne mesure plus rien"
    )
    _verifier_les_quatre_identites(devis_calcule)


def _resultat(montants: tuple[str, ...], taux: tuple[tuple[str, str], ...]) -> dict:
    from metreo_domain.estimate import EstimateLineInput, LineKind, compute_estimate
    from metreo_domain.money import DEFAULT_ROUNDING, Money
    from metreo_domain.pricing import MarkupPolicy, TaxRate
    from metreo_domain.units import Quantity

    taxes = tuple(
        TaxRate(code=code, label=f"TVA {code}", rate=Decimal(valeur)) for code, valeur in taux
    )
    lignes = tuple(
        EstimateLineInput(
            line_id=f"L{rang}",
            code=str(rang),
            designation=f"Poste {rang}",
            quantity=Quantity.of("1", "fft"),
            kind=LineKind.ITEM,
            unit_price=Money(Decimal(montant), "EUR"),
        )
        for rang, montant in enumerate(montants)
    )
    return compute_estimate(
        lines=lignes, currency="EUR", markup=MarkupPolicy(), taxes=taxes
    ).to_dict(DEFAULT_ROUNDING)


def test_the_three_line_case_that_broke_the_vat_identity() -> None:
    """Le contre-exemple minimal, celui qui a justifié la décision.

    Trois forfaits portant une fraction de centime : le pied annonçait une TVA
    de 63,01 sur une base de 300,02, alors que 21 % de 300,02 font 63,00.
    """
    payload = _resultat(("100.005", "100.0083", "100.0116"), (("VAT-BE-21", "0.21"),))
    _verifier_les_quatre_identites(payload)

    assert payload["total_selling_price_ht"] == "300.03", payload
    assert payload["taxes"][0]["amount"] == "63.01", payload
    assert payload["total_ttc"] == "363.04", payload


@pytest.mark.parametrize("nombre", [3, 8, 50, 200, 500])
def test_a_long_bill_of_quantities_still_adds_up(nombre: int) -> None:
    """L'écart croissait avec le nombre de postes : jusqu'à 1,50 EUR à 500.

    C'est la taille où le défaut devenait visible pour un maître d'ouvrage, et
    c'est donc la taille à laquelle l'identité doit tenir.
    """
    montants = tuple(f"{100 + rang}.{(rang * 7) % 100:02d}7" for rang in range(1, nombre + 1))
    _verifier_les_quatre_identites(_resultat(montants, (("VAT-BE-21", "0.21"),)))


def test_several_vat_rates_each_get_their_own_base() -> None:
    """Deux taux sur les mêmes postes : chacun sur sa base imprimée.

    Sans ce cas, une implémentation qui calculerait toutes les TVA sur le
    Total HT global passerait — ce qui serait faux dès qu'un poste relève d'un
    taux réduit.
    """
    payload = _resultat(
        ("100.005", "250.333", "1000.0049"),
        (("VAT-BE-21", "0.21"), ("VAT-BE-6", "0.06")),
    )
    _verifier_les_quatre_identites(payload)
    assert len(payload["taxes"]) == 2, payload["taxes"]


def test_options_are_priced_but_left_out_of_the_total_and_its_base() -> None:
    """Une option est chiffrée, elle n'entre ni dans le HT ni dans la base.

    C'est la sémantique existante, et elle ne doit pas changer : le Total HT
    reste celui du marché de base, et la TVA porte sur cette base-là.
    """
    from metreo_domain.estimate import EstimateLineInput, LineKind, compute_estimate
    from metreo_domain.money import DEFAULT_ROUNDING, Money
    from metreo_domain.pricing import MarkupPolicy, TaxRate
    from metreo_domain.units import Quantity

    def ligne(rang: int, montant: str, kind: LineKind) -> EstimateLineInput:
        return EstimateLineInput(
            line_id=f"L{rang}",
            code=str(rang),
            designation=f"Poste {rang}",
            quantity=Quantity.of("1", "fft"),
            kind=kind,
            unit_price=Money(Decimal(montant), "EUR"),
        )

    tva = TaxRate(code="VAT-BE-21", label="TVA 21 %", rate=Decimal("0.21"))
    payload = compute_estimate(
        lines=(
            ligne(0, "100.005", LineKind.ITEM),
            ligne(1, "200.0083", LineKind.ITEM),
            ligne(2, "999.999", LineKind.OPTION),
            ligne(3, "888.888", LineKind.VARIANT),
        ),
        currency="EUR",
        markup=MarkupPolicy(),
        taxes=(tva,),
    ).to_dict(DEFAULT_ROUNDING)

    _verifier_les_quatre_identites(payload)

    # Les deux postes de base seulement : 100,01 + 200,01.
    assert payload["total_selling_price_ht"] == "300.02", payload
    assert payload["taxes"][0]["taxable_base"] == "300.02", payload
    # Et l'option reste chiffrée, à part.
    assert payload["options_total_ht"] != "0.00", payload


def test_the_csv_prints_exactly_those_four_numbers(seeded_client: TestClient) -> None:
    """Le document réellement remis, pas seulement la charge utile."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)

    export = seeded_client.get(
        f"{'/api/v1/estimates'}/{estimate['id']}/versions/{version['id']}/export.csv",
        headers=headers,
    )
    assert export.status_code == 200, export.text
    document = list(csv.reader(io.StringIO(export.text.lstrip("﻿")), delimiter=";"))

    depart = next(n for n, ligne in enumerate(document) if ligne and ligne[0] == "Poste")
    entetes = document[depart]
    colonne_total = entetes.index("Total HT")
    colonne_statut = len(entetes) - 1

    somme = Decimal(0)
    for ligne in document[depart + 1 :]:
        if not ligne or not ligne[0]:
            break
        valeur = ligne[colonne_total].strip()
        statut = ligne[colonne_statut].strip() if len(ligne) > colonne_statut else ""
        if not valeur or "HORS TOTAL" in statut:
            continue
        somme += Decimal(valeur)

    pied: dict[str, Decimal] = {}
    tva_totale = Decimal(0)
    for ligne in document:
        if len(ligne) < 2 or not ligne[1].strip():
            continue
        if ligne[0] in {"Total HT", "Total TTC"}:
            pied[ligne[0]] = Decimal(ligne[1])
        elif ligne[0].startswith("TVA"):
            tva_totale += Decimal(ligne[1])

    assert pied["Total HT"] == somme, (
        f"le CSV imprime un Total HT de {pied['Total HT']} pour une somme de lignes de {somme}"
    )
    assert pied["Total TTC"] == pied["Total HT"] + tva_totale, (
        f"le CSV imprime {pied['Total TTC']} au lieu de {pied['Total HT'] + tva_totale}"
    )


def test_the_printable_quote_prints_exactly_those_four_numbers(
    seeded_client: TestClient,
) -> None:
    """L'aperçu HTML est le document effectivement remis au client."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)

    apercu = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/quote.html",
        headers=headers,
    )
    assert apercu.status_code == 200, apercu.text
    html = apercu.text

    def montant(libelle: str) -> Decimal:
        motif = rf"<td>{re.escape(libelle)}</td><td class=\"num\">([\d.]+)"
        trouve = re.search(motif, html)
        assert trouve, f"« {libelle} » absent de l'aperçu"
        return Decimal(trouve.group(1))

    tva = sum(
        (Decimal(m) for m in re.findall(r"<td>TVA[^<]*</td><td class=\"num\">([\d.]+)", html)),
        Decimal(0),
    )
    assert tva > 0, "l'aperçu ne porte plus de TVA"
    assert montant("Total TTC") == montant("Total HT") + tva


def test_a_frozen_quote_keeps_its_digest_while_its_display_changes(
    seeded_client: TestClient,
) -> None:
    """Ce que le changement d'affichage fait — et ne fait pas — aux devis gelés.

    Les montants **affichés** d'un devis gelé avant ce changement suivent la
    nouvelle convention : `recompute_from_snapshot` rejoue le moteur sur les
    *entrées* de l'instantané, et c'est `to_dict` qui met en forme. Un devis
    figé hier peut donc afficher un Total HT différent d'un centime demain.

    Ce qui ne bouge pas, et que ce test verrouille :

      - `snapshot_sha256` — l'empreinte porte sur l'instantané **stocké**, que
        rien ne réécrit ;
      - `total_selling_price_ht` et `total_ttc` en base, qui sont les valeurs
        **brutes** non arrondies ;
      - `total_selling_price_ht_raw`, qui reste la somme exacte.

    Un devis gelé reste donc comparable à lui-même, et vérifiable.
    """
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)

    gel = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True, "label": None},
    )
    assert gel.status_code == 200, gel.text
    empreinte = gel.json()["snapshot_sha256"]
    brut_ht = Decimal(gel.json()["total_selling_price_ht"])
    assert empreinte and len(empreinte) == 64

    # Relu depuis l'instantané : les identités tiennent aussi là.
    relu = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
        headers=headers,
    )
    assert relu.status_code == 200, relu.text
    assert relu.json()["from_snapshot"] is True
    payload = relu.json()["result"]
    _verifier_les_quatre_identites(payload)

    # L'empreinte et les valeurs brutes n'ont pas bougé.
    apres = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    assert apres["snapshot_sha256"] == empreinte

    # La colonne est un NUMERIC(28, 10) : elle garde dix décimales, quand la
    # valeur calculée en porte davantage. C'est la précision du stockage, elle
    # ne dépend pas de la convention d'affichage — on compare donc à ce
    # niveau-là plutôt que d'exiger une égalité que la base ne promet pas.
    dix_decimales = Decimal("0.0000000001")
    stocke = Decimal(apres["total_selling_price_ht"]).quantize(dix_decimales)
    assert stocke == brut_ht.quantize(dix_decimales)
    assert Decimal(payload["total_selling_price_ht_raw"]).quantize(dix_decimales) == stocke

    # Et le total imprimé s'écarte du brut : c'est le prix assumé de la
    # cohérence, pas un calcul faux.
    ecart = abs(Decimal(payload["total_selling_price_ht"]) - brut_ht)
    assert ecart < Decimal("1"), f"écart de {ecart} entre le total imprimé et le brut"


# -- la liste des versions annonce le nombre du document -------------------


def test_the_version_list_shows_the_document_total_not_the_rounded_raw(
    seeded_client: TestClient,
) -> None:
    """Un seul total affiché, et c'est celui du devis.

    C'est le point qui bloquait cette PR : la liste des versions arrondissait
    le total **brut**, et annonçait donc `99 097,08` là où le devis imprime
    `99 097,07`. Deux nombres « affichés » pour une même version, produits par
    la même API.
    """
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)

    gel = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True, "label": None},
    )
    assert gel.status_code == 200, gel.text

    calcul = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation",
        headers=headers,
    ).json()["result"]
    liste = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]

    assert liste["document_totals_available"] is True
    assert liste["total_selling_price_ht_display"] == calcul["total_selling_price_ht"]
    assert liste["total_ttc_display"] == calcul["total_ttc"]

    # Et ce n'est pas l'arrondi du brut : les deux diffèrent bien, sinon ce
    # test passerait sans rien prouver.
    brut = Decimal(liste["total_selling_price_ht"])
    assert Decimal(liste["total_selling_price_ht_display"]) != brut.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ), "brut arrondi et total imprimé coïncident : le cas ne discrimine plus rien"


def test_the_csv_and_the_html_agree_with_the_version_list(
    seeded_client: TestClient,
) -> None:
    """Les quatre lecteurs — liste, calcul, CSV, aperçu — disent la même chose."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    base = f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}"
    seeded_client.post(f"{base}/freeze", headers=headers, json={"confirm": True, "label": None})

    liste = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    attendu = liste["total_selling_price_ht_display"]

    csv_texte = seeded_client.get(f"{base}/export.csv", headers=headers).text.lstrip("﻿")
    pied = [
        ligne
        for ligne in csv.reader(io.StringIO(csv_texte), delimiter=";")
        if ligne and ligne[0] == "Total HT"
    ]
    assert pied and pied[0][1] == attendu, f"CSV : {pied} ≠ {attendu}"

    html = seeded_client.get(f"{base}/quote.html", headers=headers).text
    trouve = re.search(r"<td>Total HT</td><td class=\"num\">([\d.]+)", html)
    assert trouve and trouve.group(1) == attendu, f"aperçu : {trouve} ≠ {attendu}"


def test_a_frozen_total_survives_a_later_price_change(seeded_client: TestClient) -> None:
    """Le document figé ne bouge plus, même si les prix bougent après.

    C'est la raison d'être de la persistance : sans elle, le total affiché
    serait recalculé à chaque lecture, et un devis déjà remis changerait de
    montant.
    """
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    base = f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}"
    seeded_client.post(f"{base}/freeze", headers=headers, json={"confirm": True, "label": None})

    avant = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]["total_selling_price_ht_display"]

    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    lot = seeded_client.post(
        f"/api/v1/price-books/versions/{pb_version}/imports/preview",
        headers=headers,
        files={
            "file": (
                "hausse.csv",
                b"code;libelle;unite;prix_unitaire\n"
                b"MAT-TUY-160;Tuyau beton non arme DN 400;m;146,25\n",
                "text/csv",
            )
        },
    ).json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{lot['batch_id']}/commit",
        headers=headers,
        json={"strategy": "replace", "confirm": True},
    )

    apres = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]["total_selling_price_ht_display"]
    assert apres == avant, f"le total figé a bougé : {avant} -> {apres}"


def test_a_draft_version_has_no_frozen_document_total(seeded_client: TestClient) -> None:
    """Un brouillon n'a pas de document figé, et ne prétend pas en avoir un."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]

    assert version["status"] == "draft"
    assert version["document_totals_available"] is False
    assert version["document_total_ht"] is None


def test_a_legacy_frozen_version_shows_no_total_rather_than_a_wrong_one(
    seeded_client: TestClient,
) -> None:
    """`NULL` veut dire « inconnu », jamais « voici l'arrondi du brut ».

    On simule une version gelée avant l'introduction des colonnes : ses totaux
    bruts sont là, son document ne l'est pas. La liste doit afficher une
    absence — un maître d'ouvrage ne doit pas lire un montant qui n'est pas
    celui de son devis.
    """
    from sqlalchemy import select

    from metreo_api.db import get_session_factory
    from metreo_api.models import EstimateVersion

    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    base = f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}"
    seeded_client.post(f"{base}/freeze", headers=headers, json={"confirm": True, "label": None})

    with get_session_factory()() as session:
        ligne = session.scalar(select(EstimateVersion).where(EstimateVersion.id == version["id"]))
        assert ligne is not None
        brut = ligne.total_selling_price_ht
        ligne.document_total_ht = None
        ligne.document_total_ttc = None
        session.commit()

    relu = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    assert relu["document_totals_available"] is False
    assert relu["total_selling_price_ht_display"] is None
    assert relu["total_ttc_display"] is None
    # Le brut, lui, n'a pas été touché : aucune donnée n'est perdue.
    assert Decimal(relu["total_selling_price_ht"]) == brut
