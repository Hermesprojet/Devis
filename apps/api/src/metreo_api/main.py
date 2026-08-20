"""FastAPI application factory.

A modular monolith: one process, clear module boundaries, and long operations
designed to be moved to a worker without changing their contract.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from metreo_domain import __version__ as domain_version
from metreo_domain.errors import DomainError

from .config import Settings, get_settings
from .logging_config import configure_logging, request_id_var
from .routers import audit_log, auth, boq, estimates, meta, organizations, pricebooks, projects

logger = logging.getLogger("metreo.api")

DESCRIPTION = """
API de l'application d'étude de prix et de devis BTP.

**Outil d'aide à la décision.** Les quantités, prix et hypothèses restent sous
la responsabilité de l'utilisateur. Aucun montant n'est produit sans que sa
décomposition soit consultable.

Phase 1: organisations, projets, bibliothèque de prix (import CSV en deux temps),
bordereau, moteur de calcul déterministe, gel de version, exports et journal
d'audit. Les modules documentaires, plans et achats sont décrits dans
`docs/ROADMAP.md` et ne sont **pas** implémentés.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    problems = settings.validate_startup()
    if problems and settings.is_production:
        raise RuntimeError("configuration refusée: " + "; ".join(problems))
    for problem in problems:
        logger.warning("configuration_problem", extra={"problem": problem})

    app = FastAPI(
        title=settings.app_name,
        version=domain_version,
        description=DESCRIPTION,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Content-Disposition"],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Callable[[Request], Awaitable]):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        """Domain refusals are client errors, not server faults.

        An ambiguous conversion or a zero productivity rate is the engine doing
        its job; the client gets the code and the context it needs to ask the
        user for the missing input.
        """
        return JSONResponse(status_code=422, content={"detail": exc.to_dict()})

    prefix = settings.api_prefix
    app.include_router(meta.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(organizations.router, prefix=prefix)
    app.include_router(projects.router, prefix=prefix)
    app.include_router(pricebooks.router, prefix=prefix)
    app.include_router(boq.router, prefix=prefix)
    app.include_router(estimates.router, prefix=prefix)
    app.include_router(audit_log.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": domain_version,
            "docs": "/docs",
            "api": prefix,
        }

    return app


app = create_app()
