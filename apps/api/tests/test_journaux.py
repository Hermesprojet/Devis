"""Ce que les journaux portent, et surtout ce qu'ils ne portent pas.

Un journal d'exploitation est lu par des gens qui n'ont pas accès aux données
de l'application, conservé plus longtemps qu'elle, et souvent expédié chez un
tiers. Ce qu'il contient est donc un choix de sécurité, pas un détail de
confort.

Ces tests tiennent deux promesses écrites dans `logging_config.py` — « tokens
and full payloads are never logged » — qui n'étaient jusqu'ici vérifiées par
rien.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from metreo_api.logging_config import JsonFormatter

from .conftest import login

JETON_VISIBLE = "jeton-qui-ne-doit-jamais-paraitre-0123456789"


class _Capture(logging.Handler):
    """Le vrai formateur de production, appelé au moment de l'émission.

    Formater après coup ne prouverait rien : `request_id` vient d'une variable
    de contexte, remise à sa valeur par défaut dès la requête terminée. Une
    première version de ces tests lisait donc `request_id: "-"` partout et
    aurait déclaré cassée une corrélation qui fonctionne.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lignes: list[dict] = []
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lignes.append(json.loads(self.format(record)))


@pytest.fixture()
def journal() -> Iterator[_Capture]:
    handler = _Capture()
    logger = logging.getLogger("metreo.api")
    niveau = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(niveau)


def _texte(journal: _Capture) -> str:
    return "\n".join(json.dumps(ligne, ensure_ascii=False) for ligne in journal.lignes)


def test_a_request_is_logged_as_one_json_object_with_its_correlation_id(
    seeded_client: TestClient, journal: _Capture
) -> None:
    reponse = seeded_client.get("/api/v1/health")

    assert reponse.status_code == 200
    lignes = [ligne for ligne in journal.lignes if ligne["message"] == "http_request"]
    assert lignes, "aucune ligne de requête journalisée"
    ligne = lignes[-1]
    assert ligne["path"] == "/api/v1/health"
    assert ligne["status_code"] == 200
    assert ligne["method"] == "GET"
    # L'identifiant renvoyé au client est celui du journal : sans cette
    # égalité, la corrélation annoncée ne relie rien.
    assert ligne["request_id"] == reponse.headers["X-Request-Id"]


def test_the_bearer_token_never_reaches_the_log(
    seeded_client: TestClient, journal: _Capture
) -> None:
    """Le jeton d'une requête authentifiée ne doit paraître nulle part.

    Un journal qui porte des jetons vivants transforme sa propre rétention en
    fenêtre d'usurpation.
    """
    entetes = login(seeded_client, "admin@dubois.demo")
    jeton = entetes["Authorization"].removeprefix("Bearer ")

    assert seeded_client.get("/api/v1/projects", headers=entetes).status_code == 200

    sortie = _texte(journal)
    assert jeton not in sortie
    assert "Authorization" not in sortie
    assert "Bearer" not in sortie


def test_a_query_string_is_not_logged(seeded_client: TestClient, journal: _Capture) -> None:
    """Seul le chemin est journalisé, jamais la chaîne de requête.

    C'est là que finissent les valeurs qu'on ne contrôle pas : un jeton glissé
    en paramètre par un client tiers, un terme de recherche nominatif. Les
    journaliser reviendrait à les conserver.
    """
    entetes = login(seeded_client, "admin@dubois.demo")
    seeded_client.get(f"/api/v1/projects?search={JETON_VISIBLE}", headers=entetes)

    sortie = _texte(journal)
    assert JETON_VISIBLE not in sortie
    lignes = [ligne for ligne in journal.lignes if ligne["message"] == "http_request"]
    assert lignes[-1]["path"] == "/api/v1/projects"


def test_a_failing_request_still_leaves_a_correlated_trace(
    seeded_client: TestClient, journal: _Capture
) -> None:
    """Le seul cas où le journal sert vraiment est celui qui manquait."""
    reponse = seeded_client.get("/api/v1/projects/pas-un-identifiant")

    lignes = [ligne for ligne in journal.lignes if ligne.get("path")]
    assert lignes, "une requête refusée doit laisser une trace"
    assert lignes[-1]["request_id"] == reponse.headers["X-Request-Id"]


def test_a_supplied_correlation_id_is_honoured(seeded_client: TestClient) -> None:
    """Le proxy pose l'identifiant, l'application le reprend.

    Sans cela, la ligne du proxy et celle de l'application portent deux
    identifiants différents pour la même requête, et le rapprochement se fait
    à la main, sur l'horodatage.
    """
    fourni = "b7f3c1d2e4a5960718293a4b5c6d7e8f"
    reponse = seeded_client.get("/api/v1/health", headers={"X-Request-Id": fourni})
    assert reponse.headers["X-Request-Id"] == fourni


def test_an_oversized_correlation_id_is_replaced_not_truncated(
    seeded_client: TestClient,
) -> None:
    """Un en-tête hors format est remplacé, jamais coupé.

    Coupé, il rentrerait dans la colonne d'audit mais ne correspondrait plus à
    celui renvoyé au client : deux identifiants pour une requête, et une piste
    qui s'arrête au milieu.
    """
    trop_long = "z" * 200
    reponse = seeded_client.get("/api/v1/health", headers={"X-Request-Id": trop_long})
    rendu = reponse.headers["X-Request-Id"]
    assert rendu != trop_long
    assert not trop_long.startswith(rendu)
    assert len(rendu) <= 64


def test_configuring_the_logs_re_enables_a_logger_something_else_disabled() -> None:
    """Un journal éteint ne se voit pas : il n'y a rien à voir.

    `logging.config.fileConfig` désactive par défaut tous les journaux déjà
    existants. C'est ce que fait `alembic/env.py` à chaque migration. Dans la
    composition de recette les migrations tournent dans leur propre conteneur,
    mais rien ne garantit qu'un opérateur ne lancera pas `alembic upgrade` puis
    l'API dans le même processus — et là, l'application se tait sans qu'aucune
    erreur ne le dise.

    `configure_logging` doit donc rendre la parole, pas seulement poser un
    formateur.
    """
    from metreo_api.logging_config import configure_logging

    logger = logging.getLogger("metreo.api")
    logger.disabled = True
    try:
        configure_logging()
        assert logger.disabled is False
    finally:
        logger.disabled = False


def test_the_migration_runner_does_not_silence_the_application() -> None:
    """La cause, prise à sa source, et vérifiée là où elle vit.

    Une première version de ce test appelait `fileConfig` en passant
    lui-même `disable_existing_loggers=False`, puis constatait que le journal
    survivait. Il ne prouvait donc rien de notre code — seulement une propriété
    de la bibliothèque standard, et il serait resté vert après avoir retiré
    l'argument d'`env.py`.

    Celui-ci lit l'appel réel.
    """
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text(
        encoding="utf-8"
    )
    appel = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fileConfig"
    )
    passe = {mot.arg: ast.unparse(mot.value) for mot in appel.keywords}
    assert passe.get("disable_existing_loggers") == "False", (
        "fileConfig éteint par défaut tous les journaux déjà créés, dont "
        f"metreo.api : un processus qui migre puis sert se tait. Passé : {passe}"
    )
