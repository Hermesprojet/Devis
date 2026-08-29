"""Les nombres imprimés sur un devis ne s'additionnent pas — défaut verrouillé.

**Ces tests ne décrivent pas une propriété souhaitée.** Ils reproduisent un
défaut mesuré et bornent son ampleur, pour qu'il ne s'aggrave pas en silence en
attendant qu'une convention soit tranchée. Voir `docs/ARRONDI_DES_DOCUMENTS.md`.

Deux identités que le lecteur d'un devis vérifie de tête sont fausses :

    somme des totaux de ligne imprimés  ≠  Total HT imprimé
    Total HT imprimé + TVA imprimée     ≠  Total TTC imprimé

La cause est unique : `EstimateResult.to_dict` arrondit chaque montant
indépendamment à partir de sa valeur exacte, si bien que chaque nombre est le
bon arrondi de sa propre valeur, et que leur mise côte à côte se contredit.

Si la convention est tranchée — total égal à la somme des lignes imprimées, ou
ligne d'écart d'arrondi — **ces tests doivent être supprimés** et remplacés par
les identités correspondantes. Leurs messages d'échec le rappellent.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_estimating import price_the_missing_line

#: Au-delà, l'écart n'est plus un artefact d'arrondi de fin de chaîne mais une
#: erreur de calcul, et ce test doit rougir.
ECART_MAXIMUM_ATTENDU = Decimal("0.05")

_A_CORRIGER = (
    "Si l'incohérence a été corrigée, supprimez ce test et remplacez-le par "
    "l'identité correspondante ; mettez à jour docs/ARRONDI_DES_DOCUMENTS.md."
)


@pytest.fixture()
def devis_exporte(seeded_client: TestClient) -> list[list[str]]:
    """Le devis de démonstration, chiffré, tel que le CSV l'imprime."""
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)

    reponse = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv",
        headers=headers,
    )
    assert reponse.status_code == 200, reponse.text
    # Point-virgule et BOM : le CSV vise Excel en configuration francophone.
    return list(csv.reader(io.StringIO(reponse.text.lstrip("﻿")), delimiter=";"))


def _somme_des_lignes(document: list[list[str]]) -> tuple[Decimal, int]:
    """Additionne les totaux de ligne tels qu'ils sont imprimés."""
    depart = next(n for n, ligne in enumerate(document) if ligne and ligne[0] == "Poste")
    entetes = document[depart]
    colonne_total = entetes.index("Total HT")
    colonne_statut = len(entetes) - 1

    somme = Decimal("0")
    comptees = 0
    for ligne in document[depart + 1 :]:
        if not ligne or not ligne[0]:
            break
        valeur = ligne[colonne_total].strip()
        statut = ligne[colonne_statut].strip() if len(ligne) > colonne_statut else ""
        # Les options et variantes sont chiffrées mais hors total, et les
        # sections ne portent aucun montant.
        if not valeur or "HORS TOTAL" in statut:
            continue
        somme += Decimal(valeur)
        comptees += 1
    return somme, comptees


def _totaux(document: list[list[str]]) -> dict[str, Decimal]:
    releve: dict[str, Decimal] = {}
    for ligne in document:
        if len(ligne) >= 2 and ligne[0] in {"Total HT", "Total TTC"}:
            releve[ligne[0]] = Decimal(ligne[1])
        elif len(ligne) >= 2 and ligne[0].startswith("TVA"):
            releve["TVA"] = releve.get("TVA", Decimal("0")) + Decimal(ligne[1])
    return releve


def test_the_printed_lines_do_not_add_up_to_the_printed_total(
    devis_exporte: list[list[str]],
) -> None:
    somme, comptees = _somme_des_lignes(devis_exporte)
    assert comptees >= 5, (
        f"seulement {comptees} lignes chiffrées relevées : le devis de "
        "démonstration a changé et ce test ne mesure plus rien"
    )
    total = _totaux(devis_exporte)["Total HT"]
    ecart = somme - total

    assert ecart != 0, f"Les lignes s'additionnent désormais au total. {_A_CORRIGER}"
    assert abs(ecart) <= ECART_MAXIMUM_ATTENDU, (
        f"L'écart d'arrondi atteint {ecart}, au-delà de "
        f"{ECART_MAXIMUM_ATTENDU} : ce n'est plus une fin de chaîne, "
        "c'est un calcul faux."
    )


def test_the_printed_ht_and_vat_do_not_add_up_to_the_printed_ttc(
    devis_exporte: list[list[str]],
) -> None:
    """L'identité la plus visible des trois : HT + TVA = TTC."""
    releve = _totaux(devis_exporte)
    attendu = releve["Total HT"] + releve["TVA"]
    ecart = attendu - releve["Total TTC"]

    assert ecart != 0, f"Le TTC est désormais la somme des montants imprimés. {_A_CORRIGER}"
    assert abs(ecart) <= ECART_MAXIMUM_ATTENDU, (
        f"HT imprimé + TVA imprimée = {attendu}, TTC imprimé = "
        f"{releve['Total TTC']} : écart de {ecart}."
    )


def test_the_gap_is_systematic_and_grows_with_the_number_of_lines() -> None:
    """Ce n'est pas un cas particulier du jeu de démonstration.

    Sans cette mesure, on pourrait croire l'écart borné à un centime. Il croît
    avec le nombre de postes, et un bordereau de voirie en compte des centaines.
    """
    from metreo_domain.estimate import EstimateLineInput, LineKind, compute_estimate
    from metreo_domain.money import DEFAULT_ROUNDING, Money
    from metreo_domain.pricing import LumpSumComponent, MarkupPolicy, ResourceKind
    from metreo_domain.units import Quantity

    def ligne(rang: int) -> EstimateLineInput:
        # Un montant qui porte toujours une fraction de centime.
        montant = Decimal(f"{100 + rang}.{(rang * 7) % 100:02d}7")
        return EstimateLineInput(
            line_id=f"L{rang:04d}",
            code=str(rang),
            designation=f"Poste {rang}",
            quantity=Quantity.of("1", "fft"),
            kind=LineKind.ITEM,
            components=(
                LumpSumComponent(
                    label="forfait",
                    kind=ResourceKind.OTHER,
                    amount_value=Money(montant, "EUR"),
                ),
            ),
        )

    ecarts: dict[int, Decimal] = {}
    for nombre in (8, 200):
        resultat = compute_estimate(
            lines=tuple(ligne(rang) for rang in range(1, nombre + 1)),
            currency="EUR",
            markup=MarkupPolicy(),
        )
        rendu = resultat.to_dict(DEFAULT_ROUNDING)
        somme = sum(
            Decimal(poste["price"]["selling_price_ht"])
            for poste in rendu["lines"]
            if poste["included_in_total"] and poste["price"]
        )
        ecarts[nombre] = somme - Decimal(rendu["total_selling_price_ht"])

    assert ecarts[8] != 0 and ecarts[200] != 0, _A_CORRIGER
    assert abs(ecarts[200]) > abs(ecarts[8]), (
        f"l'écart devrait croître avec le nombre de postes : "
        f"8 postes → {ecarts[8]}, 200 postes → {ecarts[200]}"
    )
