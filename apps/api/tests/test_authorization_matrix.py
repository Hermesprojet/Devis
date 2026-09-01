"""Authorisation matrix — 401 / 403 / 404 on every mounted route.

Three codes, three distinct meanings, asserted separately for every route:

``401`` the caller is not authenticated;
``403`` the caller is authenticated, belongs to the right tenant, but lacks the
        functional permission;
``404`` the resource does not exist **or** belongs to another tenant — the API
        must not reveal which, so a cross-tenant identifier looks exactly like a
        made-up one.

The table below is not maintained by hand against the routers: the coverage
guard walks the routes the application actually mounts and fails when one of
them is missing here. Adding a route without deciding its authorisation story
breaks the suite.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metreo_api.db import get_session_factory
from metreo_api.models import (
    DocumentRevision,
    ExtractionProposal,
    IssuedQuote,
    SourceCitation,
)
from metreo_api.security.roles import ROLE_PERMISSIONS, Permission, Role

from .conftest import login
from .routes_inventory import mounted_routes

# --------------------------------------------------------------------------
# Route classification
# --------------------------------------------------------------------------

#: Reachable without any token.
PUBLIC: frozenset[str] = frozenset(
    {
        "GET /",
        "GET /docs",
        "GET /docs/oauth2-redirect",
        "GET /openapi.json",
        "GET /redoc",
        "GET /api/v1/health",
        # Sonde de vivacité. Publique par nécessité : un orchestrateur qui
        # décide de redémarrer un conteneur n'a pas de jeton, et n'en aura
        # jamais. Elle ne touche rien — ni base, ni configuration — et ne rend
        # que `{"status": "live"}`.
        "GET /api/v1/live",
        # Sonde de disponibilité, publique pour la même raison que /live : un
        # équilibreur qui décide de retirer une instance du service ne
        # s'authentifie pas. Elle n'expose qu'un booléen — la base répond ou
        # non — et aucune donnée d'organisation.
        "GET /api/v1/ready",
        "GET /api/v1/units",
        "GET /api/v1/roles",
        "GET /api/v1/region-profiles",
        "POST /api/v1/auth/dev-login",
        # Le parcours de connexion lui-même : il ne peut pas exiger le jeton
        # qu'il sert à obtenir. Sa protection est ailleurs — `state`, `nonce`,
        # PKCE et la signature du fournisseur — et les tests OIDC l'exercent.
        "GET /api/v1/auth/oidc/start",
        "GET /api/v1/auth/oidc/callback",
        "POST /api/v1/auth/oidc/exchange",
        # Le parcours du destinataire d'un devis. Il n'a pas de compte Metreo
        # et n'en aura pas : son droit d'accès est le secret du lien, échangé
        # une fois contre une session courte en cookie `HttpOnly`. Ces routes
        # refusent tout ce qui ne porte pas cette session — mais elles ne
        # peuvent pas exiger un jeton porteur, qui n'existe pas de son côté.
        "POST /api/v1/public/quote-sessions",
        "GET /api/v1/public/quote",
        "GET /api/v1/public/quote/document.pdf",
        "POST /api/v1/public/quote/response",
    }
)

#: Authenticated, but deliberately not gated on a permission. Confidential
#: fields are masked inside the payload instead — see
#: ``test_commercial_rates_are_masked_without_margin_read``.
AUTH_ONLY: frozenset[str] = frozenset(
    {
        "GET /api/v1/auth/me",
        "GET /api/v1/organization",
        # Le logo de SON entreprise. Aucune permission dédiée : toute personne
        # qui voit l'écran voit l'en-tête que ses devis porteront, et le logo
        # figure de toute façon sur chaque devis qu'elle peut déjà télécharger.
        # Il n'y a pas d'identifiant dans le chemin : la seule organisation
        # atteignable est celle de la session.
        "GET /api/v1/organization/logo",
        "GET /api/v1/organization/settings",
        "GET /api/v1/organization/tax-rates",
    }
)

#: Routes that carry no tenant-owned identifier in their path, so there is no
#: cross-tenant identifier to forge. They are still covered by 401 and 403.
NO_TENANT_IDENTIFIER: frozenset[str] = frozenset(
    {
        "GET /api/v1/audit/events",
        "GET /api/v1/audit/verify",
        "GET /api/v1/clients",
        "GET /api/v1/estimates",
        "GET /api/v1/price-books",
        "GET /api/v1/projects",
        "GET /api/v1/quotes",
        "GET /api/v1/organization/members",
        # Elle ne lit RIEN de l'organisation : elle applique un motif
        # proposé et rend le numéro qu'il produirait. Il n'y a donc aucun
        # identifiant d'un voisin à forger.
        "GET /api/v1/organization/quote-number-preview",
        # Aucun identifiant de tenant dans le chemin : l'organisation visée
        # est toujours celle de la session, jamais une désignée par l'appelant.
        "GET /api/v1/organization/logo",
        "PATCH /api/v1/organization",
        "PUT /api/v1/organization/logo",
        "DELETE /api/v1/organization/logo",
        "PATCH /api/v1/organization/settings",
        "POST /api/v1/organization/members",
        "POST /api/v1/organization/tax-rates",
        "POST /api/v1/clients",
        "POST /api/v1/price-books",
        "POST /api/v1/projects",
        "POST /api/v1/estimates",
    }
)


#: Routes taking a file upload rather than a JSON body.
MULTIPART: frozenset[str] = frozenset(
    {
        "POST /api/v1/price-books/versions/{version_id}/imports/preview",
        # Le dépôt d'un original est lui aussi un multipart. Le corps envoyé
        # ci-dessous n'atteint jamais le disque : la route vérifie le document
        # — donc le tenant — avant d'ouvrir le flux.
        "POST /api/v1/documents/{document_id}/revisions",
    }
)

#: A minimal, valid CSV for the multipart routes above.
_CSV = b"code,label,unit_code,unit_price\nA.1,Article,m3,10.00\n"


def _request(
    client: TestClient, route: Any, path: str, headers: dict[str, str] | None, ids: dict[str, str]
) -> Any:
    """Issue the call with the body shape the route actually expects."""
    if route.key in MULTIPART:
        return client.request(
            route.method, path, headers=headers, files={"file": ("prix.csv", _CSV)}
        )
    return client.request(route.method, path, headers=headers, json=_body_for(route.key, ids))


def _body_for(key: str, ids: dict[str, str]) -> dict[str, Any] | None:
    """A payload valid enough to reach the tenant lookup.

    For the 404 cases the body must pass validation, otherwise the route would
    answer 422 and the test would prove nothing about tenant scoping.
    """
    bodies: dict[str, dict[str, Any]] = {
        "POST /api/v1/projects": {"reference": "X-001", "name": "Projet"},
        "POST /api/v1/projects/{project_id}/documents": {"title": "Cahier des charges"},
        "POST /api/v1/issued-quotes/{quote_id}/share-links": {},
        "POST /api/v1/issued-quotes/{quote_id}/events": {
            "kind": "transmitted",
            "channel": "email",
        },
        "POST /api/v1/issued-quotes/{quote_id}/events/{event_id}/correction": {
            "reason": "Canal erroné",
        },
        "PATCH /api/v1/documents/{document_id}": {"status": "archived"},
        "PATCH /api/v1/projects/{project_id}": {"name": "Renommé"},
        "POST /api/v1/projects/{project_id}/boqs": {"name": "Bordereau"},
        "POST /api/v1/boqs/{boq_id}/items": {
            "position": "1.1",
            "designation": "Poste",
            "unit_code": "m3",
            "quantity": "1",
        },
        "POST /api/v1/boqs/{boq_id}/items:bulk": {
            "items": [
                {
                    "position": "1.1",
                    "designation": "Poste",
                    "unit_code": "m3",
                    "quantity": "1",
                }
            ]
        },
        "PATCH /api/v1/boq-items/{item_id}": {"designation": "Renommé"},
        "POST /api/v1/boq-items/{item_id}/approve": {},
        "POST /api/v1/boq-items/{item_id}/transition": {"status": "verified"},
        "POST /api/v1/organization/tax-rates": {
            "code": "TX-CORPS",
            "label": "Taxe",
            "rate": "0.10",
        },
        "PATCH /api/v1/organization/tax-rates/{tax_rate_id}": {"label": "Renommée"},
        "POST /api/v1/organization/members": {
            "email": "corps@matrice.example",
            "full_name": "Collaborateur",
            "role": "viewer",
        },
        "PATCH /api/v1/organization/members/{membership_id}": {"role": "viewer"},
        "POST /api/v1/price-books": {"name": "Bibliothèque"},
        "POST /api/v1/price-books/{price_book_id}/versions": {"label": "v1"},
        "POST /api/v1/price-books/versions/{version_id}/items": {
            "code": "X.1",
            "label": "Article",
            "unit_code": "m3",
            "unit_price": "10.00",
        },
        "POST /api/v1/price-books/versions/{version_id}/publish": {},
        "POST /api/v1/price-books/versions/{version_id}/composites": {
            "code": "C.1",
            "label": "Composé",
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "lump_sum",
                    "label": "Forfait",
                    "lump_sum_amount": "100.00",
                }
            ],
        },
        "PUT /api/v1/price-books/composites/{composite_id}": {
            "code": "C.1",
            "label": "Composé",
            "unit_code": "m3",
            "revision": 1,
            "components": [
                {
                    "component_type": "lump_sum",
                    "label": "Forfait",
                    "lump_sum_amount": "100.00",
                }
            ],
        },
        "POST /api/v1/price-books/composites/{composite_id}/duplicate": {"code": "C.2"},
        "POST /api/v1/price-books/versions/{version_id}/composites/preview": {
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "lump_sum",
                    "label": "Forfait",
                    "lump_sum_amount": "100.00",
                }
            ],
        },
        # ``confirm`` is refused before any lookup, and identically for an
        # unknown identifier, so it leaks nothing — but without it the route
        # answers 400 and the tenant check below would never be reached.
        "POST /api/v1/price-books/imports/{batch_id}/commit": {"confirm": True},
        "POST /api/v1/estimates": {
            "project_id": ids.get("project", "x"),
            "boq_id": ids.get("boq", "x"),
            "price_book_version_id": ids.get("price_book_version", "x"),
            "name": "Estimation",
        },
        "POST /api/v1/estimates/{estimate_id}/versions": {"label": "v1"},
        "POST /api/v1/estimates/{estimate_id}/versions/{version_id}/freeze": {"confirm": True},
        # Corps vide mais PRÉSENT : tous les champs d'émission sont
        # facultatifs, or un corps absent est refusé en 422 avant la
        # moindre lecture de tenant — le 404 attendu ne prouverait rien.
        "POST /api/v1/estimates/{estimate_id}/versions/{version_id}/issue": {},
        "POST /api/v1/clients": {"name": "Client de matrice"},
        "PATCH /api/v1/clients/{client_id}": {"city": "Namur"},
        "PATCH /api/v1/organization/settings": {"rounding_scale": 2},
        "POST /api/v1/extraction-proposals/{proposal_id}/decisions": {
            "decision": "accepted",
            "reason": "Vérification humaine.",
        },
    }
    return bodies.get(key)


# --------------------------------------------------------------------------
# Fixtures: a full resource graph in each organisation
# --------------------------------------------------------------------------


def _build_graph(client: TestClient, headers: dict[str, str], reference: str) -> dict[str, str]:
    """Create one of every tenant-owned resource and return their identifiers."""
    fiche = client.post(
        "/api/v1/clients",
        headers=headers,
        json={
            "name": f"Client {reference}",
            "billing_address": "Rue de la Matrice 1",
            "postal_code": "5000",
            "city": "Namur",
        },
    )
    assert fiche.status_code == 201, fiche.text
    client_id = fiche.json()["id"]

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"reference": reference, "name": reference, "client_id": client_id},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    document = client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=headers,
        json={"title": "Cahier des charges"},
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]

    identity = client.get("/api/v1/auth/me", headers=headers)
    assert identity.status_code == 200, identity.text
    organization_id = identity.json()["organization_id"]
    user_id = identity.json()["user_id"]
    revision_id = str(uuid4())
    citation_id = str(uuid4())
    proposal_id = str(uuid4())
    session = get_session_factory()()
    try:
        revision = DocumentRevision(
            id=revision_id,
            organization_id=organization_id,
            document_id=document_id,
            revision_number=1,
            sha256="a" * 64,
            byte_size=128,
            media_type="application/pdf",
            storage_key=f"tenant/{organization_id}/matrix.pdf",
            original_filename="matrix.pdf",
            created_by=user_id,
        )
        session.add(revision)
        session.flush()
        citation = SourceCitation(
            id=citation_id,
            organization_id=organization_id,
            revision_id=revision_id,
            page=1,
            char_start=0,
            char_end=10,
            x0=Decimal("0.1"),
            y0=Decimal("0.1"),
            x1=Decimal("0.9"),
            y1=Decimal("0.9"),
            extractor="matrix-fixture",
            confidence=Decimal("0.9"),
        )
        session.add(citation)
        session.flush()
        session.add(
            ExtractionProposal(
                id=proposal_id,
                organization_id=organization_id,
                revision_id=revision_id,
                citation_id=citation_id,
                schema_name="matrix",
                schema_version="1",
                value={"fixture": True},
                confidence=Decimal("0.9"),
                pipeline_version="matrix-1",
                prompt_version="matrix-1",
                model_version="matrix-1",
            )
        )
        session.commit()
    finally:
        session.close()

    boq = client.post(
        f"/api/v1/projects/{project_id}/boqs", headers=headers, json={"name": "Bordereau"}
    )
    assert boq.status_code == 201, boq.text
    boq_id = boq.json()["id"]

    item = client.post(
        f"/api/v1/boqs/{boq_id}/items",
        headers=headers,
        json={
            "position": "1.1",
            "designation": "Excavation",
            "unit_code": "m3",
            "quantity": "10",
        },
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]

    book = client.post("/api/v1/price-books", headers=headers, json={"name": f"Prix {reference}"})
    assert book.status_code == 201, book.text
    book_id = book.json()["id"]

    version = client.post(
        f"/api/v1/price-books/{book_id}/versions", headers=headers, json={"label": "v1"}
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]

    batch = client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("prix.csv", b"code,label,unit_code,unit_price\nA.1,Article,m3,10.00\n")},
    )
    assert batch.status_code in {200, 201}, batch.text
    batch_id = batch.json()["batch_id"]

    estimate = client.post(
        "/api/v1/estimates",
        headers=headers,
        json={
            "project_id": project_id,
            "boq_id": boq_id,
            "price_book_version_id": version_id,
            "name": "Estimation",
        },
    )
    assert estimate.status_code == 201, estimate.text
    estimate_id = estimate.json()["id"]

    estimate_version = client.get(f"/api/v1/estimates/{estimate_id}/versions", headers=headers)
    assert estimate_version.status_code == 200, estimate_version.text
    version_rows = estimate_version.json()
    if not version_rows:
        created = client.post(
            f"/api/v1/estimates/{estimate_id}/versions", headers=headers, json={"label": "v1"}
        )
        assert created.status_code == 201, created.text
        version_rows = [created.json()]

    taux = client.post(
        "/api/v1/organization/tax-rates",
        headers=headers,
        json={"code": f"TX-{reference}"[:30], "label": "Taxe de matrice", "rate": "0.10"},
    )
    assert taux.status_code == 201, taux.text

    membre = client.post(
        "/api/v1/organization/members",
        headers=headers,
        json={
            "email": f"{reference.lower()}@matrice.example",
            "full_name": "Collaborateur de matrice",
            "role": "viewer",
        },
    )
    assert membre.status_code == 201, membre.text

    #: Le devis émis est posé directement en base, comme la révision et la
    #: citation ci-dessus. La matrice éprouve le CLOISONNEMENT de la route de
    #: téléchargement, pas le parcours d'émission — celui-ci a ses propres
    #: tests, et le faire passer ici lierait cette suite au moindre changement
    #: de règle d'émission.
    quote_id = str(uuid4())
    session = get_session_factory()()
    try:
        session.add(
            IssuedQuote(
                id=quote_id,
                organization_id=organization_id,
                project_id=project_id,
                estimate_id=estimate_id,
                estimate_version_id=version_rows[0]["id"],
                client_id=client_id,
                number=f"MATRIX-{reference}",
                sequence_year=2026,
                sequence_number=1,
                issued_at=datetime(2026, 3, 1, 9, 0, 0),
                # Volontairement lointaine : la matrice doit pouvoir créer un
                # lien de consultation quel que soit le jour où elle tourne, et
                # un devis périmé ne se partage pas.
                valid_until=date(2999, 12, 31),
                organization_snapshot={"name": "Matrice"},
                client_snapshot={"name": f"Client {reference}"},
                project_snapshot={"reference": reference},
                document_snapshot={"lines": []},
                pdf_storage_key=f"tenant/{organization_id}/devis/{quote_id}.pdf",
                pdf_sha256="b" * 64,
                pdf_byte_size=1024,
                issued_by=user_id,
            )
        )
        session.commit()
    finally:
        session.close()

    lien = client.post(f"/api/v1/issued-quotes/{quote_id}/share-links", headers=headers, json={})
    assert lien.status_code == 201, lien.text
    transmis = client.post(
        f"/api/v1/issued-quotes/{quote_id}/events",
        headers=headers,
        json={"kind": "transmitted", "channel": "email"},
    )
    assert transmis.status_code == 201, transmis.text
    evenement = next(e for e in transmis.json()["events"] if e["kind"] == "transmitted")

    # Un sous-détail, pour que les routes qui le nomment aient une cible réelle
    # dans CE tenant — sans quoi le contrôle « identifiant d'un autre tenant »
    # ne prouverait rien : il comparerait deux absences.
    compose = client.post(
        f"/api/v1/price-books/versions/{version_id}/composites",
        headers=headers,
        json={
            "code": f"SD-{reference}",
            "label": "Sous-détail de la matrice",
            "unit_code": "m3",
            "components": [
                {
                    "component_type": "lump_sum",
                    "label": "Forfait",
                    "resource_kind": "other",
                    "lump_sum_amount": "100.00",
                }
            ],
        },
    )
    assert compose.status_code == 201, compose.text

    return {
        "composite": compose.json()["id"],
        "share_link": lien.json()["link"]["id"],
        "quote_event": evenement["id"],
        "tax_rate": taux.json()["id"],
        "membership": membre.json()["id"],
        "client": client_id,
        "issued_quote": quote_id,
        "project": project_id,
        "document": document_id,
        "proposal": proposal_id,
        "boq": boq_id,
        "boq_item": item_id,
        "price_book": book_id,
        "price_book_version": version_id,
        "import_batch": batch_id,
        "estimate": estimate_id,
        "estimate_version": version_rows[0]["id"],
    }


@pytest.fixture()
def admin_a(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def admin_b(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@janssens.demo")


@pytest.fixture()
def graph_a(seeded_client: TestClient, admin_a: dict[str, str]) -> dict[str, str]:
    return _build_graph(seeded_client, admin_a, "MATRIX-A")


@pytest.fixture()
def graph_b(seeded_client: TestClient, admin_b: dict[str, str]) -> dict[str, str]:
    return _build_graph(seeded_client, admin_b, "MATRIX-B")


_PARAM_TO_KEY = {
    "project_id": "project",
    "client_id": "client",
    "quote_id": "issued_quote",
    "link_id": "share_link",
    "event_id": "quote_event",
    "document_id": "document",
    "proposal_id": "proposal",
    "boq_id": "boq",
    "item_id": "boq_item",
    "price_book_id": "price_book",
    "composite_id": "composite",
    "batch_id": "import_batch",
    "estimate_id": "estimate",
    "tax_rate_id": "tax_rate",
    "membership_id": "membership",
}


def _fill(path: str, ids: dict[str, str]) -> str:
    """Substitute path parameters from a resource graph.

    ``{version_id}`` is ambiguous — it names a price-book version under
    ``/price-books`` and an estimate version under ``/estimates`` — so it is
    resolved from the path prefix rather than from the parameter name.
    """
    filled = path
    for param, key in _PARAM_TO_KEY.items():
        filled = filled.replace("{" + param + "}", ids[key])
    if "{version_id}" in filled:
        key = "estimate_version" if "/estimates/" in filled else "price_book_version"
        filled = filled.replace("{version_id}", ids[key])
    return filled


# --------------------------------------------------------------------------
# Coverage guard
# --------------------------------------------------------------------------


def _all_routes(client: TestClient) -> list[Any]:
    return mounted_routes(client.app)


def test_every_mounted_route_is_classified(client: TestClient) -> None:
    """No route ships without an explicit authorisation decision."""
    unclassified = [
        route.key
        for route in _all_routes(client)
        if route.key not in PUBLIC and route.key not in AUTH_ONLY and route.permission is None
    ]
    assert unclassified == [], (
        "Ces routes n'exigent ni authentification ni permission et ne sont pas "
        "déclarées publiques. Ajoutez-les à PUBLIC ou AUTH_ONLY, ou posez un "
        f"require(Permission.X) : {unclassified}"
    )


def test_public_and_auth_only_lists_describe_real_routes(client: TestClient) -> None:
    """The lists above cannot rot: every entry must still be mounted."""
    mounted = {route.key for route in _all_routes(client)}
    assert mounted >= PUBLIC, f"PUBLIC cite des routes absentes : {sorted(PUBLIC - mounted)}"
    assert mounted >= AUTH_ONLY, (
        f"AUTH_ONLY cite des routes absentes : {sorted(AUTH_ONLY - mounted)}"
    )
    assert mounted >= NO_TENANT_IDENTIFIER, (
        f"NO_TENANT_IDENTIFIER cite des routes absentes : {sorted(NO_TENANT_IDENTIFIER - mounted)}"
    )


# --------------------------------------------------------------------------
# 401 — unauthenticated
# --------------------------------------------------------------------------


def test_every_protected_route_answers_401_without_a_token(
    seeded_client: TestClient, graph_a: dict[str, str]
) -> None:
    wrong: list[str] = []
    for route in _all_routes(seeded_client):
        if route.key in PUBLIC:
            continue
        response = _request(seeded_client, route, _fill(route.path, graph_a), None, graph_a)
        if response.status_code != 401:
            wrong.append(f"{route.key} -> {response.status_code}")
    assert wrong == [], f"Routes protégées ne répondant pas 401 sans jeton : {wrong}"


def test_a_public_route_stays_reachable_without_a_token(seeded_client: TestClient) -> None:
    """The 401 sweep above would also pass if everything were locked down."""
    assert seeded_client.get("/api/v1/health").status_code == 200
    assert seeded_client.get("/api/v1/units").status_code == 200


# --------------------------------------------------------------------------
# 403 — authenticated, right tenant, missing permission
# --------------------------------------------------------------------------

#: Seeded users of organisation A, by role.
_ROLE_ACCOUNTS: dict[Role, str] = {
    Role.ORG_ADMIN: "admin@dubois.demo",
    Role.ESTIMATOR: "metreur@dubois.demo",
    Role.VIEWER: "lecteur@dubois.demo",
}


def _role_lacking(permission: Permission) -> Role | None:
    for role, email in _ROLE_ACCOUNTS.items():
        assert email  # the account exists in the seed
        if permission not in ROLE_PERMISSIONS[role]:
            return role
    return None


def test_permission_routes_answer_403_for_a_role_that_lacks_the_permission(
    seeded_client: TestClient, graph_a: dict[str, str]
) -> None:
    """Same tenant, real resource, missing permission: 403, never 404 or 200."""
    wrong: list[str] = []
    exercised = 0
    for route in _all_routes(seeded_client):
        if route.permission is None:
            continue
        role = _role_lacking(route.permission)
        if role is None:
            continue
        headers = login(seeded_client, _ROLE_ACCOUNTS[role])
        response = _request(seeded_client, route, _fill(route.path, graph_a), headers, graph_a)
        exercised += 1
        if response.status_code != 403:
            wrong.append(f"{route.key} ({role.value}) -> {response.status_code}")
    assert wrong == [], f"Routes ne répondant pas 403 sans la permission : {wrong}"
    assert exercised >= 20, f"Trop peu de routes couvertes par le test 403 : {exercised}"


def test_permissions_held_by_every_role_are_declared(seeded_client: TestClient) -> None:
    """Document the permissions no seeded role lacks, instead of hiding them.

    ``project:read``, ``boq:read`` and ``estimate:read`` are held by all six
    roles, so no 403 case can be built for the routes that require only those.
    If a future role drops one of them, this test turns red and the 403 sweep
    above starts covering the corresponding routes.
    """
    universal = {
        permission
        for permission in Permission
        if all(permission in perms for perms in ROLE_PERMISSIONS.values())
    }
    assert universal == {
        Permission.PROJECT_READ,
        Permission.DOCUMENT_READ,
        Permission.BOQ_READ,
        Permission.ESTIMATE_READ,
    }, f"Permissions détenues par tous les rôles : {sorted(p.value for p in universal)}"


def test_commercial_rates_are_masked_without_margin_read(seeded_client: TestClient) -> None:
    """The auth-only settings route protects its confidential fields itself."""
    estimator = login(seeded_client, "metreur@dubois.demo")
    admin = login(seeded_client, "admin@dubois.demo")
    assert Permission.MARGIN_READ not in ROLE_PERMISSIONS[Role.ESTIMATOR]

    masked = seeded_client.get("/api/v1/organization/settings", headers=estimator).json()
    full = seeded_client.get("/api/v1/organization/settings", headers=admin).json()

    assert masked["commercial_rates_visible"] is False
    assert full["commercial_rates_visible"] is True
    for field in ("margin_rate", "site_overheads_rate", "general_overheads_rate"):
        # None, never 0: a mask must not be mistaken for a real rate.
        assert masked[field] is None, field
        assert full[field] is not None, field


# --------------------------------------------------------------------------
# 404 — cross-tenant identifiers
# --------------------------------------------------------------------------


def test_cross_tenant_identifiers_answer_404_on_every_route(
    seeded_client: TestClient, admin_b: dict[str, str], graph_a: dict[str, str]
) -> None:
    """Organisation B, full permissions, pointing at organisation A's rows."""
    wrong: list[str] = []
    exercised = 0
    for route in _all_routes(seeded_client):
        if route.key in PUBLIC or route.key in AUTH_ONLY:
            continue
        if route.key in NO_TENANT_IDENTIFIER:
            continue
        response = _request(seeded_client, route, _fill(route.path, graph_a), admin_b, graph_a)
        exercised += 1
        if response.status_code != 404:
            wrong.append(f"{route.key} -> {response.status_code}")
    assert wrong == [], f"Identifiants d'un autre tenant ne donnant pas 404 : {wrong}"
    assert exercised >= 15, f"Trop peu de routes couvertes par le test 404 : {exercised}"


def test_the_same_routes_succeed_for_their_owner(
    seeded_client: TestClient, admin_a: dict[str, str], graph_a: dict[str, str]
) -> None:
    """The 404 sweep would also pass against identifiers that simply do not work."""
    reads = [
        f"/api/v1/projects/{graph_a['project']}",
        f"/api/v1/projects/{graph_a['project']}/documents",
        f"/api/v1/documents/{graph_a['document']}",
        f"/api/v1/documents/{graph_a['document']}/revisions",
        f"/api/v1/projects/{graph_a['project']}/boqs",
        f"/api/v1/boqs/{graph_a['boq']}/items",
        f"/api/v1/price-books/{graph_a['price_book']}/versions",
        f"/api/v1/price-books/versions/{graph_a['price_book_version']}/items",
        f"/api/v1/price-books/imports/{graph_a['import_batch']}",
        f"/api/v1/estimates/{graph_a['estimate']}",
        f"/api/v1/estimates/{graph_a['estimate']}/versions",
    ]
    for path in reads:
        assert seeded_client.get(path, headers=admin_a).status_code == 200, path


# --------------------------------------------------------------------------
# Nested identifiers: a child of B reached through a parent of A
# --------------------------------------------------------------------------


def test_a_child_of_another_tenant_is_not_reachable_through_an_own_parent(
    seeded_client: TestClient,
    admin_a: dict[str, str],
    graph_a: dict[str, str],
    graph_b: dict[str, str],
) -> None:
    """Mixing a legitimate parent with somebody else's child must not widen access."""
    # A's token, A's boq, but B's price item on the line.
    price_b = seeded_client.get(
        f"/api/v1/price-books/versions/{graph_b['price_book_version']}/items",
        headers=login(seeded_client, "admin@janssens.demo"),
    )
    assert price_b.status_code == 200, price_b.text
    items = price_b.json()["items"]
    if items:
        response = seeded_client.post(
            f"/api/v1/boqs/{graph_a['boq']}/items",
            headers=admin_a,
            json={
                "position": "9.9",
                "designation": "Ligne avec un prix d'un autre tenant",
                "unit_code": "m3",
                "quantity": "1",
                "price_item_id": items[0]["id"],
            },
        )
        assert response.status_code == 404, response.text

    # A's token, A's estimate, but B's boq and price book version in the body.
    response = seeded_client.post(
        "/api/v1/estimates",
        headers=admin_a,
        json={
            "project_id": graph_a["project"],
            "boq_id": graph_b["boq"],
            "price_book_version_id": graph_a["price_book_version"],
            "name": "Estimation détournée",
        },
    )
    assert response.status_code == 404, response.text

    response = seeded_client.post(
        "/api/v1/estimates",
        headers=admin_a,
        json={
            "project_id": graph_b["project"],
            "boq_id": graph_a["boq"],
            "price_book_version_id": graph_a["price_book_version"],
            "name": "Estimation détournée",
        },
    )
    assert response.status_code == 404, response.text


def test_an_estimate_version_of_another_tenant_is_not_reachable_through_an_own_estimate(
    seeded_client: TestClient,
    admin_a: dict[str, str],
    graph_a: dict[str, str],
    graph_b: dict[str, str],
) -> None:
    """The child identifier is checked against the tenant, not only against the parent."""
    for suffix in ("computation", "export.csv", "quote.html"):
        response = seeded_client.get(
            f"/api/v1/estimates/{graph_a['estimate']}"
            f"/versions/{graph_b['estimate_version']}/{suffix}",
            headers=admin_a,
        )
        assert response.status_code == 404, f"{suffix} -> {response.status_code}"

    response = seeded_client.post(
        f"/api/v1/estimates/{graph_a['estimate']}/versions/{graph_b['estimate_version']}/freeze",
        headers=admin_a,
        json={"confirm": True},
    )
    assert response.status_code == 404, response.text
