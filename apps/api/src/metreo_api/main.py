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
from .routers import (
    audit_log,
    auth,
    boq,
    documents,
    estimates,
    meta,
    organizations,
    pricebooks,
    projects,
)

logger = logging.getLogger("metreo.api")

DESCRIPTION = """
API de l'application d'étude de prix et de devis BTP.

**Outil d'aide à la décision.** Les quantités, prix et hypothèses restent sous
la responsabilité de l'utilisateur. Aucun montant n'est produit sans que sa
décomposition soit consultable.

Phase 1: organisations, projets, bibliothèque de prix (import CSV en deux temps),
bordereau, moteur de calcul déterministe, gel de version, exports et journal
d'audit. Les modules documentaires, plans et achats sont décrits dans
`docs/ROADMAP.md`. La Phase 2A expose uniquement les métadonnées documentaires
et la validation humaine ; aucun upload, OCR ou fournisseur IA n'est actif.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    problems = settings.validate_startup()
    if problems and settings.is_production:
        raise RuntimeError("configuration refusée: " + "; ".join(problems))
    for problem in problems:
        logger.warning("configuration_problem", extra={"problem": problem})

    # Le schéma OpenAPI décrit toute la surface d'API : chemins, paramètres,
    # formes d'erreur, modèles. Mesuré sur cette application démarrée en
    # `production` : `/openapi.json` rendait **82 745 octets sans
    # authentification**, `/docs` et `/redoc` répondaient 200. Ce n'est pas un
    # secret — les routes sont de toute façon protégées — mais c'est une carte
    # complète offerte à qui la demande, et elle n'a aucune raison d'être
    # publiée là où personne ne développe.
    #
    # **Trois points d'entrée, pas deux.** `/redoc` est monté par défaut par
    # FastAPI même sans être nommé ici : il était ouvert lui aussi, et la note
    # de suivi qui n'en citait que deux sous-estimait l'exposition.
    #
    # Le seuil est le même que pour le mode d'authentification et pour le
    # moteur de base de données : `is_production` couvre `staging` **et**
    # `production`. Une pré-production est jointe depuis l'extérieur comme une
    # production ; la traiter autrement ici contredirait le reste du fichier.
    # Aucun réglage ne permet de rouvrir : un interrupteur se met du mauvais
    # côté, et c'est précisément la classe d'erreur qu'on retire.
    #
    # `app.openapi()` continue de produire le schéma en mémoire — vérifié : le
    # contrôle d'installation propre et la matrice d'autorisation s'en servent
    # et ne passent pas par HTTP.
    documentation_publiee = not settings.is_production
    app = FastAPI(
        title=settings.app_name,
        version=domain_version,
        description=DESCRIPTION,
        docs_url="/docs" if documentation_publiee else None,
        redoc_url="/redoc" if documentation_publiee else None,
        openapi_url="/openapi.json" if documentation_publiee else None,
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
        # La colonne d'audit stocke 64 caractères. Un en-tête plus long serait
        # refusé à l'écriture par PostgreSQL, et accepté tel quel par SQLite,
        # qui n'applique pas la longueur déclarée d'un VARCHAR. L'identifiant
        # journalisé ne correspondrait plus à celui renvoyé au client. Un
        # en-tête hors format est donc remplacé, jamais coupé.
        supplied = request.headers.get("X-Request-Id")
        request_id = supplied if supplied and len(supplied) <= 64 else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        # Le `reset` doit venir APRÈS la journalisation, pas avant : placé dans
        # un `finally` en amont du `logger.info`, il vidait la variable de
        # contexte et chaque ligne de journal portait `request_id: "-"`. La
        # corrélation annoncée n'existait donc pour aucune requête.
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers["X-Request-Id"] = request_id
            # Sans en-tête de cache, Chromium écrit le corps de la réponse en
            # clair dans son cache disque. Mesuré : un devis téléchargé se
            # retrouve dans `Default/Cache/Cache_Data/`, et y reste après la
            # déconnexion — sur un poste partagé, le suivant le relit.
            #
            # Posé ici et pas endpoint par endpoint : une liste d'endpoints à
            # protéger se périme au premier endpoint ajouté. Un endpoint qui
            # aurait de bonnes raisons d'être caché pose son propre en-tête,
            # et celui-ci le respecte.
            response.headers.setdefault("Cache-Control", "no-store")
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
        except Exception:
            # Une requête qui explose doit laisser une trace corrélée : sans
            # cela, le seul cas où l'on a vraiment besoin du journal est
            # précisément celui qui n'y figure pas.
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "http_request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            request_id_var.reset(token)

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
    app.include_router(documents.router, prefix=prefix)
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
