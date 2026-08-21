"""Le statut d'une ligne ne se change que par une transition autorisée.

Régression P0-2 de la revue indépendante : `metreur@dubois.demo` recevait 403
sur `POST /boq-items/{id}/approve` et obtenait 200 en envoyant
`PATCH {"status": "approved"}`. La matrice route-permission était verte, et
l'élévation de privilège passait par un champ.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import login


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def estimator(seeded_client: TestClient) -> dict[str, str]:
    """Porte BOQ_WRITE mais pas BOQ_APPROVE."""
    return login(seeded_client, "metreur@dubois.demo")


@pytest.fixture()
def item(seeded_client: TestClient, admin: dict[str, str]) -> dict:
    project = seeded_client.post(
        "/api/v1/projects", headers=admin, json={"reference": "T-1", "name": "Transitions"}
    ).json()
    boq = seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=admin, json={"name": "Métré"}
    ).json()
    return seeded_client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=admin,
        json={
            "position": "1.1",
            "designation": "Déblai en pleine masse",
            "unit_code": "m3",
            "quantity": "100",
        },
    ).json()


class TestFieldLevelEscalation:
    def test_a_writer_cannot_approve_through_the_update_route(
        self, seeded_client: TestClient, estimator: dict[str, str], item: dict
    ) -> None:
        """Le cœur du défaut : refusé sur une route, obtenu sur l'autre."""
        denied = seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=estimator)
        assert denied.status_code == 403

        smuggled = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}", headers=estimator, json={"status": "approved"}
        )
        assert smuggled.status_code in (403, 422), smuggled.text

        current = seeded_client.get(
            f"/api/v1/boqs/{item['boq_id']}/items", headers=estimator
        ).json()
        stored = next(row for row in current if row["id"] == item["id"])
        assert stored["status"] != "approved", "le statut a été écrit malgré le refus"

    def test_a_writer_cannot_downgrade_an_approved_line(
        self,
        seeded_client: TestClient,
        admin: dict[str, str],
        estimator: dict[str, str],
        item: dict,
    ) -> None:
        """Déclasser vaut approuver : les deux exigent BOQ_APPROVE."""
        seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}", headers=estimator, json={"status": "proposed"}
        )
        assert response.status_code in (403, 422)

    def test_the_two_request_lock_bypass_is_closed(
        self,
        seeded_client: TestClient,
        admin: dict[str, str],
        estimator: dict[str, str],
        item: dict,
    ) -> None:
        """Contournement en deux temps : déclasser, puis modifier la quantité.

        Le verrou sur les quantités approuvées ne vaut que si le statut ne peut
        pas être abaissé pour l'ouvrir.
        """
        seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}", headers=estimator, json={"status": "proposed"}
        )
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}", headers=estimator, json={"quantity": "999"}
        )
        assert response.status_code == 409, response.text


class TestLegitimateTransitions:
    def test_an_approver_can_still_approve(
        self, seeded_client: TestClient, admin: dict[str, str], item: dict
    ) -> None:
        response = seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "approved"

    def test_a_writer_can_still_edit_a_draft_line(
        self, seeded_client: TestClient, estimator: dict[str, str], item: dict
    ) -> None:
        """La correction ne doit pas empêcher le travail normal du métreur."""
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}",
            headers=estimator,
            json={"quantity": "120", "designation": "Déblai, révisé"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["quantity"] == "120"


class TestApprovedQuantityOverride:
    """Bloquant A : `override_approved` ne remplace pas une autorisation.

    Le premier contournement passait par le champ `status`. Celui-ci passe par
    `override_approved` : le porteur de BOQ_WRITE ne peut pas approuver, mais
    il pouvait modifier une quantité approuvée en se déclarant lui-même
    autorisé à déroger — et la ligne redescendait à « vérifié » au passage.
    """

    def test_a_writer_cannot_override_an_approved_quantity(
        self,
        seeded_client: TestClient,
        admin: dict[str, str],
        estimator: dict[str, str],
        item: dict,
    ) -> None:
        seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}",
            headers=estimator,
            json={"quantity": "999", "override_approved": True, "override_reason": "Probe"},
        )
        assert response.status_code == 403, response.text

        listed = seeded_client.get(f"/api/v1/boqs/{item['boq_id']}/items", headers=estimator).json()
        stored = next(row for row in listed if row["id"] == item["id"])
        assert stored["quantity"] == "100", "la quantité approuvée a été modifiée"
        assert stored["status"] == "approved", "le statut approuvé a été perdu"

    def test_an_approver_still_needs_a_reason(
        self, seeded_client: TestClient, admin: dict[str, str], item: dict
    ) -> None:
        seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}",
            headers=admin,
            json={"quantity": "999", "override_approved": True},
        )
        assert response.status_code == 422

    def test_an_approver_with_a_reason_may_override_and_it_is_audited(
        self, seeded_client: TestClient, admin: dict[str, str], item: dict
    ) -> None:
        seeded_client.post(f"/api/v1/boq-items/{item['id']}/approve", headers=admin)
        response = seeded_client.patch(
            f"/api/v1/boq-items/{item['id']}",
            headers=admin,
            json={"quantity": "999", "override_approved": True, "override_reason": "Métré corrigé"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["quantity"] == "999"
        assert response.json()["status"] == "verified"

        events = seeded_client.get("/api/v1/audit/events", headers=admin).json()
        rows = events["items"] if isinstance(events, dict) else events
        assert any("dérogation" in (e.get("summary") or "").lower() for e in rows), rows[:3]
