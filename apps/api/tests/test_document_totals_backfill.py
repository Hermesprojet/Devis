"""La migration reconstruit les totaux imprimés des versions déjà gelées.

La reconstruction est déterministe et ne rejoue aucun moteur : l'instantané
stocké porte déjà, ligne par ligne, le total HT tel qu'il fut imprimé. Le total
documentaire est leur somme ; la TVA de chaque taux, celle de la base imprimée.

Ce fichier vérifie la fonction de reconstruction elle-même, sur des instantanés
construits à la main, plutôt que de rejouer `alembic upgrade` : le contenu de
l'instantané est ce qui décide, et c'est lui qu'il faut exercer — y compris
dans les formes qu'on ne sait pas lire, qui doivent rendre `None` et non lever.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260829_0001_document_totals.py"
)


def _module():
    """Charge la migration comme un module ordinaire, sans passer par Alembic."""
    spec = importlib.util.spec_from_file_location("migration_totaux", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARRONDI = {"scale": 2, "mode": "half_up"}


def _ligne(montant: str, *, incluse: bool = True, taxes: tuple[str, ...] = ("VAT-BE-21",)):
    return {
        "included_in_total": incluse,
        "price": {
            "selling_price_ht": montant,
            "taxes": [{"code": code} for code in taxes],
        },
    }


def test_the_document_total_is_the_sum_of_the_printed_lines() -> None:
    module = _module()
    instantane = {
        "result": {
            "lines": [_ligne("100.01"), _ligne("100.01"), _ligne("100.01")],
            "taxes": [{"code": "VAT-BE-21", "rate": "0.21"}],
        }
    }
    ht, ttc = module._document_totals(instantane, ARRONDI)

    assert ht == Decimal("300.03")
    # 21 % de 300,03 = 63,0063 -> 63,01
    assert ttc == Decimal("363.04")


def test_excluded_lines_stay_out_of_the_total_and_of_the_taxable_base() -> None:
    """Une option est chiffrée, elle n'entre ni dans le HT ni dans la base."""
    module = _module()
    instantane = {
        "result": {
            "lines": [
                _ligne("100.01"),
                _ligne("200.01"),
                _ligne("999.99", incluse=False),
            ],
            "taxes": [{"code": "VAT-BE-21", "rate": "0.21"}],
        }
    }
    ht, ttc = module._document_totals(instantane, ARRONDI)

    assert ht == Decimal("300.02")
    assert ttc == Decimal("300.02") + Decimal("63.00")


def test_each_rate_gets_its_own_printed_base() -> None:
    """Deux taux : chacun sur les lignes qui le portent, pas sur le HT global."""
    module = _module()
    instantane = {
        "result": {
            "lines": [
                _ligne("100.00", taxes=("VAT-BE-21",)),
                _ligne("200.00", taxes=("VAT-BE-6",)),
            ],
            "taxes": [
                {"code": "VAT-BE-21", "rate": "0.21"},
                {"code": "VAT-BE-6", "rate": "0.06"},
            ],
        }
    }
    ht, ttc = module._document_totals(instantane, ARRONDI)

    assert ht == Decimal("300.00")
    # 21 % de 100 + 6 % de 200 = 21,00 + 12,00
    assert ttc == Decimal("333.00")


def test_the_version_rounding_policy_is_honoured() -> None:
    """L'échelle et le mode viennent de la version, pas d'un défaut global."""
    module = _module()
    instantane = {
        "result": {
            "lines": [_ligne("100.005")],
            "taxes": [{"code": "VAT-BE-21", "rate": "0.21"}],
        }
    }
    ht_pair, ttc_pair = module._document_totals(instantane, {"scale": 2, "mode": "half_even"})
    ht_sup, ttc_sup = module._document_totals(instantane, {"scale": 2, "mode": "half_up"})

    # 21,00105 -> 21,00 dans les deux modes ; c'est la mise à l'échelle qui
    # doit différer sur un demi exact.
    assert ht_pair == ht_sup == Decimal("100.005")
    assert ttc_pair == ttc_sup == Decimal("100.005") + Decimal("21.00")

    ht3, _ = module._document_totals(instantane, {"scale": 3, "mode": "half_up"})
    assert ht3 == Decimal("100.005")


@pytest.mark.parametrize(
    "instantane",
    [
        {},
        {"result": None},
        {"result": {}},
        {"result": {"lines": "pas une liste", "taxes": []}},
        {"result": {"lines": [], "taxes": "pas une liste"}},
        {"result": {"lines": [{"included_in_total": True, "price": {}}], "taxes": []}},
        {"result": {"lines": [_ligne("pas-un-nombre")], "taxes": []}},
    ],
)
def test_an_unreadable_snapshot_yields_nothing_rather_than_raising(instantane) -> None:
    """Une forme qu'on ne sait pas lire laisse la ligne à NULL.

    C'est le comportement voulu : la migration ne doit ni échouer sur une
    donnée ancienne, ni inventer un total. `NULL` dit « inconnu », et l'API
    l'affiche comme une absence.
    """
    assert _module()._document_totals(instantane, ARRONDI) is None


def test_a_snapshot_without_taxes_still_yields_a_total() -> None:
    """Sans TVA, le TTC vaut le HT — et la version reste reconstruite."""
    module = _module()
    instantane = {
        "result": {
            "lines": [_ligne("100.01", taxes=()), _ligne("50.02", taxes=())],
            "taxes": [],
        }
    }
    ht, ttc = module._document_totals(instantane, ARRONDI)
    assert ht == Decimal("150.03")
    assert ttc == Decimal("150.03")
