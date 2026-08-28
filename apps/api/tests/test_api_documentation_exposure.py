"""Le schéma OpenAPI n'est publié que là où quelqu'un développe.

**Ce qui était ouvert, mesuré et non supposé.** L'application démarrée en
`production` — configuration par ailleurs valide, `auth_mode=jwt` et PostgreSQL —
servait :

* `GET /docs` → 200 ;
* `GET /redoc` → 200 ;
* `GET /openapi.json` → 200, **82 745 octets**, sans aucun jeton.

Ce n'est pas une fuite de données : les routes restent protégées, et un schéma
n'est pas un secret. C'est une carte — chemins, paramètres, formes d'erreur,
noms de modèles — offerte à qui la demande, et elle raccourcit le travail de
reconnaissance de quelqu'un qui cherche une faille ailleurs.

**`/redoc` n'était pas dans la note de suivi.** Elle citait « `/docs` et
`/openapi.json` montés inconditionnellement ». FastAPI monte `/redoc` par
défaut, sans qu'il soit nommé dans l'appel : l'exposition réelle était d'un
point d'entrée plus large que celle qui était écrite.

Nuance mesurée, et non supposée : `/redoc` ne se monte que si `openapi_url`
existe. Fermer ce dernier suffit donc à le fermer, et débrancher la seule
condition sur `redoc_url` laisse les tests d'exécution verts. Les trois sont
malgré tout nommés explicitement, pour que la fermeture ne repose pas sur un
détail interne de FastAPI ; c'est le contrôle d'AST, en fin de fichier, qui
tient cette exigence-là.

**Pourquoi `staging` est traité comme `production`.** Le même seuil
`is_production` interdit déjà `auth_mode=dev` et SQLite. Une pré-production est
jointe depuis l'extérieur comme une production. Aucun réglage ne permet de
rouvrir : un interrupteur se met du mauvais côté, et c'est cette classe d'erreur
qu'on retire plutôt que de la garder sous surveillance.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

#: Les trois chemins que FastAPI peut monter. `/docs/oauth2-redirect` en dépend
#: et disparaît avec eux.
CHEMINS_DE_DOCUMENTATION = ("/docs", "/redoc", "/openapi.json")

API_ROOT = Path(__file__).resolve().parents[1]


def _application(environment: str, monkeypatch: pytest.MonkeyPatch):
    """Une application construite pour un environnement donné, sans base."""
    from metreo_api.config import get_settings

    monkeypatch.setenv("METREO_ENVIRONMENT", environment)
    if environment in ("staging", "production"):
        # Une pré-production ou une production refuse de démarrer sans ces
        # trois-là — c'est un garde antérieur, et il a raison. On les fournit
        # donc pour atteindre le sujet du test, qui est ailleurs. La valeur du
        # secret n'a aucune importance ici : aucun jeton n'est émis ni vérifié,
        # et la base nommée n'existe pas, aucune connexion n'étant ouverte.
        monkeypatch.setenv("METREO_AUTH_MODE", "jwt")
        monkeypatch.setenv("METREO_JWT_SECRET", "valeur-de-test-sans-usage-0123456789-abcd")
        monkeypatch.setenv(
            "METREO_DATABASE_URL",
            "postgresql+psycopg://metreo:metreo@localhost:5432/metreo_inexistante",
        )
    get_settings.cache_clear()
    try:
        from metreo_api.main import create_app

        return create_app()
    finally:
        get_settings.cache_clear()


class TestTheSchemaIsNotPublishedInProduction:
    """Les trois chemins, et pas seulement les deux qui étaient nommés."""

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_no_documentation_path_answers(
        self, environment: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _application(environment, monkeypatch)
        with TestClient(app) as client:
            reponses = {
                chemin: client.get(chemin).status_code for chemin in CHEMINS_DE_DOCUMENTATION
            }
        assert reponses == dict.fromkeys(CHEMINS_DE_DOCUMENTATION, 404), (
            f"en {environment}, la documentation d'API répond encore : {reponses}"
        )

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_no_documentation_route_is_even_mounted(
        self, environment: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """404 pourrait venir d'ailleurs ; la route ne doit pas exister."""
        app = _application(environment, monkeypatch)
        montees = {
            route.path
            for route in app.routes
            if getattr(route, "path", "").startswith(("/docs", "/redoc", "/openapi"))
        }
        assert montees == set(), (
            f"routes de documentation encore montées en {environment} : {montees}"
        )


class TestTheSchemaStaysAvailableWhereItIsUseful:
    """Sans ceci, tout fermer partout passerait pour une correction."""

    @pytest.mark.parametrize("environment", ["development", "test"])
    def test_every_documentation_path_answers(
        self, environment: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _application(environment, monkeypatch)
        with TestClient(app) as client:
            reponses = {
                chemin: client.get(chemin).status_code for chemin in CHEMINS_DE_DOCUMENTATION
            }
        assert reponses == dict.fromkeys(CHEMINS_DE_DOCUMENTATION, 200), (
            f"en {environment}, la documentation d'API doit rester servie : {reponses}"
        )

    def test_the_schema_is_still_produced_in_memory_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le contrôle d'installation propre et la matrice en dépendent.

        Ils appellent `app.openapi()` et ne passent pas par HTTP. Retirer la
        route ne doit pas retirer la méthode — sinon la correction casserait
        deux contrôles qui n'ont rien à voir avec l'exposition.
        """
        app = _application("production", monkeypatch)
        schema = app.openapi()
        assert schema["paths"], "le schéma doit rester constructible en mémoire"
        assert len(schema["paths"]) > 20, len(schema["paths"])


class TestTheDecisionIsWrittenWhereItIsTaken:
    """Et elle ne doit pas pouvoir se rouvrir par un réglage."""

    def test_the_three_paths_are_conditioned_on_the_same_flag(self) -> None:
        """Les trois sur la même condition, et ce contrôle est le seul à le voir.

        Mesuré en débranchant `redoc_url` seul : les tests d'exécution restent
        verts. FastAPI ne monte `/redoc` que si `openapi_url` existe, donc
        fermer ce dernier ferme déjà le premier. Nommer les trois n'est donc pas
        redondant pour rien — c'est ce qui empêche la condition de dépendre d'un
        détail d'implémentation de FastAPI, qu'une version suivante pourrait
        changer sans prévenir. Ce test est le seul filet sous cette hypothèse.
        """
        source = (API_ROOT / "src" / "metreo_api" / "main.py").read_text(encoding="utf-8")
        arbre = ast.parse(source)
        appel = next(
            node
            for node in ast.walk(arbre)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FastAPI"
        )
        conditionnes = {
            mot.arg: ast.unparse(mot.value)
            for mot in appel.keywords
            if mot.arg in {"docs_url", "redoc_url", "openapi_url"}
        }
        assert set(conditionnes) == {"docs_url", "redoc_url", "openapi_url"}, (
            f"un chemin de documentation n'est pas conditionné : {sorted(conditionnes)}"
        )
        for nom, expression in conditionnes.items():
            assert isinstance(ast.parse(expression, mode="eval").body, ast.IfExp), (
                f"{nom} n'est pas conditionnel : {expression}"
            )
            assert "documentation_publiee" in expression, (
                f"{nom} ne dépend pas de la même décision que les autres : {expression}"
            )

    def test_no_environment_variable_can_reopen_it(self) -> None:
        """La décision ne se règle pas : elle se déduit de l'environnement."""
        from metreo_api.config import Settings

        champs = set(Settings.model_fields)
        interrupteurs = {
            nom
            for nom in champs
            if any(mot in nom for mot in ("docs", "openapi", "redoc", "swagger"))
        }
        assert interrupteurs == set(), (
            f"un réglage peut rouvrir la documentation : {sorted(interrupteurs)}"
        )
