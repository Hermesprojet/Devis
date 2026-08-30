"""Les trois sondes, et surtout leur comportement quand la base tombe.

Une panne de base est le moment où ces trois points doivent dire trois choses
différentes. S'ils disent la même, l'un des trois est de trop — et pire, la
panne se propage : un orchestrateur qui lit le mauvais point redémarre des
conteneurs sains les uns après les autres.

    /live    le processus répond           -> reste VERT pendant la panne
    /ready   le service peut servir        -> passe ROUGE (503)
    /health  état lisible par un humain    -> 200, mais `degraded`
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

BASE = "/api/v1"


@pytest.fixture()
def base_injoignable(monkeypatch: pytest.MonkeyPatch):
    """Rend toute exécution SQL impossible, comme une base tombée.

    On coupe au niveau de `Session.execute` : c'est le geste que font les
    trois routes, et le seul endroit où une vraie panne se manifesterait de
    la même façon pour toutes.
    """
    from sqlalchemy.orm import Session

    def tombe(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connexion refusée"))

    monkeypatch.setattr(Session, "execute", tombe)


def test_live_says_only_that_the_process_answers(client: TestClient) -> None:
    reponse = client.get(f"{BASE}/live")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "live"}


def test_ready_is_green_when_the_database_answers(client: TestClient) -> None:
    reponse = client.get(f"{BASE}/ready")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "ready", "database": "ok"}


def test_live_stays_green_while_the_database_is_down(
    client: TestClient, base_injoignable: None
) -> None:
    """Le point que le HEALTHCHECK des images interroge.

    S'il virait au rouge ici, une panne de base ferait redémarrer tous les
    conteneurs applicatifs — une panne ajoutée à une panne, sur des processus
    qui n'ont rien de cassé.
    """
    reponse = client.get(f"{BASE}/live")
    assert reponse.status_code == 200
    assert reponse.json() == {"status": "live"}


def test_ready_turns_red_when_the_database_is_down(
    client: TestClient, base_injoignable: None
) -> None:
    """503, et pas un 200 poli.

    Un équilibreur ne lit pas notre JSON : il lit le code. Répondre 200 avec
    un corps qui dit « ça ne va pas » revient à ne rien dire.
    """
    reponse = client.get(f"{BASE}/ready")
    assert reponse.status_code == 503
    assert reponse.json()["status"] == "unready"
    assert reponse.json()["database"] == "unreachable"


def test_health_stays_200_but_says_degraded_when_the_database_is_down(
    client: TestClient, base_injoignable: None
) -> None:
    """`/health` est une page d'état, pas une sonde — et le reste.

    Ce test fixe la différence : si un jour `/health` se met à répondre 503,
    il devient une sonde, et il faudra décider laquelle des deux disparaît.
    """
    reponse = client.get(f"{BASE}/health")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["status"] == "degraded"
    assert corps["database"] == "unreachable"
    assert "database unreachable" in corps["configuration_problems"]


def test_the_three_probes_disagree_during_an_outage(
    client: TestClient, base_injoignable: None
) -> None:
    """Le contrôle qui tient l'ensemble.

    Trois points, trois réponses distinctes pendant la même panne. C'est la
    propriété utile ; les tests précédents n'en vérifient chacun qu'un tiers.
    """
    codes = {
        chemin: client.get(f"{BASE}/{chemin}").status_code for chemin in ("live", "ready", "health")
    }
    assert codes == {"live": 200, "ready": 503, "health": 200}


def test_ready_needs_no_token(client: TestClient) -> None:
    """Une sonde n'a pas de jeton, et n'en aura jamais.

    L'orchestrateur qui décide de retirer une instance du service ne
    s'authentifie pas.
    """
    assert client.get(f"{BASE}/ready").status_code == 200
