"""Quel taux de TVA se retrouve sur un devis, et lequel n'y a pas sa place.

`active_taxes` décide seul du taux appliqué à une estimation : il écarte les
taux pas encore en vigueur, ceux qui ne le sont plus, et ceux qui ne sont pas
le taux par défaut de l'organisation. C'est le montant facturé qui en dépend.

Mesuré, sur `main`, par une campagne de mutation : les trois règles de
sélection étaient sans aucune couverture. En supprimant tour à tour le filtre
sur `applies_from`, celui sur `applies_to` et celui sur `is_default`, la suite
complète restait verte à chaque fois.

La raison en est visible dans le jeu de démonstration : ses deux taux belges —
21 % par défaut, 6 % non par défaut, tous deux en vigueur depuis 1996 et sans
date de fin — ne déclenchent aucun des trois filtres. Les règles tournaient
donc à vide dans tous les tests qui les traversaient.

Les dates sont passées explicitement par `at`, jamais laissées à
`date.today()` : un test qui dépend du jour où il tourne finit par mentir un
jour précis, et c'est le jour où on ne le regarde pas.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from .conftest import login  # noqa: F401  (importé pour la fixture seeded_client)

#: Bornes choisies loin de tout aujourd'hui, pour que le test dise la même
#: chose dans dix ans.
AVANT = date(2020, 1, 1)
PIVOT = date(2025, 6, 15)
APRES = date(2030, 1, 1)


def _session() -> Any:
    from metreo_api.db import get_session_factory

    return get_session_factory()()


@pytest.fixture()
def organisation(seeded: dict[str, str]) -> str:
    return seeded["organization_a"]


def _ajouter_taux(
    organization_id: str,
    *,
    code: str,
    taux: str,
    depuis: date | None,
    jusqu_a: date | None = None,
    par_defaut: bool = True,
) -> None:
    from metreo_api.models import TaxRateRow

    session = _session()
    try:
        session.add(
            TaxRateRow(
                organization_id=organization_id,
                code=code,
                label=f"Taux {code}",
                rate=taux,
                applies_from=depuis,
                applies_to=jusqu_a,
                is_default=par_defaut,
            )
        )
        session.commit()
    finally:
        session.close()


def _codes_retenus(organization_id: str, *, au: date) -> set[str]:
    from metreo_api.services.estimating import active_taxes

    session = _session()
    try:
        return {taux.code for taux in active_taxes(session, organization_id, at=au)}
    finally:
        session.close()


def test_the_seeded_default_rate_is_the_one_selected(organisation: str) -> None:
    """Contrôle positif : sans lui, tous les tests d'exclusion passeraient
    aussi si `active_taxes` ne renvoyait jamais rien."""
    retenus = _codes_retenus(organisation, au=PIVOT)
    assert retenus == {"VAT-BE-21"}, retenus


def test_a_rate_not_yet_in_force_is_left_out_until_its_date(organisation: str) -> None:
    """Et il entre le jour venu : le filtre suit la date, il n'exclut pas en bloc."""
    _ajouter_taux(organisation, code="TVA-FUTURE", taux="0.30", depuis=APRES)

    assert "TVA-FUTURE" not in _codes_retenus(organisation, au=PIVOT)
    assert "TVA-FUTURE" in _codes_retenus(organisation, au=APRES)


def test_a_rate_that_has_expired_is_left_out_from_the_day_after(organisation: str) -> None:
    _ajouter_taux(organisation, code="TVA-ANCIENNE", taux="0.25", depuis=AVANT, jusqu_a=PIVOT)

    assert "TVA-ANCIENNE" in _codes_retenus(organisation, au=AVANT)
    assert "TVA-ANCIENNE" not in _codes_retenus(organisation, au=APRES)


def test_the_boundaries_are_inclusive_on_both_ends(organisation: str) -> None:
    """Le jour même compte, des deux côtés.

    `applies_from > reference` et `applies_to < reference` : un taux qui
    commence aujourd'hui s'applique aujourd'hui, un taux qui finit aujourd'hui
    s'applique encore aujourd'hui. C'est le décalage d'un jour classique, et
    il porte sur un montant facturé.
    """
    _ajouter_taux(organisation, code="TVA-BORNES", taux="0.15", depuis=PIVOT, jusqu_a=PIVOT)

    assert "TVA-BORNES" in _codes_retenus(organisation, au=PIVOT)
    assert "TVA-BORNES" not in _codes_retenus(organisation, au=date(2025, 6, 14))
    assert "TVA-BORNES" not in _codes_retenus(organisation, au=date(2025, 6, 16))


def test_a_rate_that_is_not_the_default_is_never_selected(organisation: str) -> None:
    """Le 6 % belge est dans le jeu de démonstration, et n'est pas retenu.

    Le taux réduit existe et se choisit au cas par cas ; il ne s'applique pas
    d'office. `active_taxes` ne renvoie que le taux par défaut.
    """
    assert "VAT-BE-06" not in _codes_retenus(organisation, au=PIVOT)

    _ajouter_taux(organisation, code="TVA-OPTION", taux="0.12", depuis=AVANT, par_defaut=False)
    assert "TVA-OPTION" not in _codes_retenus(organisation, au=PIVOT)
