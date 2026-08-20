"""Walk every route the application actually mounts.

FastAPI 0.141 keeps included routers behind ``_IncludedRouter`` objects rather
than flattening them into ``app.routes``, so a naive iteration sees only the
handful of routes declared on the application itself. Everything that inspects
the route table — the authorisation matrix, the coverage guard — goes through
:func:`mounted_routes` instead of walking ``app.routes`` by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metreo_api.security.roles import Permission

_HTTP_METHODS = frozenset({"GET", "POST", "PATCH", "PUT", "DELETE"})


@dataclass(frozen=True)
class MountedRoute:
    method: str
    path: str
    #: ``None`` when the route enforces no permission (public or auth-only).
    permission: Permission | None
    name: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def _required_permission(route: Any) -> Permission | None:
    """Read the permission a route enforces, via ``require``'s contract.

    ``require()`` attaches ``required_permission`` to the dependency it builds;
    see :func:`metreo_api.security.auth.require`.
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None
    stack = list(getattr(dependant, "dependencies", ()))
    while stack:
        sub = stack.pop()
        call = getattr(sub, "call", None)
        permission = getattr(call, "required_permission", None)
        if permission is not None:
            return permission
        stack.extend(getattr(sub, "dependencies", ()))
    return None


def mounted_routes(app: Any) -> list[MountedRoute]:
    """Every HTTP route of ``app``, including those behind included routers."""
    found: list[MountedRoute] = []

    def walk(routes: Any, prefix: str) -> None:
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                walk(original.routes, prefix + getattr(context, "prefix", ""))
                continue
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", None)
            if not methods or path is None:
                continue
            for method in sorted(methods & _HTTP_METHODS):
                found.append(
                    MountedRoute(
                        method=method,
                        path=prefix + path,
                        permission=_required_permission(route),
                        name=getattr(route, "name", ""),
                    )
                )

    walk(app.routes, "")
    return sorted(found, key=lambda route: route.key)
