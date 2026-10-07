"""La décision humaine : ce qu'elle retient, ce qu'elle refuse, ce qu'elle garde.

Trois règles, et chacune a sa raison d'exister :

1. **une correction est une QUANTITÉ**, pas le texte qu'on a bien voulu taper.
   Le champ `after_value` est volontairement libre — il sert à toutes les
   propositions d'extraction — et pour une mesure de plan cela voulait dire que
   « 3,8 m environ » partait en base et revenait à l'écran. Le jour où une
   mesure validée alimentera un métré, ce champ sera lu comme un nombre, et il
   sera trop tard pour le découvrir ce jour-là ;
2. **une mesure rejetée n'alimente aucun bordereau**, ni une mesure sur
   laquelle personne n'a tranché. Le produit le promet depuis le début ; ces
   tests sont l'endroit où la promesse devient exécutable ;
3. **la proposition de la machine survit à la décision.** Après une correction,
   la ligne porte encore la valeur mesurée, la valeur retenue ET le motif. Sans
   les trois, le dossier cesse d'être auditable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import calibration_de_plan

from .conftest import login
from .test_calibration_de_plan_api import _calibrer, _deposer_un_pdf, _mesurer, _relire

pytest.importorskip(
    "pypdfium2",
    reason="l'extra « pdf » n'est pas installé : pip install './apps/api[pdf]'",
)

ADMIN = "admin@dubois.demo"


# ---------------------------------------------------------------------------
# La règle pure : ce qui est retenu, et ce qui ne l'est pas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "attendu"),
    [
        ("accepted", "4180.68"),  # confirmée : la mesure elle-même
        ("corrected", "3800.50"),  # corrigée : la valeur de la personne
        ("rejected", None),  # rejetée : RIEN
        (None, None),  # pas encore tranchée : rien non plus
    ],
)
def test_ce_qu_une_decision_retient(decision: str | None, attendu: str | None) -> None:
    """Quatre états, quatre réponses — et deux d'entre elles sont « rien ».

    C'est la règle centrale du produit : une proposition machine n'est pas une
    quantité tant qu'un humain ne l'a pas faite sienne.
    """
    retenue = calibration_de_plan._valeur_retenue(decision, Decimal("4180.68"), Decimal("3800.50"))
    assert retenue == (Decimal(attendu) if attendu is not None else None)


def test_une_correction_sans_valeur_ne_retient_rien() -> None:
    """Un refus déguisé, et traité comme tel.

    Reprendre la proposition dans ce cas reviendrait à reprendre exactement le
    nombre que la personne venait d'écarter.
    """
    assert calibration_de_plan._valeur_retenue("corrected", Decimal("4180.68"), None) is None


# ---------------------------------------------------------------------------
# Le parcours complet, par l'API
# ---------------------------------------------------------------------------


def _plan_mesure(client: TestClient, reference: str) -> tuple[dict[str, str], str, str, dict]:
    """Un plan déposé, calibré et mesuré. Rend de quoi décider ensuite."""
    admin = login(client, ADMIN)
    document, revision = _deposer_un_pdf(client, admin, reference)
    assert _calibrer(client, admin, document, revision).status_code == 201
    mesure = _mesurer(client, admin, document, revision, libelle="Façade sud")
    assert mesure.status_code == 201, mesure.text
    return admin, document, revision, mesure.json()


def _relire_la_mesure(
    client: TestClient, admin: dict[str, str], document: str, revision: str, proposal_id: str
) -> dict:
    reponse = _relire(client, admin, document, revision)
    assert reponse.status_code == 200, reponse.text
    return next(m for m in reponse.json()["mesures"] if m["proposal_id"] == proposal_id)


def _decider(
    client: TestClient, admin: dict[str, str], proposal_id: str, **corps: object
) -> object:
    return client.post(
        f"/api/v1/extraction-proposals/{proposal_id}/decisions", headers=admin, json=corps
    )


def test_une_mesure_est_rendue_lisible_des_sa_creation(seeded_client: TestClient) -> None:
    """La valeur exacte ET son écriture française, côte à côte.

    L'une sert au calcul, l'autre à l'œil. Les deux voyagent ensemble pour que
    l'écran n'ait jamais à refaire l'arrondi — deux arrondis finiraient par
    diverger d'un chiffre.
    """
    _, _, _, corps = _plan_mesure(seeded_client, "PDF-LISIBLE")

    assert "." in corps["valeur"], "la valeur exacte garde son point décimal"
    assert corps["valeur_lisible"].endswith(" mm")
    assert "," in corps["valeur_lisible"] or " " in corps["valeur_lisible"], (
        f"la valeur lisible doit être écrite en français : {corps['valeur_lisible']}"
    )
    assert corps["incertitude_lisible"].startswith("± ")


def test_une_mesure_sans_decision_n_alimente_aucun_bordereau(
    seeded_client: TestClient,
) -> None:
    """Le point de départ : une proposition n'est pas une quantité."""
    _, _, _, corps = _plan_mesure(seeded_client, "PDF-SANS-DECISION")

    assert corps["decision"] is None
    assert corps["valeur_retenue"] is None
    assert corps["reprenable"] is False


def test_une_mesure_confirmee_devient_reprenable(seeded_client: TestClient) -> None:
    admin, document, revision, corps = _plan_mesure(seeded_client, "PDF-CONFIRMEE")

    reponse = _decider(
        seeded_client,
        admin,
        corps["proposal_id"],
        decision="accepted",
        reason="Vérifiée sur place au décamètre.",
    )
    assert reponse.status_code == 201, reponse.text

    relue = _relire_la_mesure(seeded_client, admin, document, revision, corps["proposal_id"])
    assert relue["decision"] == "accepted"
    assert relue["valeur_retenue"] == relue["valeur"], "confirmer retient la mesure telle quelle"
    assert relue["reprenable"] is True


def test_une_mesure_rejetee_n_alimente_jamais_un_bordereau(seeded_client: TestClient) -> None:
    """Et sa proposition reste lisible : on doit pouvoir dire ce qu'on a écarté."""
    admin, document, revision, corps = _plan_mesure(seeded_client, "PDF-REJETEE")

    reponse = _decider(
        seeded_client,
        admin,
        corps["proposal_id"],
        decision="rejected",
        reason="Contour pointé sur le mauvais local.",
    )
    assert reponse.status_code == 201, reponse.text

    relue = _relire_la_mesure(seeded_client, admin, document, revision, corps["proposal_id"])
    assert relue["decision"] == "rejected"
    assert relue["valeur_retenue"] is None
    assert relue["reprenable"] is False
    assert relue["valeur"] == corps["valeur"], "la proposition machine n'est jamais réécrite"
    assert relue["valeur_lisible"] == corps["valeur_lisible"]


def test_une_mesure_corrigee_retient_la_valeur_de_la_personne(
    seeded_client: TestClient,
) -> None:
    """Les DEUX nombres survivent, et le motif avec eux."""
    admin, document, revision, corps = _plan_mesure(seeded_client, "PDF-CORRIGEE")
    motif = "Relevé sur place : la façade fait 3,80 m."

    reponse = _decider(
        seeded_client,
        admin,
        corps["proposal_id"],
        decision="corrected",
        reason=motif,
        before_value={"valeur": corps["valeur"], "unite": "mm"},
        after_value={"valeur": "3800,5", "unite": "mm"},
    )
    assert reponse.status_code == 201, reponse.text
    # L'accusé de décision ne répète pas les valeurs documentaires — c'est
    # voulu, et documenté sur `ValidationDecisionOut`. La normalisation se
    # constate donc en relisant la mesure, ci-dessous.
    assert reponse.json()["decision"] == "corrected"

    relue = _relire_la_mesure(seeded_client, admin, document, revision, corps["proposal_id"])
    assert relue["valeur"] == corps["valeur"], "la proposition machine n'est jamais réécrite"
    # La virgule belge est acceptée, et normalisée AVANT d'aller en base : le
    # premier lecteur qui oublierait de la convertir lirait zéro.
    assert relue["valeur_retenue"] == "3800.5"
    assert relue["unite_retenue"] == "mm"
    assert relue["valeur_retenue_lisible"] == "3 800,50 mm"
    assert relue["reprenable"] is True


# ---------------------------------------------------------------------------
# Ce qu'une correction ne peut PAS être
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("apres", "code"),
    [
        ({"valeur": "trois mille huit cents"}, "valeur_retenue_non_numerique"),
        ({"valeur": "3,8 m environ"}, "valeur_retenue_non_numerique"),
        ({"valeur": ""}, "valeur_retenue_absente"),
        ({"unite": "mm"}, "valeur_retenue_absente"),
        ({"valeur": "0"}, "valeur_retenue_non_positive"),
        ({"valeur": "-120"}, "valeur_retenue_non_positive"),
        ({"valeur": "3.8", "unite": "m2"}, "unite_retenue_differente"),
    ],
)
def test_une_correction_qui_n_est_pas_une_quantite_est_refusee(
    seeded_client: TestClient, apres: dict, code: str
) -> None:
    """Sept frappes plausibles, sept refus nommés.

    Le code est stable et la phrase est affichable telle quelle : dire « ce
    n'est pas une quantité » au moment de la saisie vaut mieux que le découvrir
    le jour où cette valeur entre dans un métré.
    """
    admin, _, _, corps = _plan_mesure(seeded_client, f"PDF-REFUS-{abs(hash(code)) % 1000}")

    reponse = _decider(
        seeded_client,
        admin,
        corps["proposal_id"],
        decision="corrected",
        reason="Relevé sur place.",
        before_value={"valeur": corps["valeur"], "unite": "mm"},
        after_value=apres,
    )
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == code
    assert reponse.json()["detail"]["message"]


def test_un_rejet_n_a_pas_besoin_de_valeur_retenue(seeded_client: TestClient) -> None:
    """Le contrôle ne vaut que pour une CORRECTION.

    Exiger une valeur pour rejeter demanderait à la personne d'inventer le
    nombre qu'elle vient précisément de déclarer inconnu.
    """
    admin, _, _, corps = _plan_mesure(seeded_client, "PDF-REJET-SANS-VALEUR")

    reponse = _decider(
        seeded_client, admin, corps["proposal_id"], decision="rejected", reason="Mauvais local."
    )
    assert reponse.status_code == 201, reponse.text


def test_une_confirmation_n_exige_pas_non_plus_de_valeur(seeded_client: TestClient) -> None:
    """Confirmer, c'est retenir la mesure telle quelle : rien à saisir."""
    admin, _, _, corps = _plan_mesure(seeded_client, "PDF-CONFIRM-SANS-VALEUR")

    reponse = _decider(
        seeded_client, admin, corps["proposal_id"], decision="accepted", reason="Elle est bonne."
    )
    assert reponse.status_code == 201, reponse.text


# ---------------------------------------------------------------------------
# La liste de ce qu'un bordereau pourrait reprendre
# ---------------------------------------------------------------------------


def test_la_derniere_decision_l_emporte_y_compris_sur_un_rejet(
    seeded_client: TestClient,
) -> None:
    """Un rejet n'est pas définitif, et c'est voulu : il se revient dessus.

    Ce qui est définitif est la TRACE : les trois décisions restent au journal,
    et c'est la dernière qui dit si la mesure est reprenable.
    """
    admin, document, revision, corps = _plan_mesure(seeded_client, "PDF-REVIREMENT")
    proposal_id = corps["proposal_id"]

    _decider(seeded_client, admin, proposal_id, decision="rejected", reason="Mauvais local.")
    rejetee = _relire_la_mesure(seeded_client, admin, document, revision, proposal_id)
    assert rejetee["reprenable"] is False

    _decider(
        seeded_client, admin, proposal_id, decision="accepted", reason="Revérifiée, elle est bonne."
    )
    reprise = _relire_la_mesure(seeded_client, admin, document, revision, proposal_id)
    assert reprise["decision"] == "accepted"
    assert reprise["reprenable"] is True
