from decimal import Decimal

import pytest

from metreo_domain.errors import CurrencyMismatchError
from metreo_domain.money import Money, RoundingPolicy, money_sum, to_decimal


def test_float_input_never_leaks_binary_artefacts():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")


def test_french_decimal_comma_is_accepted():
    assert to_decimal("1 234,56") == Decimal("1234.56")


def test_addition_requires_same_currency():
    with pytest.raises(CurrencyMismatchError):
        Money("10", "EUR") + Money("10", "USD")


def test_amounts_are_stored_unrounded():
    unit = Money("1.005", "EUR")
    total = unit.scaled_by(3)
    assert total.amount == Decimal("3.015")
    assert total.rounded().amount == Decimal("3.02")


def test_rounding_modes_differ_where_it_matters():
    amount = Money("2.5", "EUR")
    assert amount.rounded(RoundingPolicy(scale=0, mode="half_up")).amount == Decimal("3")
    assert amount.rounded(RoundingPolicy(scale=0, mode="half_even")).amount == Decimal("2")


def test_unit_price_scale_can_differ_from_total_scale():
    policy = RoundingPolicy(scale=2, mode="half_up", unit_price_scale=4)
    assert policy.quantize(Decimal("1.23456")) == Decimal("1.23")
    assert policy.quantize_unit_price(Decimal("1.23456")) == Decimal("1.2346")


def test_money_sum_of_empty_list_is_typed_zero():
    total = money_sum([], "EUR")
    assert total.is_zero() and total.currency == "EUR"


def test_invalid_currency_is_rejected():
    with pytest.raises(ValueError):
        Money("1", "EU")
