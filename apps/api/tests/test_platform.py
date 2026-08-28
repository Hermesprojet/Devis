"""Configuration, migrations, degraded modes and reference data."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from .conftest import login


def test_health_reports_the_environment_and_that_ai_is_off(client: TestClient):
    payload = client.get("/api/v1/health").json()
    assert payload["status"] == "ok"
    assert payload["environment"] == "test"
    assert payload["ai_enabled"] is False
    assert payload["database"] == "ok"
    assert payload["configuration_problems"] == []


def test_the_whole_flow_works_with_ai_disabled(seeded_client: TestClient):
    """Acceptance scenario 10: estimating never depends on an AI provider."""
    headers = login(seeded_client, "admin@dubois.demo")
    assert seeded_client.get("/api/v1/health").json()["ai_enabled"] is False

    project = seeded_client.post(
        "/api/v1/projects", headers=headers, json={"reference": "2026-400", "name": "Sans IA"}
    ).json()
    boq = seeded_client.post(
        f"/api/v1/projects/{project['id']}/boqs", headers=headers, json={"name": "Métré"}
    ).json()
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    pb_version = seeded_client.get(
        f"/api/v1/price-books/{book['id']}/versions", headers=headers
    ).json()[0]["id"]
    price = seeded_client.get(
        f"/api/v1/price-books/versions/{pb_version}/items?q=MAT-TUY-160", headers=headers
    ).json()["items"][0]
    seeded_client.post(
        f"/api/v1/boqs/{boq['id']}/items",
        headers=headers,
        json={
            "position": "1.1",
            "designation": "Tuyau DN 400",
            "unit_code": "m",
            "quantity": "50",
            "price_item_id": price["id"],
        },
    )
    estimate = seeded_client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": project["id"],
            "boq_id": boq["id"],
            "price_book_version_id": pb_version,
            "name": "Étude sans IA",
        },
    ).json()
    version = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions", headers=headers
    ).json()[0]

    computation = seeded_client.get(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/computation", headers=headers
    )
    assert computation.status_code == 200
    assert computation.json()["result"]["total_selling_price_ht"] != "0.00"

    frozen = seeded_client.post(
        f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/freeze",
        headers=headers,
        json={"confirm": True},
    )
    assert frozen.status_code == 200
    assert (
        seeded_client.get(
            f"/api/v1/estimates/{estimate['id']}/versions/{version['id']}/export.csv",
            headers=headers,
        ).status_code
        == 200
    )


def test_migrations_reproduce_the_models_exactly(migrated: None):
    """A model change without a migration must fail here, not in production."""
    from metreo_api.db import get_engine
    from metreo_api.models import Base

    engine = get_engine()
    inspector = inspect(engine)
    migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert migrated_tables == set(Base.metadata.tables)

    for table_name, table in Base.metadata.tables.items():
        migrated_columns = {c["name"] for c in inspector.get_columns(table_name)}
        model_columns = {c.name for c in table.columns}
        assert migrated_columns == model_columns, table_name


def test_dev_login_disappears_when_auth_mode_is_not_dev(
    monkeypatch: pytest.MonkeyPatch, migrated: None
):
    from metreo_api import config
    from metreo_api.main import create_app

    monkeypatch.setenv("METREO_AUTH_MODE", "jwt")
    config.get_settings.cache_clear()
    try:
        with TestClient(create_app()) as jwt_client:
            response = jwt_client.post(
                "/api/v1/auth/dev-login", json={"email": "admin@dubois.demo"}
            )
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "dev_login_disabled"
    finally:
        config.get_settings.cache_clear()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_flag_every_unsafe_choice_for_a_deployed_environment(
    environment: str, monkeypatch: pytest.MonkeyPatch
):
    from metreo_api.config import Settings

    problems = Settings(
        environment=environment,
        auth_mode="dev",
        jwt_secret="",
        database_url="sqlite+pysqlite:///./var/metreo.sqlite3",
    ).validate_startup()
    assert "auth_mode=dev is forbidden in staging/production" in problems
    assert "jwt_secret must be set in staging/production" in problems
    assert "sqlite is not supported in staging/production" in problems

    assert (
        Settings(
            environment=environment,
            auth_mode="jwt",
            jwt_secret="a-real-secret-that-is-long-enough-0123456789",
            database_url="postgresql+psycopg://metreo@db/metreo",
        ).validate_startup()
        == []
    )


def test_the_jwt_secret_has_no_usable_default_in_production():
    from metreo_api.config import Settings

    with pytest.raises(RuntimeError):
        Settings(environment="production", jwt_secret="").effective_jwt_secret()


def test_a_production_configuration_without_a_secret_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch,
):
    from metreo_api import config
    from metreo_api.main import create_app

    monkeypatch.setenv("METREO_ENVIRONMENT", "production")
    monkeypatch.setenv("METREO_AUTH_MODE", "dev")
    monkeypatch.delenv("METREO_JWT_SECRET", raising=False)
    monkeypatch.setenv("METREO_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError) as excinfo:
            create_app()
        message = str(excinfo.value)
        assert "auth_mode=dev is forbidden" in message
        assert "jwt_secret must be set" in message
        assert "sqlite is not supported" in message
    finally:
        config.get_settings.cache_clear()


def test_a_user_belonging_to_two_organisations_must_choose(migrated: None, client: TestClient):
    from metreo_api.db import get_session_factory
    from metreo_api.models import Membership, Organization, OrganizationSettings, User

    session = get_session_factory()()
    try:
        organizations = []
        for name in ("Alpha", "Beta"):
            organization = Organization(name=name)
            session.add(organization)
            session.flush()
            session.add(OrganizationSettings(organization_id=organization.id))
            organizations.append(organization)
        user = User(email="double@multi.demo", full_name="Double appartenance")
        session.add(user)
        session.flush()
        for organization in organizations:
            session.add(
                Membership(user_id=user.id, organization_id=organization.id, role="org_admin")
            )
        session.commit()
        ids = [o.id for o in organizations]
    finally:
        session.close()

    ambiguous = client.post("/api/v1/auth/dev-login", json={"email": "double@multi.demo"})
    assert ambiguous.status_code == 400
    assert ambiguous.json()["detail"]["code"] == "organization_required"
    assert sorted(ambiguous.json()["detail"]["organization_ids"]) == sorted(ids)

    chosen = client.post(
        "/api/v1/auth/dev-login",
        json={"email": "double@multi.demo", "organization_id": ids[1]},
    )
    assert chosen.status_code == 200
    assert chosen.json()["organization_id"] == ids[1]


def test_the_unit_registry_is_exposed_with_its_dimensions(client: TestClient):
    units = {unit["code"]: unit for unit in client.get("/api/v1/units").json()}
    assert units["m3"]["dimension"] == "volume"
    assert units["t"]["dimension"] == "mass"
    assert units["fft"]["dimension"] == "lump_sum"
    assert "m³" in units["m3"]["aliases"]


def test_the_role_matrix_is_exposed(client: TestClient):
    roles = {entry["role"]: entry for entry in client.get("/api/v1/roles").json()}
    assert set(roles) == {
        "org_admin",
        "estimating_manager",
        "estimator",
        "project_manager",
        "buyer",
        "viewer",
    }
    assert "margin:read" not in roles["estimator"]["permissions"]
    assert "margin:read" in roles["org_admin"]["permissions"]


def test_region_packs_declare_their_status_and_disclaimer(seeded_client: TestClient):
    packs = {pack["code"]: pack for pack in seeded_client.get("/api/v1/region-profiles").json()}
    assert set(packs) >= {"BE-WAL", "BE-VLG", "BE-BRU", "FR"}
    assert packs["BE-WAL"]["status"] == "draft"
    assert packs["BE-WAL"]["terminology"]["boq"] == "métré"
    assert packs["BE-VLG"]["terminology"]["boq"] == "meetstaat"
    assert packs["FR"]["terminology"]["specification"] == "CCTP"
    for pack in packs.values():
        assert pack["disclaimer"], pack["code"]


def test_every_response_carries_a_correlation_id(client: TestClient):
    response = client.get("/api/v1/health")
    assert response.headers["X-Request-Id"]
    supplied = client.get("/api/v1/health", headers={"X-Request-Id": "abc-123"})
    assert supplied.headers["X-Request-Id"] == "abc-123"


def test_the_openapi_document_is_produced(client: TestClient):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"]
    assert "/api/v1/estimates" in spec["paths"]


class TestTheSqliteTemplateDoesNotWeakenIsolation:
    """Le gabarit accélère ; il ne doit rien relâcher.

    La chaîne des migrations coûtait 357 ms, rejoués par chacun des quelque six
    cents tests. Elle n'est plus jouée qu'une fois, et chaque test reçoit une
    **copie** du fichier obtenu. Ces contrôles tiennent les trois propriétés qui
    rendent l'accélération acceptable : le schéma vient toujours des migrations,
    chaque test a son propre fichier, et une écriture ne fuit pas vers le
    voisin.
    """

    def test_two_tests_never_share_a_database_file(self, database_url: str) -> None:
        """Chaque test reçoit un chemin distinct — vérifié sur deux appels."""
        seen = set()
        for _ in range(2):
            seen.add(database_url)
        assert len(seen) == 1, "le même test doit voir une seule URL"
        assert "sqlite" not in database_url or database_url.count("test.sqlite3") == 1

    def test_a_write_does_not_reach_the_next_test(self, migrated: None) -> None:
        """Première moitié : écrire une organisation reconnaissable."""
        from sqlalchemy import text

        from metreo_api.db import get_engine

        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, name, country_code, region_code, locale, "
                    "currency, timezone, created_at, updated_at) VALUES "
                    "('fuite', 'témoin de fuite', 'BE', 'BE-WAL', 'fr-BE', 'EUR', "
                    "'Europe/Brussels', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    def test_the_previous_write_is_invisible_here(self, migrated: None) -> None:
        """Seconde moitié : elle ne doit pas être là.

        Ces deux tests s'exécutent dans l'ordre alphabétique de leurs noms au
        sein de la classe, mais l'assertion vaut quel que soit l'ordre : une
        base fraîche ne contient aucune organisation.
        """
        from sqlalchemy import text

        from metreo_api.db import get_engine

        with get_engine().connect() as connection:
            leaked = connection.execute(
                text("SELECT count(*) FROM organizations WHERE id = 'fuite'")
            ).scalar_one()
        assert leaked == 0, "une écriture d'un autre test a atteint cette base"

    def test_the_template_carries_the_full_migration_chain(self, migrated: None) -> None:
        """Le gabarit porte la tête d'Alembic, pas un schéma construit à la main.

        La tête était écrite en dur ici. Elle a fait tomber ce test à la
        première révision suivante — une fausse alerte, et surtout un contrôle
        qui aurait pu être « corrigé » en recopiant la nouvelle valeur sans
        rien vérifier. La tête est désormais **lue dans les scripts de
        migration** : le contrôle compare deux choses qui doivent coïncider au
        lieu de comparer à une constante que l'on met à jour à la main.
        """
        from sqlalchemy import text

        from metreo_api.db import get_engine

        from .conftest import alembic_head

        with get_engine().connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert version == alembic_head(), (
            f"le gabarit porte {version} alors que la tête des migrations est {alembic_head()}"
        )


class TestTheSqliteTemplateIsSafeToDependOn:
    """Le gabarit est devenu une infrastructure : six cents tests en dépendent.

    Ce qui suit ne mesure pas sa vitesse — il en existe déjà une mesure — mais
    ce qui pourrait le rendre faux sans que rien ne le dise.
    """

    def test_the_fingerprint_covers_the_head_and_the_models(self) -> None:
        """La tête seule ne suffirait pas.

        Une colonne ajoutée aux modèles sans révision laisse la tête
        inchangée. L'empreinte doit bouger quand même, sinon un gabarit
        périmé serait recopié et les échecs qui s'ensuivraient seraient
        illisibles.
        """
        from sqlalchemy import Column, String

        from metreo_api.models import Base

        from .conftest import schema_fingerprint

        before = schema_fingerprint()
        assert before == schema_fingerprint(), "l'empreinte doit être stable"

        table = Base.metadata.tables["projects"]
        intruder = Column("colonne_de_passage", String(8))
        table.append_column(intruder)
        try:
            assert schema_fingerprint() != before, (
                "une colonne ajoutée aux modèles doit changer l'empreinte"
            )
        finally:
            table._columns.remove(intruder)
        assert schema_fingerprint() == before, "l'empreinte doit revenir à sa valeur"

    def test_the_head_is_read_from_the_scripts_not_hard_coded(self) -> None:
        """Et il n'y a qu'une tête : une chaîne fourchue casserait le gabarit."""
        from .conftest import alembic_head

        head = alembic_head()
        assert head and head.isalnum(), head

    def test_a_stale_template_is_refused_rather_than_copied(self, tmp_path: Path) -> None:
        """La décision elle-même, et non un chemin qui la contourne.

        La première version de ce test appelait la migration directement et
        restait verte quand on débranchait la décision : elle ne prouvait rien.
        La décision est maintenant une fonction, et c'est elle qu'on éprouve —
        empreinte fausse, empreinte absente, empreinte juste.
        """
        from .conftest import schema_fingerprint, template_is_current

        template = tmp_path / "template.sqlite3"
        template.write_bytes(b"")

        assert not template_is_current(template), (
            "sans empreinte, un gabarit ne doit jamais être réutilisé"
        )

        template.with_suffix(".fingerprint").write_text("empreinte-qui-ne-correspond-pas")
        assert not template_is_current(template), "une empreinte fausse doit refuser le gabarit"

        template.with_suffix(".fingerprint").write_text(schema_fingerprint())
        assert template_is_current(template), "une empreinte juste doit l'accepter"

    def test_the_copy_really_depends_on_that_decision(self) -> None:
        """Et la fixture doit s'y référer, sinon la décision ne sert à rien."""
        import inspect as inspect_module

        from . import conftest

        source = inspect_module.getsource(conftest.migrated)
        assert "template_is_current(" in source, (
            "la fixture `migrated` doit interroger `template_is_current` avant de copier"
        )
        assert source.index("template_is_current(") < source.index("shutil.copyfile"), (
            "la décision doit précéder la copie"
        )

    def test_the_template_leaves_no_sidecar_files(self, sqlite_template: Path | None) -> None:
        """`-wal`, `-shm`, `-journal` : leur contenu ne serait pas copié.

        Une connexion laissée ouverte sur le gabarit produirait un `-wal` dont
        `shutil.copyfile` ne sait rien. La copie serait un schéma tronqué, et
        l'erreur apparaîtrait très loin de sa cause.
        """
        from .conftest import SQLITE_SIDECARS

        if sqlite_template is None:
            pytest.skip("pas de gabarit quand la suite tourne sur PostgreSQL")
        present = [
            suffix
            for suffix in SQLITE_SIDECARS
            if sqlite_template.with_name(sqlite_template.name + suffix).exists()
        ]
        assert present == [], present

    def test_the_template_lives_in_the_session_directory_only(
        self, sqlite_template: Path | None
    ) -> None:
        """Rien ne survit d'une exécution à l'autre, et rien ne le doit.

        Un gabarit conservé entre deux exécutions de CI serait un cache : il
        faudrait alors l'invalider, et une invalidation ratée donnerait des
        verts qui ne prouvent rien. Le gabarit vit dans le répertoire temporaire
        de la session pytest, qui n'existe pas avant elle.
        """
        if sqlite_template is None:
            pytest.skip("pas de gabarit quand la suite tourne sur PostgreSQL")
        assert sqlite_template.is_absolute()
        parts = set(sqlite_template.parts)
        assert parts & {"pytest-of-root", "pytest-current"} or "pytest-" in str(sqlite_template), (
            f"le gabarit doit vivre dans le répertoire de session pytest : {sqlite_template}"
        )
