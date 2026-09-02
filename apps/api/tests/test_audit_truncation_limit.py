"""Ce que la chaîne d'audit ne détecte pas, écrit noir sur blanc.

Une chaîne de hachage détecte la **modification** d'un maillon et la
**suppression au milieu ou au début** : le numéro de séquence attendu ne tombe
plus juste, ou le `previous_hash` ne correspond plus. Elle ne détecte pas la
suppression des **derniers** maillons. Les événements restants forment une
chaîne parfaitement cohérente, numérotée de 1 à n, et `verify_chain` rend
`valid: True`.

Mesuré sur le journal réel : quatre événements, les deux derniers supprimés,
`{'valid': True, 'checked': 2}`. Seuls `checked` et `head_hash` changent, et
rien dans le système ne les compare d'une fois à l'autre.

**Le dossier de menaces l'affirmait autrement.** Il écrivait « deux tests
reproduisent une modification et une suppression, et vérifient la détection ».
Le test de suppression existant supprime le **premier** événement, ce qui crée
un trou de séquence. Rien ne couvrait la troncature en fin, et la formulation
laissait croire le contraire.

**Rien de bon marché ne ferme ce trou à l'intérieur de la base.** Sceller le
compte demanderait une ligne qui connaisse le total ; cette ligne est aussi
supprimable que les autres. La seule fermeture réelle est un ancrage hors base —
export signé ou stockage en écriture unique — que la feuille de route porte déjà
en phase 5.

Ce fichier ne corrige donc rien : il **épingle la limite**. Si l'un de ces tests
passe au rouge parce que la détection s'est améliorée, c'est une bonne nouvelle
et il faut le réécrire, pas le contourner.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from .conftest import login


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


def _evenements(organization_id: str):
    from metreo_api.db import get_session_factory
    from metreo_api.models import AuditEvent

    session = get_session_factory()()
    try:
        return session, session.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization_id)
            .order_by(AuditEvent.sequence.desc())
        ).all()
    except Exception:  # pragma: no cover - le nettoyage prime sur le diagnostic
        session.close()
        raise


class TestWhatTheChainDoesDetect:
    """Sans ceci, la limite ci-dessous passerait pour une absence de protection."""

    def test_deleting_the_last_event_and_rewriting_nothing_else(
        self, seeded_client: TestClient, headers: dict[str, str], seeded: dict[str, str]
    ) -> None:
        """La suppression au MILIEU laisse un trou de séquence, et il est vu."""
        for numero in range(3):
            seeded_client.post(
                "/api/v1/projects",
                headers=headers,
                json={"reference": f"2026-40{numero}", "name": "T"},
            )
        session, evenements = _evenements(seeded["organization_a"])
        try:
            milieu = evenements[len(evenements) // 2]
            session.delete(milieu)
            session.commit()
        finally:
            session.close()

        rapport = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
        assert rapport["valid"] is False
        assert rapport["reason"] == "sequence_gap"


class TestWhatTheChainDoesNotDetect:
    """La limite, épinglée. Rouge ici = la détection s'est améliorée."""

    def test_truncating_the_tail_is_not_detected(
        self, seeded_client: TestClient, headers: dict[str, str], seeded: dict[str, str]
    ) -> None:
        for numero in range(3):
            seeded_client.post(
                "/api/v1/projects",
                headers=headers,
                json={"reference": f"2026-41{numero}", "name": "T"},
            )
        avant = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
        assert avant["valid"] is True
        assert avant["checked"] >= 3

        session, evenements = _evenements(seeded["organization_a"])
        try:
            for event in evenements[:2]:  # les deux DERNIERS
                session.delete(event)
            session.commit()
        finally:
            session.close()

        apres = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
        assert apres["valid"] is True, (
            "Bonne nouvelle si ce test tombe : la troncature en fin est désormais "
            "détectée. Réécrivez ce fichier et corrigez le dossier de menaces."
        )
        assert apres["checked"] == avant["checked"] - 2
        assert apres["head_hash"] != avant["head_hash"]

    def test_only_two_observable_quantities_change(
        self, seeded_client: TestClient, headers: dict[str, str], seeded: dict[str, str]
    ) -> None:
        """Et elles ne sont comparées à rien : c'est là que le trou se loge.

        `checked` et `head_hash` bougent, mais aucun composant ne conserve leur
        valeur d'une vérification à l'autre. Un ancrage hors base — export signé,
        stockage en écriture unique — est ce qui les rendrait utilisables ; il
        est en feuille de route, il n'est pas là.
        """
        seeded_client.post(
            "/api/v1/projects", headers=headers, json={"reference": "2026-420", "name": "T"}
        )
        avant = seeded_client.get("/api/v1/audit/verify", headers=headers).json()

        session, evenements = _evenements(seeded["organization_a"])
        try:
            session.delete(evenements[0])
            session.commit()
        finally:
            session.close()

        apres = seeded_client.get("/api/v1/audit/verify", headers=headers).json()
        differences = {cle for cle in set(avant) | set(apres) if avant.get(cle) != apres.get(cle)}
        assert differences == {"checked", "head_hash"}, (
            f"la troncature change {sorted(differences)} ; si un champ s'ajoute, "
            "il devient exploitable pour la détecter et ce test doit le dire"
        )
