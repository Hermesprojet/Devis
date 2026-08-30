"""Health and reference data that the UI needs before authenticating."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from metreo_domain import __version__ as domain_version
from metreo_domain.units import known_units

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import RegionProfile
from ..schemas import HealthOut, RegionProfileOut, UnitOut
from ..security.roles import ROLE_PERMISSIONS, Role

router = APIRouter(tags=["meta"])


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
    return HealthOut(
        status="ok" if not problems else "degraded",
        environment=settings.environment,
        version=domain_version,
        ai_enabled=settings.ai_enabled,
        database=database,
        configuration_problems=problems,
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
