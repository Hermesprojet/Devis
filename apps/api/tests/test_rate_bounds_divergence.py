"""Les limites de taux du schéma HTTP ont divergé de celles du domaine.

Ce test vit ici, et non dans `packages/domain/tests`, parce qu'il lit
`metreo_api.schemas`. La CI installe le job « Domaine et contrats purs » avec
`packages/domain` et `packages/contracts` **seulement** : un import de
`metreo_api` depuis là passerait en local — l'environnement de développement
installe les trois paquets en mode éditable — et échouerait en CI.

Voir `packages/domain/tests/test_unproven_bounds.py` pour les deux autres
bornes que la campagne de mutation a trouvées non prouvées.
"""

from __future__ import annotations

from decimal import Decimal

from metreo_domain import bounds

LES_QUATRE_TAUX = (
    "site_overheads_rate",
    "general_overheads_rate",
    "contingency_rate",
    "margin_rate",
)


def test_the_bound_values_are_not_rewritten_by_hand_in_the_schemas() -> None:
    """Les limites du schéma HTTP doivent dériver de `bounds`, pas les copier.

    `schemas._bounded` existe pour cela, et son docstring l'affirme :
    « redéclarer ici un maximum en dur produirait deux vérités qui
    divergeraient à la première correction ».

    Elles ont divergé. Les quatre taux de `OrganizationSettingsUpdate` sont
    écrits à la main, et `margin_rate` porte un `lt=10` là où les trois autres
    portent `le=10` et où le domaine accepte `10` pour les quatre. Mesuré :

        margin_rate = 10          domaine : accepté   schéma HTTP : refusé
        site_overheads_rate = 10  domaine : accepté   schéma HTTP : accepté

    Ce test **constate** cet écart, il ne le corrige pas : aligner le schéma
    sur le domaine ou l'inverse change ce qu'une entreprise peut saisir, et
    c'est une décision, pas une correction. Il rougira le jour où l'écart sera
    tranché — et c'est ce qu'on veut : que la décision soit visible.
    """
    from metreo_api.schemas import OrganizationSettingsUpdate

    def borne_haute(champ: str) -> tuple[Decimal | None, Decimal | None]:
        contraintes = OrganizationSettingsUpdate.model_fields[champ].metadata
        le = next((getattr(c, "le", None) for c in contraintes if hasattr(c, "le")), None)
        lt = next((getattr(c, "lt", None) for c in contraintes if hasattr(c, "lt")), None)
        return le, lt

    releve = {champ: borne_haute(champ) for champ in LES_QUATRE_TAUX}
    attendu = {
        "site_overheads_rate": (Decimal(10), None),
        "general_overheads_rate": (Decimal(10), None),
        "contingency_rate": (Decimal(10), None),
        # Le seul strict des quatre, et le seul à diverger du domaine.
        "margin_rate": (None, Decimal(10)),
    }
    assert releve == attendu, (
        f"les limites de taux du schéma ont changé : {releve}. Si elles "
        "dérivent désormais de bounds.RATE, supprimez ce test ; sinon, "
        "mettez-le à jour et dites laquelle des deux vérités fait foi."
    )

    assert bounds.RATE.maximum == Decimal(10), (
        "bounds.RATE a bougé sans que les quatre littéraux du schéma suivent"
    )


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
