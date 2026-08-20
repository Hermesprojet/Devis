"""Roles and permissions.

Authorisation is checked on the server for every action. The UI hides what a
user cannot do, but hiding is not a control — the dependency in
:mod:`metreo_api.security.auth` is.

Cost and margin permissions are deliberately separate from quantity
permissions: a site manager legitimately reads quantities without seeing the
loaded hourly rate of a crew.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ORG_ADMIN = "org_admin"
    ESTIMATING_MANAGER = "estimating_manager"
    ESTIMATOR = "estimator"
    PROJECT_MANAGER = "project_manager"
    BUYER = "buyer"
    VIEWER = "viewer"

    @property
    def label_fr(self) -> str:
        return _ROLE_LABELS_FR[self]


_ROLE_LABELS_FR: dict[Role, str] = {
    Role.ORG_ADMIN: "Administrateur de l'entreprise",
    Role.ESTIMATING_MANAGER: "Responsable étude de prix",
    Role.ESTIMATOR: "Métreur / deviseur",
    Role.PROJECT_MANAGER: "Chef de projet / conducteur",
    Role.BUYER: "Acheteur",
    Role.VIEWER: "Lecteur / auditeur",
}


class Permission(str, Enum):
    ORG_MANAGE = "org:manage"
    USER_MANAGE = "user:manage"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PRICEBOOK_READ = "pricebook:read"
    PRICEBOOK_WRITE = "pricebook:write"
    BOQ_READ = "boq:read"
    BOQ_WRITE = "boq:write"
    BOQ_APPROVE = "boq:approve"
    ESTIMATE_READ = "estimate:read"
    ESTIMATE_WRITE = "estimate:write"
    ESTIMATE_FREEZE = "estimate:freeze"
    #: See internal cost decomposition (déboursé sec, resource rates).
    COST_READ = "cost:read"
    #: See margin, contingency and general overhead rates.
    MARGIN_READ = "margin:read"
    EXPORT_CLIENT = "export:client"
    EXPORT_INTERNAL = "export:internal"
    AUDIT_READ = "audit:read"


_ALL = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ORG_ADMIN: set(_ALL),
    Role.ESTIMATING_MANAGER: _ALL - {Permission.USER_MANAGE},
    Role.ESTIMATOR: {
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
        Permission.PRICEBOOK_READ,
        Permission.PRICEBOOK_WRITE,
        Permission.BOQ_READ,
        Permission.BOQ_WRITE,
        Permission.ESTIMATE_READ,
        Permission.ESTIMATE_WRITE,
        Permission.COST_READ,
        Permission.EXPORT_CLIENT,
        Permission.EXPORT_INTERNAL,
    },
    Role.PROJECT_MANAGER: {
        Permission.PROJECT_READ,
        Permission.PRICEBOOK_READ,
        Permission.BOQ_READ,
        Permission.ESTIMATE_READ,
        Permission.COST_READ,
        Permission.EXPORT_CLIENT,
    },
    Role.BUYER: {
        Permission.PROJECT_READ,
        Permission.PRICEBOOK_READ,
        Permission.PRICEBOOK_WRITE,
        Permission.BOQ_READ,
        Permission.ESTIMATE_READ,
        Permission.COST_READ,
    },
    Role.VIEWER: {
        Permission.PROJECT_READ,
        Permission.BOQ_READ,
        Permission.ESTIMATE_READ,
        Permission.AUDIT_READ,
    },
}


def permissions_for(role: str) -> set[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return set()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
