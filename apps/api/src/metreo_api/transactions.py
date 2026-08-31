"""Où une écriture devient vraie — et à quel instant le client l'apprend.

Le problème, mesuré et corrigé une première fois par la PR #52 sur le seul
parcours de connexion : **FastAPI exécute le code de sortie des dépendances
`yield` après avoir envoyé la réponse.** Le `commit` de `session_scope` a donc
lieu quand le client a déjà lu son 2xx. Entre les deux, l'écriture n'est
visible d'aucune autre connexion — et la requête suivante, qui ouvre sa propre
session, peut ne rien trouver.

Ce n'était pas une bizarrerie de l'authentification : c'est le modèle de toutes
les routes. Une session de navigateur qui crée une taxe puis l'utilise, ou qui
téléverse un document puis le télécharge, court la même course.

La règle, désormais unique et centrale :

    écriture métier + invariants + audit + commit réussi → ALORS 2xx
    échec du commit → rollback complet, compensations, réponse d'erreur

Elle est appliquée par `RouteTransactionnelle`, qui valide entre le moment où
la fonction de route a produit sa réponse et le moment où cette réponse part.
Ce n'est possible que là : les dépendances `yield` se dénouent trop tard, et un
intergiciel n'a pas accès à la session.

Ce que ce module n'est pas : une collection de `commit()` recopiés dans chaque
route. Il y en a exactement un, ici, et le registre ci-dessous dit à quelles
routes il s'applique.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Iterator
from enum import Enum
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("metreo.api")


class Famille(str, Enum):
    """Ce qu'une route fait vraiment, du point de vue transactionnel.

    Le verbe HTTP ne suffit pas : trois `GET` écrivent un événement d'audit —
    les deux exports et le téléchargement d'un original — et deux `GET` du
    parcours de connexion écrivent une transaction. Classer par verbe aurait
    laissé ces cinq routes dehors.
    """

    #: Aucune écriture. La session ne sert qu'à lire.
    LECTURE = "lecture"
    #: Écriture en base, sans événement d'audit.
    ECRITURE = "ecriture"
    #: Écriture en base ET événement d'audit chaîné, dans la même transaction.
    ECRITURE_AUDITEE = "ecriture_auditee"
    #: Écriture en base ET octets posés sur le volume — deux ressources qui ne
    #: partagent aucune transaction. Voir `compenser`.
    ECRITURE_ET_FICHIER = "ecriture_et_fichier"

    @property
    def ecrit(self) -> bool:
        return self is not Famille.LECTURE


#: Le registre des routes, par (méthode, chemin du gabarit).
#:
#: Il est EXÉCUTABLE : `test_registre_transactionnel.py` compare cette table à
#: ce que l'application expose réellement, et refuse aussi bien une route
#: absente qu'une entrée qui ne correspond à plus rien. Ajouter une route
#: d'écriture sans la classer fait tomber la suite.
REGISTRE: dict[tuple[str, str], Famille] = {}


def _classer(famille: Famille, *routes: str) -> None:
    for route in routes:
        methode, chemin = route.split(" ", 1)
        REGISTRE[(methode, chemin)] = famille


# -- Écritures en base, sans audit -------------------------------------------
_classer(
    Famille.ECRITURE,
    # Le parcours de connexion. Ces trois-là portaient déjà leur `commit`
    # explicite depuis #52 ; ils le tiennent maintenant du modèle central, et
    # la course qu'ils avaient est éprouvée route par route.
    "GET /api/v1/auth/oidc/start",
    "GET /api/v1/auth/oidc/callback",
    "POST /api/v1/auth/oidc/exchange",
)

# -- Verbes d'écriture qui n'écrivent rien ----------------------------------
#
# Classées explicitement, et non laissées au défaut : une route POST qui ne
# figure nulle part est un oubli, et le démarrage la refuse. Dire « celle-ci ne
# écrit pas » est une décision, pas un silence.
_classer(
    Famille.LECTURE,
    # Cherche l'utilisateur et signe un jeton. Aucune écriture — le mode n'existe
    # d'ailleurs qu'en développement.
    "POST /api/v1/auth/dev-login",
)

# -- Écritures accompagnées d'un événement d'audit ---------------------------
_classer(
    Famille.ECRITURE_AUDITEE,
    "POST /api/v1/clients",
    "PATCH /api/v1/clients/{client_id}",
    "DELETE /api/v1/clients/{client_id}",
    "POST /api/v1/estimates/{estimate_id}/versions/{version_id}/issue",
    "POST /api/v1/projects",
    "PATCH /api/v1/projects/{project_id}",
    "DELETE /api/v1/projects/{project_id}",
    "POST /api/v1/projects/{project_id}/boqs",
    "POST /api/v1/boqs/{boq_id}/items",
    "POST /api/v1/boqs/{boq_id}/items:bulk",
    "PATCH /api/v1/boq-items/{item_id}",
    "DELETE /api/v1/boq-items/{item_id}",
    "POST /api/v1/boq-items/{item_id}/approve",
    "POST /api/v1/boq-items/{item_id}/transition",
    "POST /api/v1/estimates",
    "POST /api/v1/estimates/{estimate_id}/versions",
    "POST /api/v1/estimates/{estimate_id}/versions/{version_id}/freeze",
    "POST /api/v1/organization/members",
    "PATCH /api/v1/organization/members/{membership_id}",
    "PATCH /api/v1/organization/settings",
    "POST /api/v1/organization/tax-rates",
    "PATCH /api/v1/organization/tax-rates/{tax_rate_id}",
    "DELETE /api/v1/organization/tax-rates/{tax_rate_id}",
    "POST /api/v1/price-books",
    "POST /api/v1/price-books/{price_book_id}/versions",
    "POST /api/v1/price-books/versions/{version_id}/publish",
    "POST /api/v1/price-books/versions/{version_id}/items",
    "POST /api/v1/price-books/versions/{version_id}/composites",
    "POST /api/v1/price-books/versions/{version_id}/imports/preview",
    "POST /api/v1/price-books/imports/{batch_id}/commit",
    "POST /api/v1/projects/{project_id}/documents",
    "PATCH /api/v1/documents/{document_id}",
    "POST /api/v1/extraction-proposals/{proposal_id}/decisions",
    # Trois lectures qui écrivent : elles n'écrivent RIEN d'autre que la trace
    # de leur propre exécution, et cette trace vaut d'être vraie.
    "GET /api/v1/estimates/{estimate_id}/versions/{version_id}/export.csv",
    "GET /api/v1/estimates/{estimate_id}/versions/{version_id}/quote.html",
    "GET /api/v1/documents/{document_id}/revisions/{revision_id}/content",
    # Reprendre un devis remis se trace : c'est un document commercial, et
    # savoir qui en a repris une copie a la même valeur que savoir qui l'a émis.
    "GET /api/v1/issued-quotes/{quote_id}/document.pdf",
)

# -- Écritures qui posent aussi des octets sur le volume ---------------------
_classer(
    Famille.ECRITURE_ET_FICHIER,
    "POST /api/v1/documents/{document_id}/revisions",
)


def famille_de(methode: str, chemin: str) -> Famille:
    """La famille d'une route, `LECTURE` par défaut.

    Le défaut est le plus prudent des deux : une route de lecture classée par
    erreur en écriture validerait une transaction vide, sans conséquence ; une
    route d'écriture oubliée ne validerait pas à temps. C'est pourquoi le
    registre est contrôlé par un test, et non par ce défaut.
    """
    return REGISTRE.get((methode.upper(), chemin), Famille.LECTURE)


# ---------------------------------------------------------------------------
# Compensations : ce que la base ne sait pas défaire
# ---------------------------------------------------------------------------


def compenser(session: Session, action: Callable[[], None], quoi: str) -> None:
    """Enregistre le geste qui annule un effet EXTÉRIEUR à la base.

    PostgreSQL et le système de fichiers ne partagent aucune transaction. Un
    original écrit sur le volume avant un `commit` qui échoue resterait là,
    orphelin, sans révision pour le nommer — et la sauvegarde l'emporterait.

    L'action doit être IDEMPOTENTE : elle peut ne jamais être appelée, l'être
    une fois, ou l'être après que le fichier a déjà disparu.
    """
    session.info.setdefault("compensations", []).append((quoi, action))


def compenser_apres_annulation(session: Session) -> None:
    """Défait ce que la base ne pouvait pas défaire. Toujours APRÈS le rollback."""
    for quoi, action in reversed(session.info.pop("compensations", [])):
        try:
            action()
        except Exception:  # une compensation qui échoue ne doit pas masquer la cause
            logger.exception("compensation_impossible", extra={"compensation": quoi})
        else:
            logger.warning("compensation_appliquee", extra={"compensation": quoi})


def _oublier_compensations(session: Session) -> None:
    session.info.pop("compensations", None)


# ---------------------------------------------------------------------------
# La route qui valide avant de répondre
# ---------------------------------------------------------------------------


def parcourir(routes: list[Any], prefixe: str = "") -> Iterator[tuple[str, str, APIRoute]]:
    """Rend (méthode, chemin public, route) pour tout l'arbre de routage.

    Cette version de FastAPI n'aplatit plus les routeurs inclus : elle les monte
    par référence, et le chemin porté par une route est donc celui de son
    routeur, sans le préfixe de montage. Reconstituer le chemin PUBLIC est la
    seule façon de le comparer au registre — et à ce que voit le client.
    """
    for route in routes:
        contexte = getattr(route, "include_context", None)
        if contexte is not None:
            yield from parcourir(contexte.included_router.routes, prefixe + (contexte.prefix or ""))
            continue
        chemin = prefixe + getattr(route, "path", "")
        for methode in sorted(getattr(route, "methods", None) or []):
            yield methode, chemin, route


#: Verbes dont une route ne peut PAS être une lecture par défaut.
VERBES_ECRIVANTS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def classer_les_routes(app: Any) -> list[tuple[str, str, Famille]]:
    """Attache sa famille à chaque route, et refuse de démarrer sans registre.

    Le contrôle est fait au DÉMARRAGE, pas seulement dans les tests : une route
    d'écriture qu'on ajouterait sans la classer ne validerait pas à temps, et le
    défaut ne se verrait qu'en charge, une fois sur dix. Mieux vaut que
    l'application refuse de se lever en nommant la route.

    Un `GET` absent du registre est une lecture — c'est le seul défaut possible,
    et il est sans danger : valider une transaction vide ne coûte rien, ne pas
    valider une écriture coûte une panne. Les `GET` qui écrivent sont donc au
    registre, nommément.
    """
    classees: list[tuple[str, str, Famille]] = []
    oubliees: list[str] = []
    for methode, chemin, route in parcourir(app.routes):
        famille = REGISTRE.get((methode, chemin))
        if famille is None:
            if methode in VERBES_ECRIVANTS and getattr(route, "include_in_schema", True):
                oubliees.append(f"{methode} {chemin}")
            famille = Famille.LECTURE
        if isinstance(route, RouteTransactionnelle):
            route.famille_transactionnelle = famille
        classees.append((methode, chemin, famille))
    if oubliees:
        raise RuntimeError(
            "Routes d'écriture absentes du registre transactionnel de "
            "`metreo_api/transactions.py` — leur écriture ne serait pas validée "
            "avant la réponse :\n  " + "\n  ".join(sorted(oubliees))
        )
    return classees


class RouteTransactionnelle(APIRoute):
    #: Renseignée par `classer_les_routes`, une fois les routeurs montés : le
    #: chemin public d'une route n'existe pas avant.
    famille_transactionnelle: Famille = Famille.LECTURE

    """Valide la transaction entre la réponse produite et la réponse envoyée.

    C'est le seul endroit du code où une écriture d'API est validée. Le
    gestionnaire enveloppé est appelé À L'INTÉRIEUR de
    `wrap_app_handling_exceptions` : une erreur levée ici traverse donc les
    mêmes gestionnaires d'exception que le reste, et le client reçoit une
    réponse d'erreur en bonne et due forme — jamais le 2xx que la fonction de
    route venait pourtant de composer.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def gestionnaire(request: Request) -> Response:
            # Lue à l'appel, et non à la construction : le chemin public d'une
            # route n'est connu qu'une fois le routeur monté, donc après.
            famille = self.famille_transactionnelle
            reponse = await original(request)
            session: Session | None = getattr(request.state, "session_metreo", None)
            if session is None or not famille.ecrit:
                return reponse
            if reponse.status_code >= 400:
                # La route a déjà décidé de refuser : rien à valider.
                await run_in_threadpool(session.rollback)
                compenser_apres_annulation(session)
                return reponse
            try:
                await run_in_threadpool(session.commit)
            except BaseException:
                # L'ordre compte : d'abord défaire la base, ensuite le volume.
                await run_in_threadpool(session.rollback)
                compenser_apres_annulation(session)
                raise
            _oublier_compensations(session)
            return reponse

        return gestionnaire
