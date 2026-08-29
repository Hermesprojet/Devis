"""Une seule vérité pour les bornes de taux : `bounds.RATE`.

Elle ne l'était pas. Les quatre taux de `OrganizationSettingsUpdate`
réécrivaient leurs limites à la main, et les deux vérités avaient divergé :

    margin_rate = 10          moteur : accepté   schéma HTTP : refusé (lt=10)
    site_overheads_rate = 10  moteur : accepté   schéma HTTP : accepté (le=10)

Un instantané gelé portant `margin_rate = 10` se recalculait sans broncher
alors qu'il n'aurait jamais pu être saisi par l'API. Décision prise :
`bounds.RATE` fait foi, maximum `10`, inclusif, et les quatre champs en
dérivent par `_bounded_opt`.

Ces tests vivent ici, et non dans `packages/domain/tests`, parce qu'ils lisent
`metreo_api.schemas`. La CI installe le job « Domaine et contrats purs » avec
`packages/domain` et `packages/contracts` **seulement** : un import de
`metreo_api` depuis là passerait en local — l'environnement de développement
installe les trois paquets en mode éditable — et échouerait en CI.

Voir `packages/domain/tests/test_unproven_bounds.py` pour les deux autres
bornes que la campagne de mutation avait trouvées non prouvées.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from metreo_domain import bounds
from metreo_domain.errors import OutOfBoundsError
from metreo_domain.pricing import MarkupPolicy

from .conftest import login

LES_QUATRE_TAUX = (
    "site_overheads_rate",
    "general_overheads_rate",
    "contingency_rate",
    "margin_rate",
)


def test_the_four_rate_limits_derive_from_the_single_source_of_truth() -> None:
    """`bounds.RATE` fait foi, et les quatre taux en dérivent.

    Ils ne le faisaient pas : les limites étaient réécrites à la main, et les
    deux vérités avaient divergé — `margin_rate` portait `lt=10` là où les
    trois autres portaient `le=10` et où le moteur accepte `10` pour les
    quatre. Un instantané gelé à `margin_rate = 10` se recalculait sans
    broncher alors qu'il n'aurait jamais pu être saisi par l'API.

    Ce test ne compare pas des littéraux : il vérifie que chaque champ porte
    **exactement** la contrainte que `bounds.RATE` produit. Réintroduire un
    `le=10` écrit à la main passerait ce test — c'est voulu, la valeur serait
    la bonne — mais réintroduire un `lt` ou un maximum différent le fait
    rougir.
    """
    from metreo_api.schemas import OrganizationSettingsUpdate

    def contraintes(champ: str) -> dict[str, Decimal | None]:
        metadonnees = OrganizationSettingsUpdate.model_fields[champ].metadata
        return {
            borne: next(
                (getattr(c, borne) for c in metadonnees if hasattr(c, borne)),
                None,
            )
            for borne in ("ge", "gt", "le", "lt")
        }

    attendu = {
        "ge": bounds.RATE.minimum,
        "gt": None,
        "le": bounds.RATE.maximum,
        "lt": None,
    }
    for champ in LES_QUATRE_TAUX:
        assert contraintes(champ) == attendu, (
            f"{champ} porte {contraintes(champ)} au lieu de {attendu}. "
            "Les quatre taux doivent dériver de bounds.RATE par _bounded_opt."
        )


def test_the_api_accepts_the_bound_and_refuses_anything_above(
    seeded_client: TestClient,
) -> None:
    """La borne inclusive, vue de l'API : `10` passe, `10.000001` non.

    C'est le point exact où les deux vérités divergeaient. Il est mesuré ici
    sur le vrai chemin HTTP, pas sur le modèle Pydantic isolé : c'est ce que
    voit une entreprise qui règle ses taux.
    """
    headers = login(seeded_client, "admin@dubois.demo")

    for champ in LES_QUATRE_TAUX:
        accepte = seeded_client.patch(
            "/api/v1/organization/settings",
            headers=headers,
            json={champ: str(bounds.RATE.maximum)},
        )
        assert accepte.status_code == 200, f"{champ}=10 refusé : {accepte.text}"
        assert Decimal(accepte.json()[champ]) == bounds.RATE.maximum

        refuse = seeded_client.patch(
            "/api/v1/organization/settings",
            headers=headers,
            json={champ: str(bounds.RATE.maximum + Decimal("0.000001"))},
        )
        assert refuse.status_code == 422, f"{champ} au-delà de la borne accepté"

    # Et on repose les taux du jeu de démonstration : un test ne laisse pas
    # l'organisation avec une marge de 1000 %.
    remise = seeded_client.patch(
        "/api/v1/organization/settings",
        headers=headers,
        json=dict.fromkeys(LES_QUATRE_TAUX, "0.10"),
    )
    assert remise.status_code == 200, remise.text


def test_the_engine_and_the_api_agree_on_the_bound() -> None:
    """Les deux couches acceptent et refusent exactement les mêmes valeurs.

    Sans cette comparaison, chacune pourrait rester cohérente avec elle-même
    tout en divergeant de l'autre — c'est précisément ce qui s'était produit.
    """
    from pydantic import ValidationError

    from metreo_api.schemas import OrganizationSettingsUpdate

    def le_schema_accepte(champ: str, valeur: Decimal) -> bool:
        try:
            OrganizationSettingsUpdate(**{champ: valeur})
        except ValidationError:
            return False
        return True

    def le_moteur_accepte(champ: str, valeur: Decimal) -> bool:
        try:
            MarkupPolicy(**{champ: valeur})
        except OutOfBoundsError:
            return False
        return True

    valeurs = (
        Decimal("0"),
        Decimal("0.21"),
        bounds.RATE.maximum - Decimal("0.000001"),
        bounds.RATE.maximum,
        bounds.RATE.maximum + Decimal("0.000001"),
        Decimal("21"),
        Decimal("-0.01"),
    )
    desaccords = [
        (champ, str(valeur))
        for champ in LES_QUATRE_TAUX
        for valeur in valeurs
        if le_schema_accepte(champ, valeur) != le_moteur_accepte(champ, valeur)
    ]
    assert not desaccords, f"le schéma et le moteur divergent sur : {desaccords}"


def test_a_haulage_distance_past_the_bound_is_refused_by_the_api() -> None:
    """La troisième borne survivante — et la seule gardée par le seul schéma.

    `DISTANCE_KM` n'est vérifiée nulle part dans le moteur : elle ne vit que
    dans `RotationComponentIn.distance_km`, via `_bounded_opt`. Élargir la
    borne de 20 000 km à un milliard ne faisait rougir aucun test.

    La demi-circonférence terrestre est de 20 000 km. Au-delà, c'est une
    virgule déplacée ou une unité confondue (mètres pris pour kilomètres),
    et la majoration kilométrique qui en découle est fausse d'un facteur mille.
    """
    from pydantic import ValidationError

    from metreo_api.schemas import RotationComponentIn

    modele = {
        "component_type": "rotation",
        "label": "évacuation des terres",
        "resource_kind": "transport",
        "payload_value": "12",
        "payload_unit_code": "t",
        "cost_per_rotation": "180.00",
    }

    # La borne elle-même est acceptée.
    accepte = RotationComponentIn(
        **modele, distance_km=bounds.DISTANCE_KM.maximum, rate_per_km="0.85"
    )
    assert accepte.distance_km == bounds.DISTANCE_KM.maximum

    for trop_loin in (
        bounds.DISTANCE_KM.maximum + Decimal("0.001"),
        Decimal("1e9"),
    ):
        try:
            RotationComponentIn(**modele, distance_km=trop_loin, rate_per_km="0.85")
        except ValidationError:
            continue
        raise AssertionError(f"{trop_loin} km accepté comme distance d'évacuation")


def test_a_negative_haulage_distance_is_refused() -> None:
    from pydantic import ValidationError

    from metreo_api.schemas import RotationComponentIn

    try:
        RotationComponentIn(
            component_type="rotation",
            label="évacuation",
            resource_kind="transport",
            payload_value="12",
            payload_unit_code="t",
            cost_per_rotation="180.00",
            distance_km=Decimal("-1"),
            rate_per_km="0.85",
        )
    except ValidationError:
        return
    raise AssertionError("une distance négative a été acceptée")
