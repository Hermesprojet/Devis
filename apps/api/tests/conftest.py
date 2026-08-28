"""Test harness.

Each test gets a fresh database created **through the Alembic migrations**,
not through ``create_all``: a migration that does not reproduce the models is
a bug the suite should catch, not hide.

The suite runs on SQLite by default so that ``pytest`` needs no service. Set
``METREO_TEST_DATABASE_URL`` to a PostgreSQL URL and the whole suite runs there
instead, each test in its own schema. That switch is what makes the PostgreSQL
job in CI a real check: SQLite proves nothing about ``NUMERIC`` precision,
server-side constraints or transactional DDL.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]

#: PostgreSQL URL the suite should run against, when asked.
TEST_DATABASE_URL = os.environ.get("METREO_TEST_DATABASE_URL", "").strip()

#: Incremented per test so two schemas never collide inside one run.
_schema_counter = 0


def _next_schema_name() -> str:
    global _schema_counter
    _schema_counter += 1
    return f"metreo_test_{os.getpid()}_{_schema_counter}"


@pytest.fixture()
def database_url(tmp_path: Path) -> Iterator[str]:
    """A URL pointing at a database this test alone owns."""
    if not TEST_DATABASE_URL:
        yield f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}"
        return

    from sqlalchemy import create_engine, text

    # Le schéma est créé par ce test et détruit par lui : même principe que
    # `scripts/migration_roundtrip.py` — on ne supprime que ce qu'on a créé, et
    # le nom vient d'ici, pas d'un appelant.
    schema = _next_schema_name()
    admin = create_engine(TEST_DATABASE_URL, future=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    separator = "&" if "?" in TEST_DATABASE_URL else "?"
    try:
        # ``options`` reaches libpq, which applies it as the session
        # search_path: migrations and queries then land in this test's own
        # schema. The value is left unencoded on purpose — Alembic stores the
        # URL in a ConfigParser, which would read a percent sign as an
        # interpolation.
        yield f"{TEST_DATABASE_URL}{separator}options=-csearch_path={schema}"
    finally:
        # `finally` plutôt qu'une simple suite : une erreur de collecte ou une
        # interruption laisserait sinon un schéma orphelin par test.
        admin = create_engine(TEST_DATABASE_URL, future=True)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


#: Les scripts du dépôt portent la seule définition de « PostgreSQL prouvé » et
#: de « ressource jetable ». Les redire ici les ferait diverger.
_SCRIPTS = API_ROOT.parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@lru_cache(maxsize=1)
def _verified_server() -> str | None:
    """La bannière du serveur PostgreSQL vérifié, ou ``None`` sans URL.

    `bool(METREO_TEST_DATABASE_URL)` ne prouvait qu'une variable non vide.
    Reproduit : pointer la variable sur `sqlite+pysqlite:///…/metreo_test.sqlite3`
    faisait *sélectionner* les tests PostgreSQL-only, qui tombaient ensuite en
    quinze erreurs — un diagnostic tardif sous une étiquette qui affirmait un
    moteur que rien ne contrôlait.

    Trois étages, dans cet ordre :

    1. pas d'URL → ``None``. C'est le mode SQLite, légitime et silencieux ;
    2. une URL → elle doit être PostgreSQL, joignable, et le serveur doit le
       confirmer lui-même. Sinon on **lève** : une URL fournie exprime une
       intention, et l'ignorer en sautant les tests serait un faux vert ;
    3. la base visée doit être jetable au sens du dépôt — la suite y crée et
       y détruit un schéma par test.
    """
    from _url_safety import NotPostgreSQL, redacted, verified_postgresql_dialect
    from check_disposable_database import refusal as disposable_refusal

    if not TEST_DATABASE_URL:
        return None

    try:
        banner = verified_postgresql_dialect(TEST_DATABASE_URL)
    except NotPostgreSQL as error:
        raise RuntimeError(
            f"METREO_TEST_DATABASE_URL ne mène pas à PostgreSQL : {error}. "
            "Retirez la variable pour tourner sur SQLite, ou corrigez-la — "
            "les tests ne seront pas ignorés en silence."
        ) from None

    not_disposable = disposable_refusal(TEST_DATABASE_URL)
    if not_disposable is not None:
        raise RuntimeError(
            f"METREO_TEST_DATABASE_URL vise une base que la suite ne doit pas "
            f"toucher : {not_disposable}. La suite crée et détruit un schéma par "
            f"test — {redacted(TEST_DATABASE_URL)}"
        )
    return banner


def running_on_postgresql() -> bool:
    """Vrai seulement contre un serveur PostgreSQL **vérifié**, jetable."""
    return _verified_server() is not None


@pytest.fixture()
def app_env(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("METREO_DATABASE_URL", database_url)
    monkeypatch.setenv("METREO_ENVIRONMENT", "test")
    monkeypatch.setenv("METREO_AUTH_MODE", "dev")
    monkeypatch.setenv("METREO_JWT_SECRET", "test-secret-not-used-in-production-0123456789")

    from metreo_api import config, db

    config.get_settings.cache_clear()
    db.reset_engine()
    yield
    db.reset_engine()
    config.get_settings.cache_clear()


def _upgrade(database_url: str) -> None:
    """Appliquer la chaîne complète des migrations sur cette URL."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    previous = os.environ.get("METREO_DATABASE_URL")
    os.environ["METREO_DATABASE_URL"] = database_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if previous is not None:
            os.environ["METREO_DATABASE_URL"] = previous
        else:
            os.environ.pop("METREO_DATABASE_URL", None)


def alembic_head() -> str:
    """La tête de la chaîne, lue dans les scripts de migration.

    Écrite en dur quelque part, elle devient une constante que l'on recopie à
    la main à chaque révision — un contrôle qui ne contrôle plus rien. Lue ici,
    elle laisse les tests comparer deux choses qui doivent coïncider.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"la chaîne des migrations a {len(heads)} têtes : {heads}"
    return heads[0]


def schema_fingerprint() -> str:
    """Ce qui doit invalider un gabarit : la tête ET la forme des modèles.

    La tête seule ne suffit pas. Modifier une colonne dans les modèles sans
    écrire de révision laisse la tête inchangée ; un gabarit conservé serait
    alors en avance ou en retard sur ce que les tests croient interroger. Le
    contrôle qui confronte migrations et modèles existe, mais il tournerait
    CONTRE un gabarit périmé, et son diagnostic serait illisible.

    L'empreinte couvre donc les deux : la révision de tête, et la liste
    ordonnée des tables et de leurs colonnes telle que les modèles la
    déclarent.
    """
    from metreo_api.models import Base

    shape = ";".join(
        f"{name}:{','.join(sorted(column.name for column in table.columns))}"
        for name, table in sorted(Base.metadata.tables.items())
    )
    return hashlib.sha256(f"{alembic_head()}|{shape}".encode()).hexdigest()


def template_is_current(template: Path) -> bool:
    """Ce gabarit correspond-il encore à la tête et aux modèles ?

    Extraite pour être éprouvable. Restée en ligne dans la fixture, la décision
    ne se testait qu'indirectement, et un test qui prétendait la couvrir
    restait vert quand on la débranchait. Une empreinte absente ou illisible
    vaut « périmé » : on ne recopie jamais un gabarit dont on ne peut pas dire
    à quoi il correspond.
    """
    stamp = template.with_suffix(".fingerprint")
    try:
        return stamp.read_text(encoding="utf-8").strip() == schema_fingerprint()
    except OSError:
        return False


#: Les fichiers annexes que SQLite laisse à côté d'une base ouverte.
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


@pytest.fixture(scope="session")
def sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    """Un fichier SQLite migré **une fois**, recopié pour chaque test.

    La chaîne des migrations coûtait 357 ms, rejoués par chacun des quelque six
    cents tests : près de quatre minutes passées à reconstruire le même schéma.
    Le gabarit est produit par les migrations — elles restent la source de
    vérité, et `test_migrations_reproduce_the_models_exactly` continue de les
    confronter aux modèles — mais il n'est produit qu'une fois.

    L'isolation ne bouge pas : chaque test reçoit sa **copie**, un fichier
    distinct dans son propre répertoire temporaire, jamais un fichier partagé.

    **Ce que le gabarit doit garantir avant d'être copié**, parce qu'il est
    devenu une infrastructure dont dépendent six cents tests :

    * plus aucune connexion ouverte dessus — sinon la copie peut attraper une
      transaction en cours ;
    * aucun fichier annexe `-wal`, `-shm` ou `-journal` à côté — leur contenu
      ne serait pas copié, et la copie serait un schéma tronqué ;
    * une empreinte qui couvre la tête d'Alembic **et** la forme des modèles,
      écrite à côté du gabarit et revérifiée : un gabarit qui ne correspond
      plus est refusé, jamais réutilisé en silence.

    Le gabarit vit dans le répertoire temporaire de la SESSION pytest. Il n'y a
    donc rien à invalider entre deux exécutions de CI : chacune repart d'un
    répertoire vide. L'empreinte protège d'un gabarit périmé À L'INTÉRIEUR
    d'une exécution, pas d'un cache entre exécutions — il n'y en a pas, et il
    ne doit pas y en avoir.

    Sans URL PostgreSQL seulement : sur PostgreSQL, chaque test a déjà son
    schéma, créé et détruit par lui.
    """
    if TEST_DATABASE_URL:
        return None
    path = tmp_path_factory.mktemp("gabarit") / "template.sqlite3"
    _upgrade(f"sqlite+pysqlite:///{path}")

    # `_upgrade` passe par Alembic, qui ouvre son propre moteur. Le disposer
    # explicitement : une connexion encore ouverte laisserait un `-wal`.
    from metreo_api import db

    db.reset_engine()

    leftovers = [
        suffix for suffix in SQLITE_SIDECARS if path.with_name(path.name + suffix).exists()
    ]
    assert not leftovers, (
        f"le gabarit laisse {leftovers} à côté de lui : une connexion est restée "
        "ouverte, et la copie ne verrait pas ce que ces fichiers contiennent"
    )
    path.with_suffix(".fingerprint").write_text(schema_fingerprint(), encoding="utf-8")
    return path


@pytest.fixture()
def migrated(app_env: None, database_url: str, sqlite_template: Path | None) -> Iterator[None]:
    if sqlite_template is not None and database_url.startswith("sqlite"):
        # `database_url` pointe sur un fichier que ce test possède seul :
        # le remplacer par une copie du gabarit revient au même schéma, sans
        # rejouer la chaîne.
        if template_is_current(sqlite_template):
            shutil.copyfile(sqlite_template, database_url.split("///", 1)[1])
        else:
            # Rejouer la chaîne plutôt que copier : un gabarit périmé donnerait
            # un schéma qui ne correspond ni aux migrations ni aux modèles, et
            # les échecs qui en découleraient seraient illisibles.
            _upgrade(database_url)
    else:
        _upgrade(database_url)
    yield


@pytest.fixture()
def seeded(migrated: None) -> dict[str, str]:
    from metreo_api.db import get_session_factory
    from metreo_api.seed import seed

    session = get_session_factory()()
    try:
        return seed(session)
    finally:
        session.close()


@pytest.fixture()
def client(migrated: None) -> Iterator[TestClient]:
    from metreo_api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def seeded_client(seeded: dict[str, str], client: TestClient) -> TestClient:
    return client


def login(client: TestClient, email: str, organization_id: str | None = None) -> dict[str, str]:
    """Return the Authorization header for a seeded user."""
    payload: dict[str, str] = {"email": email}
    if organization_id:
        payload["organization_id"] = organization_id
    response = client.post("/api/v1/auth/dev-login", json=payload)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
