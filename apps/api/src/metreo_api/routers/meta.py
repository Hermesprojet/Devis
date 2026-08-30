"""Health and reference data that the UI needs before authenticating."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from metreo_domain import __version__ as domain_version
from metreo_domain.units import known_units

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import RegionProfile
from ..schemas import HealthOut, RegionProfileOut, UnitOut
from ..security.roles import ROLE_PERMISSIONS, Role
from ..transactions import RouteTransactionnelle

router = APIRouter(tags=["meta"], route_class=RouteTransactionnelle)


@router.get("/health", response_model=HealthOut, summary="État du service")
def health(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(session_scope),
) -> HealthOut:
    problems = settings.validate_startup()
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover - only on a broken deployment
        database = "unreachable"
        problems.append("database unreachable")
    # Ce que l'écran de connexion doit savoir avant d'afficher quoi que ce
    # soit : proposer un formulaire qui n'aboutira pas est pire que ne rien
    # proposer. La liste vide est une réponse valide — un déploiement d'API
    # pure accepte des jetons sans en émettre.
    methodes: list[Literal["dev", "oidc"]] = []
    if settings.auth_mode == "oidc" and settings.oidc_configured:
        methodes.append("oidc")
    elif settings.auth_mode == "dev" and not settings.is_production:
        methodes.append("dev")
    return HealthOut(
        status="ok" if not problems else "degraded",
        environment=settings.environment,
        version=domain_version,
        ai_enabled=settings.ai_enabled,
        database=database,
        configuration_problems=problems,
        login_methods=methodes,
    )


@router.get("/live", summary="Le processus répond-il ?")
def live() -> dict[str, str]:
    """Vivacité : le processus répond, et c'est tout ce que ça dit.

    Distinct de `/health`, qui interroge la base et sert donc de contrôle de
    *disponibilité*. Confondre les deux a une conséquence précise : un
    orchestrateur qui redémarre un conteneur parce que la base est
    momentanément injoignable ajoute un redémarrage à une panne qu'il
    n'atteint pas, et fait tomber les instances les unes après les autres.

    C'est ce point-ci que le `HEALTHCHECK` de l'image interroge.
    """
    return {"status": "live"}


@router.get("/ready", summary="Le service peut-il prendre du trafic ?")
def ready(
    response: Response,
    session: Session = Depends(session_scope),
) -> dict[str, str]:
    """Disponibilité : **503 quand la base ne répond pas.**

    Ni `/live` ni `/health` ne peuvent tenir ce rôle, et c'est la raison
    d'être de ce troisième point :

    - `/live` ne touche rien : il reste vert pendant une panne de base, ce
      qui est exactement ce qu'on lui demande — il empêche un orchestrateur
      de redémarrer des conteneurs sains.
    - `/health` répond **200** même en `degraded`. C'est le bon choix pour une
      page d'état lue par un humain, et le mauvais pour une sonde : un
      équilibreur qui ne lit que le code HTTP continuerait d'envoyer du trafic
      à une instance incapable de servir une seule requête utile.

    Un code HTTP rouge est la seule forme qu'une sonde comprenne sans qu'on
    lui apprenne à lire notre JSON.

    Les problèmes de configuration n'entrent PAS dans ce contrôle : ils sont
    tranchés au démarrage, où ils font échouer le processus en préproduction
    et en production. Une instance qui tourne les a déjà passés.
    """
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - seulement sur un déploiement cassé
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unready", "database": "unreachable"}
    return {"status": "ready", "database": "ok"}


@router.get("/units", response_model=list[UnitOut], summary="Unités reconnues")
def list_units() -> list[UnitOut]:
    return [
        UnitOut(
            code=unit.code,
            dimension=unit.dimension.value,
            dimension_label=unit.dimension.label_fr,
            label=unit.label_fr,
            factor_to_base=str(unit.factor_to_base),
            aliases=list(unit.aliases),
        )
        for unit in known_units()
    ]


@router.get("/roles", summary="Rôles et permissions")
def list_roles() -> list[dict[str, object]]:
    return [
        {
            "role": role.value,
            "label": role.label_fr,
            "permissions": sorted(p.value for p in permissions),
        }
        for role, permissions in ROLE_PERMISSIONS.items()
        if isinstance(role, Role)
    ]


@router.get(
    "/region-profiles",
    response_model=list[RegionProfileOut],
    summary="Packs pays/région disponibles",
)
def list_region_profiles(
    country_code: str | None = None,
    session: Session = Depends(session_scope),
) -> list[RegionProfile]:
    query = select(RegionProfile).order_by(RegionProfile.country_code, RegionProfile.code)
    if country_code:
        query = query.where(RegionProfile.country_code == country_code.upper())
    return list(session.scalars(query).all())
