"""Trois bornes qu'on pouvait élargir sans qu'un seul test ne bronche.

Campagne de mutation sur `metreo_domain/bounds.py` — douze mutations, les trois
racines de tests de la CI, imports vérifiés comme provenant de l'arbre muté.
Neuf mutations tuées, trois survivantes :

    RATE                     10      -> 10^6      SURVIT
    DISTANCE_KM              20 000  -> 10^9      SURVIT
    MAX_COMPONENTS_PER_LINE  200     -> 100 000   SURVIT

Ces trois bornes sont bien câblées à des gardes réelles. Ce qui manquait, c'est
un test qui prouve que la garde se déclenche : on pouvait multiplier la limite
par cent mille et la suite restait verte.

Les neuf autres sont tuées par des assertions qui portent, et non par accident :
élargir `QUANTITY` fait rougir `worst_case_stored_value() < SQL_MAX_ABS`,
l'invariant qui justifie le `NUMERIC(28, 10)`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_domain import bounds
from metreo_domain.errors import OutOfBoundsError, PricingConfigurationError
from metreo_domain.money import Money
from metreo_domain.pricing import (
    LumpSumComponent,
    MarkupPolicy,
    ResourceKind,
    compute_line_price,
)
from metreo_domain.units import Quantity

LES_QUATRE_TAUX = (
    "site_overheads_rate",
    "general_overheads_rate",
    "contingency_rate",
    "margin_rate",
)


@pytest.mark.parametrize("champ", LES_QUATRE_TAUX)
def test_a_rate_past_the_bound_is_refused_by_the_engine_itself(champ: str) -> None:
    """Le garde-fou existe pour les appels qui ne passent pas par Pydantic.

    `MarkupPolicy.__post_init__` le dit en toutes lettres : « un instantané
    gelé, un script ou un appel direct au moteur fournissent des taux sans
    jamais passer par Pydantic ». C'est exactement ce chemin-là qu'aucun test
    n'empruntait.

    La valeur choisie n'est pas arbitraire : `21` est la faute de saisie que le
    commentaire de la borne nomme — un taux exprimé en pourcentage entier
    plutôt qu'en fraction. Un devis calculé avec une marge de 2 100 % n'est pas
    un devis cher, c'est un devis faux.
    """
    with pytest.raises(OutOfBoundsError) as leve:
        MarkupPolicy(**{champ: Decimal("21")})

    assert leve.value.context["bound"] == "rate"
    assert champ in str(leve.value), "le message doit nommer le taux fautif"


@pytest.mark.parametrize("champ", LES_QUATRE_TAUX)
def test_the_rate_bound_itself_is_accepted(champ: str) -> None:
    """La borne est inclusive : la refuser serait une autre erreur.

    Sans ce test, « resserrer » la borne d'un cran passerait inaperçu.
    """
    politique = MarkupPolicy(**{champ: bounds.RATE.maximum})
    assert getattr(politique, champ) == bounds.RATE.maximum

    with pytest.raises(OutOfBoundsError):
        MarkupPolicy(**{champ: bounds.RATE.maximum + Decimal("0.000001")})


def test_a_negative_rate_is_refused() -> None:
    """Un taux négatif rendrait le prix de vente inférieur au coût."""
    with pytest.raises(OutOfBoundsError):
        MarkupPolicy(margin_rate=Decimal("-0.01"))


def test_the_component_limit_is_two_hundred_and_that_number_is_a_decision() -> None:
    """La valeur elle-même, pas seulement le fait qu'une limite existe.

    Première version de ce test : il comparait `limite` et `limite + 1` en
    lisant `bounds.MAX_COMPONENTS_PER_LINE` des deux côtés. Rejouée contre la
    mutation « 200 -> 100 000 », elle restait verte — le test déplaçait ses
    propres bornes avec la mutation et ne mesurait plus rien.

    Le nombre est un choix métier : « au-delà, ce n'est plus un sous-détail
    mais un bordereau ». Un choix se constate, il ne se dérive pas de
    lui-même.
    """
    assert bounds.MAX_COMPONENTS_PER_LINE == 200, (
        f"la limite est passée à {bounds.MAX_COMPONENTS_PER_LINE}. Ce nombre "
        "borne ce qu'un sous-détail peut contenir : dites pourquoi il change, "
        "et mettez à jour le commentaire de la borne."
    )


def test_a_sub_detail_beyond_the_component_limit_is_refused() -> None:
    """Au-delà de deux cents composants, ce n'est plus un sous-détail."""
    limite = bounds.MAX_COMPONENTS_PER_LINE

    def composants(nombre: int) -> tuple[LumpSumComponent, ...]:
        return tuple(
            LumpSumComponent(
                label=f"poste {rang}",
                kind=ResourceKind.OTHER,
                amount_value=Money(Decimal("1.00"), "EUR"),
            )
            for rang in range(nombre)
        )

    # La limite elle-même passe : la borne est inclusive.
    resultat = compute_line_price(
        quantity=Quantity.of("1", "fft"),
        components=composants(limite),
        currency="EUR",
        markup=MarkupPolicy(),
    )
    assert len(resultat.components) == limite

    with pytest.raises(PricingConfigurationError) as leve:
        compute_line_price(
            quantity=Quantity.of("1", "fft"),
            components=composants(limite + 1),
            currency="EUR",
            markup=MarkupPolicy(),
        )
    assert leve.value.context["maximum"] == limite
