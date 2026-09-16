"""Un nombre, une seule écriture — quel que soit le moteur qui l'a rendu.

L'instantané gelé est haché en SHA-256 (``snapshot_digest``). Son contenu doit
donc dépendre des valeurs, jamais de la façon dont le backend les a rendues.
PostgreSQL renvoie du ``NUMERIC(28, 10)`` rembourré — une quantité de 120
revient en ``Decimal("120.0000000000")`` — là où SQLite renvoie le texte écrit.
C'est ``canonical_text`` qui absorbe cet écart, et rien ne le vérifiait.

Mesuré, sur ``main`` : en remplaçant ``normalize_decimal`` par ``to_decimal``
dans ``canonical_text``, les trois travaux de la CI restent verts — domaine
(127 tests), API SQLite (735), API PostgreSQL (779) — alors que l'instantané
gelé sur PostgreSQL change en quatre points (``amount_raw`` « 4744 » devient
« 4744.0 », « 14220.44 » devient « 14220.4400000 », ``resource_quantity``
« 772.85 » devient « 772.850000 ») et l'empreinte avec lui. Le même devis gelé
sur deux moteurs aurait alors donné deux SHA-256.

L'écriture attendue est décrite ici en toutes lettres plutôt qu'en rappelant
``canonical_text`` : comparer la sortie de la fonction à elle-même passerait
quoi qu'elle fasse.

Les clés vérifiées sont celles que ``canonical_text`` alimente, lues dans
``metreo_domain.pricing`` : ``resource_quantity`` et ``amount_raw``
(``ComponentResult.to_dict``), ``rate`` (``MarkupStepResult.to_dict``).
``selling_price_ht_raw`` en est absent volontairement : il passe par ``str()``
et porte donc ses zéros de queue — « 11591.5013955000 » — - qui sont, eux,
identiques sur les deux moteurs.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import login
from .test_estimating import price_the_missing_line

#: Alimentées par ``canonical_text``. Voir le docstring du module.
CLES_CANONIQUES = frozenset({"resource_quantity", "amount_raw", "rate"})

#: En dessous, l'instantané n'a pas été parcouru et le test ne prouverait rien.
MINIMUM_DE_VALEURS = 10

_ENTIER_OU_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")


def mal_ecrit(texte: str) -> str | None:
    """Pourquoi ``texte`` n'est pas l'écriture canonique de son nombre.

    Canonique veut dire : une notation positionnelle, et aucun zéro de queue
    dans la partie décimale. « 120 », « 0.06 » et « 0 » le sont ; « 120.0 »,
    « 0.0600000000 » et « 1.2E+2 » ne le sont pas.
    """
    if not _ENTIER_OU_DECIMAL.match(texte):
        return f"{texte!r} n'est pas en notation positionnelle"
    if "." in texte and texte.endswith("0"):
        return f"{texte!r} porte un zéro de queue"
    return None


def valeurs_canoniques(noeud: Any, cle: str | None = None) -> list[tuple[str, str]]:
    """Toutes les (clé, valeur) de l'instantané portant une écriture canonique."""
    if isinstance(noeud, dict):
        return [p for k, v in noeud.items() for p in valeurs_canoniques(v, k)]
    if isinstance(noeud, list):
        return [p for v in noeud for p in valeurs_canoniques(v, cle)]
    if isinstance(noeud, str) and cle in CLES_CANONIQUES:
        return [(cle, noeud)]
    return []


@pytest.fixture()
def instantane_gele(seeded_client: TestClient) -> dict[str, Any]:
    headers = login(seeded_client, "admin@dubois.demo")
    estimate = seeded_client.get("/api/v1/estimates", headers=headers).json()[0]
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]
    price_the_missing_line(seeded_client, headers, estimate)
    gel = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert gel.status_code == 200, gel.text

    from metreo_api.db import get_session_factory
    from metreo_api.models import EstimateVersion

    session = get_session_factory()()
    try:
        return dict(session.get(EstimateVersion, version["id"]).snapshot)
    finally:
        session.close()


def test_the_frozen_snapshot_spells_every_number_the_same_way_on_any_engine(
    instantane_gele: dict[str, Any],
) -> None:
    trouvees = valeurs_canoniques(instantane_gele)
    assert len(trouvees) >= MINIMUM_DE_VALEURS, (
        "L'instantané n'a pas été parcouru : "
        f"{len(trouvees)} valeur(s) trouvée(s) pour {sorted(CLES_CANONIQUES)}. "
        "Sans cette borne, le test passerait sur un instantané vide."
    )
    fautives = [
        f"{cle} : {raison}" for cle, valeur in trouvees if (raison := mal_ecrit(valeur)) is not None
    ]
    assert fautives == [], (
        "Ces valeurs de l'instantané gelé portent l'écriture du backend et non "
        f"celle du nombre ; l'empreinte SHA-256 dépendrait du moteur : {fautives}"
    )


def test_the_spelling_check_rejects_what_a_backend_would_produce() -> None:
    """Le contrôle ci-dessus passerait aussi s'il n'examinait rien.

    Les formes refusées sont exactement celles que PostgreSQL fait remonter
    d'un ``NUMERIC(28, 10)``.
    """
    assert mal_ecrit("120") is None
    assert mal_ecrit("0.06") is None
    assert mal_ecrit("0") is None
    assert mal_ecrit("13.18181818181818181818181818") is None

    assert mal_ecrit("120.0000000000") is not None
    assert mal_ecrit("0.0600000000") is not None
    assert mal_ecrit("4744.0") is not None
    assert mal_ecrit("2233.00") is not None
    assert mal_ecrit("1.2E+2") is not None
    assert mal_ecrit("0E-10") is not None
