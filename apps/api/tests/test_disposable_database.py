"""Le garde-fou « base jetable » doit lire le nom de la base, et rien d'autre.

Sa première version cherchait `test`, `gate`, `ci`, `tmp` ou `scratch`
n'importe où dans l'URL. Ce n'est pas un contrôle : un hôte de production
`db-prod.cimenteries-sa.be` contient « ci », un utilisateur `tester` contient
« test », un mot de passe engendré peut contenir « tmp ». La cible qu'il
protège crée et détruit des schémas, et lance `alembic downgrade base`.

Chaque cas ci-dessous a été refusé ou accepté à tort par cette première
version.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("check_disposable_database")

PG = "postgresql+psycopg"


class TestRefusedUrls:
    @pytest.mark.parametrize(
        "url",
        [
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/metreo", id="base-ordinaire"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/metreo_production", id="production"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/facturation", id="facturation"),
            # Le cœur du défaut : le marqueur est ailleurs que dans le nom.
            pytest.param(
                f"{PG}://metreo_app:S3cr3t@db-prod.cimenteries-sa.be:5432/metreo",
                id="hôte-contient-ci",
            ),
            pytest.param(f"{PG}://tester:motdepasse@db.example.be:5432/metreo", id="user-test"),
            pytest.param(f"{PG}://metreo:xKtmpQ9@db.example.be:5432/metreo", id="motdepasse-tmp"),
            pytest.param(f"{PG}://metreo:metreo@ci.example.com:5432/metreo", id="hôte-ci"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/scratchpad-app", id="mot-collé"),
            # La liste de refus l'emporte sur celle d'acceptation.
            pytest.param(f"{PG}://u:p@h:5432/metreo_ci_production", id="ci-mais-production"),
            pytest.param(f"{PG}://u:p@h:5432/test_live", id="test-mais-live"),
            # Le composant contrôlé doit être celui qui fait autorité. psycopg
            # obéit à « dbname= » de la chaîne de requête, qui écrase le chemin :
            # le garde-fou annonçait « metreo_gate » pendant qu'alembic vidait
            # « metreo ». Reproduit, avec destruction réelle d'une base témoin.
            pytest.param(f"{PG}://u:p@h:5432/metreo_gate?dbname=metreo", id="dbname-écrasé"),
            pytest.param(f"{PG}://u:p@h:5432/metreo_gate?host=/tmp&dbname=x", id="hôte-et-base"),
            pytest.param(f"{PG}://u:p@h:5432/metreo_gate?service=prod", id="service-pg"),
            pytest.param(f"{PG}://u:p@h:5432/metreo_gate?user=root", id="utilisateur"),
            pytest.param("pas une url du tout", id="illisible"),
            pytest.param("", id="vide"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/", id="sans-base"),
        ],
    )
    def test_a_url_that_does_not_name_a_disposable_database_is_refused(self, url: str) -> None:
        assert guard.refusal(url) is not None, url


class TestAcceptedUrls:
    @pytest.mark.parametrize(
        "url",
        [
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/metreo_gate", id="gate"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/metreo_test", id="test"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/metreo-ci", id="ci-tiret"),
            pytest.param(f"{PG}://metreo:metreo@localhost:5432/tmp_metreo", id="tmp-devant"),
            pytest.param(
                f"{PG}://u:p@prod-host.example.com:5432/metreo_gate", id="hôte-prod-base-gate"
            ),
            # La conftest ajoute une chaîne de requête : elle ne fait pas partie du nom.
            pytest.param(
                f"{PG}://metreo:metreo@localhost:5432/metreo_gate?options=-csearch_path=s1",
                id="avec-search-path",
            ),
        ],
    )
    def test_a_disposable_database_is_accepted(self, url: str) -> None:
        assert guard.refusal(url) is None, guard.refusal(url)


class TestTheAuthoritativeComponent:
    """Le nom contrôlé doit être celui que le pilote ouvre réellement."""

    def test_a_query_parameter_cannot_move_the_target_silently(self) -> None:
        from sqlalchemy.engine import make_url

        url = f"{PG}://metreo:metreo@localhost:5432/metreo_gate?dbname=metreo"
        parsed = make_url(url)
        # Le chemin dit une chose…
        assert parsed.database == "metreo_gate"
        # …le pilote en fait une autre.
        arguments = parsed.get_dialect()().create_connect_args(parsed)[1]
        assert arguments["dbname"] == "metreo"
        # Le garde-fou ne doit donc pas se laisser rassurer par le chemin.
        assert guard.refusal(url) is not None

    def test_the_search_path_parameter_stays_legitimate(self) -> None:
        """La conftest en ajoute un : il ne déplace pas la base, il la scope."""
        url = f"{PG}://metreo:metreo@localhost:5432/metreo_gate?options=-csearch_path%3Ds1"
        assert guard.refusal(url) is None


class TestNameExtraction:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (f"{PG}://u:p@h:5432/metreo_gate", "metreo_gate"),
            (f"{PG}://u:p@h:5432/metreo_gate?options=-csearch_path%3Dx", "metreo_gate"),
            ("sqlite+pysqlite:///./var/metreo.sqlite3", "metreo.sqlite3"),
            (f"{PG}://u:p@ci.example.com:5432/metreo", "metreo"),
        ],
    )
    def test_only_the_database_name_is_read(self, url: str, expected: str) -> None:
        assert guard.database_name(url) == expected
