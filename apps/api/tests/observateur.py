"""Regarder la base à l'instant précis où la réponse part vers le client.

C'est le mécanisme qui a prouvé la course du parcours de connexion (PR #52),
généralisé ici à toutes les routes d'écriture.

Le principe : envelopper l'application ASGI par l'EXTÉRIEUR, intercepter
`http.response.start` — le moment où les premiers octets partent — et, à cet
instant, interroger la base par une session NEUVE. Une session neuve ne voit
que ce qui est validé ; c'est exactement ce que verra la requête suivante du
client. Si elle ne trouve pas ce que la réponse annonce, le client court une
course qu'il ne peut pas gagner.

Rien de statistique là-dedans : c'est l'ORDRE qui est éprouvé, pas la chance.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class Constat:
    """Ce qu'une session indépendante voyait quand la réponse est partie."""

    methode: str
    chemin: str
    code: int
    vu: Any

    def __str__(self) -> str:  # pragma: no cover - confort de lecture des échecs
        return f"{self.methode} {self.chemin} → {self.code} ; vu : {self.vu!r}"


def enveloppe_observatrice(
    application: Any, sonde: Callable[[Session], Any]
) -> tuple[Any, list[Constat]]:
    """Rend l'application enveloppée et la liste — vivante — des constats."""
    constats: list[Constat] = []

    def regarder() -> Any:
        from metreo_api.db import get_session_factory

        with get_session_factory()() as session:
            return sonde(session)

    async def enveloppe(scope: dict, receive: Any, send: Any) -> None:
        async def envoyer(message: dict) -> None:
            if message["type"] == "http.response.start" and scope.get("type") == "http":
                constats.append(
                    Constat(
                        methode=scope.get("method", "?"),
                        chemin=scope.get("path", "?"),
                        code=message["status"],
                        vu=regarder(),
                    )
                )
            await send(message)

        await application(scope, receive, envoyer)

    return enveloppe, constats
