"""Le lien par lequel un destinataire consulte son devis, sans compte Metreo.

**Ce que la base ne contient pas.** Le secret du lien n'est jamais stocké :
seule son empreinte SHA-256 l'est, et le secret brut n'est rendu qu'une fois,
à la création. Une copie de la base — sauvegarde, export, fuite — ne permet
donc d'ouvrir aucun devis. Il en va de même du jeton de session publique.

**Où le secret circule, et où il ne circule pas.** Jamais dans un chemin, une
chaîne de requête, un journal, un audit, une métrique ou un message d'erreur —
tout cela finit dans des fichiers que d'autres lisent. Il vit dans le FRAGMENT
de l'URL, que le navigateur n'envoie jamais au serveur, et la page l'échange
aussitôt contre une session courte en cookie `HttpOnly`.

**La révocation vaut immédiatement, y compris pour les sessions ouvertes.**
Une session ne porte aucun droit propre : elle désigne un lien, et se relit à
travers lui. Un lien révoqué ferme donc tout ce qui pendait à lui, sans avoir
à parcourir les sessions.

**Un devis qui porte les coûts internes ne se partage pas.** Ce n'est pas un
réglage d'affichage : le PDF remis EST le document, et il montre le déboursé,
le revient et la marge. Le partager reviendrait à envoyer la structure de prix
de l'entreprise à son client. L'émetteur doit repartir d'une version client.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..models import IssuedQuote, QuotePublicSession, QuoteShareLink, utcnow
from ..security.auth import TenantContext
from . import audit
from .cycle_devis import inscrire

#: 32 octets = 256 bits d'entropie, rendus en base64url par `token_urlsafe`.
#: Le minimum exigé, et il est vérifié par un test plutôt que promis ici.
OCTETS_DE_SECRET = 32

#: Durée de vie par défaut d'un lien, bornée par la validité du devis.
JOURS_DE_LIEN = 30

#: La session publique est COURTE : elle sert à consulter et à répondre, pas à
#: garder un accès. Un destinataire qui revient rouvre son lien.
MINUTES_DE_SESSION = 60

#: Le nom du cookie. Aucun secret n'y transite en clair côté serveur : le
#: cookie porte le jeton, la base n'en garde que l'empreinte.
COOKIE = "metreo_devis"


def empreinte(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class PartageRefuse(DomainError):
    def __init__(self, code: str, message: str, **contexte: Any) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True, slots=True)
class LienCree:
    """Le lien, et son secret — la seule fois où celui-ci existe en clair."""

    lien: QuoteShareLink
    secret: str


def liens_du_devis(session: Session, devis: IssuedQuote) -> list[QuoteShareLink]:
    return list(
        session.scalars(
            select(QuoteShareLink)
            .where(QuoteShareLink.issued_quote_id == devis.id)
            .order_by(QuoteShareLink.created_at.desc())
        ).all()
    )


def lien_actif(liens: list[QuoteShareLink], *, maintenant: datetime) -> QuoteShareLink | None:
    for lien in liens:
        if lien.revoked_at is None and lien.expires_at > maintenant:
            return lien
    return None


def creer(
    session: Session,
    *,
    context: TenantContext,
    devis: IssuedQuote,
    jours: int | None = None,
) -> LienCree:
    """Fabrique un lien, révoque le précédent, et rend le secret UNE fois."""
    if devis.include_internal_costs:
        raise PartageRefuse(
            "internal_costs_not_shareable",
            "Ce devis a été émis avec les coûts internes : déboursé, prix de "
            "revient et marge y figurent. Il ne peut pas être partagé avec le "
            "client. Créez une nouvelle version et émettez-la sans les coûts "
            "internes.",
        )

    maintenant = utcnow()
    if devis.valid_until < maintenant.date():
        raise PartageRefuse(
            "quote_expired",
            f"Ce devis n'était valable que jusqu'au "
            f"{devis.valid_until.strftime('%d/%m/%Y')} : il n'y a plus rien à "
            "partager. Émettez une nouvelle offre.",
        )

    for ancien in liens_du_devis(session, devis):
        if ancien.revoked_at is None:
            _revoquer(session, context=context, devis=devis, lien=ancien, quand=maintenant)

    #: L'échéance du lien ne dépasse jamais la validité du devis : un document
    #: périmé ne se consulte pas plus longtemps que ce qui a été promis.
    demande = maintenant + timedelta(days=jours or JOURS_DE_LIEN)
    fin_de_validite = datetime.combine(devis.valid_until, datetime.max.time()).replace(
        microsecond=0
    )
    expiration = min(demande, fin_de_validite)

    secret = secrets.token_urlsafe(OCTETS_DE_SECRET)
    lien = QuoteShareLink(
        organization_id=devis.organization_id,
        issued_quote_id=devis.id,
        secret_sha256=empreinte(secret),
        expires_at=expiration,
        created_by=context.user.id,
    )
    session.add(lien)
    session.flush()

    inscrire(
        session,
        devis=devis,
        kind="link_created",
        channel="public_link",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        comment=None,
    )
    audit.record(
        session,
        organization_id=devis.organization_id,
        action="quote.link_created",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Lien de consultation créé pour le devis {devis.number}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        # Le secret n'est PAS ici, et ne le sera jamais : un journal d'audit se
        # lit, s'exporte et se sauvegarde. Seul l'identifiant du lien y figure.
        payload={"link_id": lien.id, "expires_at": expiration.isoformat()},
    )
    return LienCree(lien=lien, secret=secret)


def _revoquer(
    session: Session,
    *,
    context: TenantContext,
    devis: IssuedQuote,
    lien: QuoteShareLink,
    quand: datetime,
) -> None:
    lien.revoked_at = quand
    lien.revoked_by = context.user.id
    session.flush()
    inscrire(
        session,
        devis=devis,
        kind="link_revoked",
        channel="public_link",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
    )
    audit.record(
        session,
        organization_id=devis.organization_id,
        action="quote.link_revoked",
        object_type="issued_quote",
        object_id=devis.id,
        summary=f"Lien de consultation révoqué pour le devis {devis.number}",
        actor_user_id=context.user.id,
        actor_email=context.user.email,
        payload={"link_id": lien.id},
    )


def revoquer(
    session: Session, *, context: TenantContext, devis: IssuedQuote, lien_id: str
) -> QuoteShareLink:
    lien = session.get(QuoteShareLink, lien_id)
    if lien is None or lien.organization_id != context.organization_id:
        raise PartageRefuse("link_not_found", "Ce lien n'existe pas.")
    if lien.issued_quote_id != devis.id:
        raise PartageRefuse("link_not_found", "Ce lien n'appartient pas à ce devis.")
    if lien.revoked_at is None:
        _revoquer(session, context=context, devis=devis, lien=lien, quand=utcnow())
    return lien


# ---------------------------------------------------------------------------
# Le côté public
# ---------------------------------------------------------------------------


def ouvrir_une_session(session: Session, *, secret: str) -> tuple[QuoteShareLink, str]:
    """Échange le secret contre un jeton de session. Rend (lien, jeton brut).

    La recherche se fait par EMPREINTE : le secret n'est comparé à rien, il est
    haché puis servi d'index. Un secret faux ne trouve simplement aucune ligne,
    en un temps qui ne dépend pas de sa ressemblance avec le bon.
    """
    lien = session.scalars(
        select(QuoteShareLink).where(QuoteShareLink.secret_sha256 == empreinte(secret))
    ).one_or_none()
    maintenant = utcnow()
    # Un seul et même refus pour « inconnu », « révoqué » et « périmé » : les
    # distinguer dirait à qui essaie s'il a trouvé un lien qui a existé.
    if lien is None or lien.revoked_at is not None or lien.expires_at <= maintenant:
        raise PartageRefuse(
            "link_unusable",
            "Ce lien n'est plus valable. Demandez-en un nouveau à l'entreprise.",
        )

    jeton = secrets.token_urlsafe(OCTETS_DE_SECRET)
    session.add(
        QuotePublicSession(
            organization_id=lien.organization_id,
            share_link_id=lien.id,
            token_sha256=empreinte(jeton),
            expires_at=min(maintenant + timedelta(minutes=MINUTES_DE_SESSION), lien.expires_at),
        )
    )
    session.flush()
    return lien, jeton


def lien_de_session(session: Session, *, jeton: str) -> QuoteShareLink:
    """Le lien derrière une session, ou un refus. Relit TOUJOURS le lien.

    C'est ce qui rend la révocation immédiate : la session n'est pas parcourue
    ni détruite, elle est simplement inutile dès l'instant où le lien qu'elle
    désigne ne vaut plus.
    """
    publique = session.scalars(
        select(QuotePublicSession).where(QuotePublicSession.token_sha256 == empreinte(jeton))
    ).one_or_none()
    maintenant = utcnow()
    if publique is None or publique.expires_at <= maintenant:
        raise PartageRefuse("session_expired", "Votre session a expiré. Rouvrez le lien.")

    lien = session.get(QuoteShareLink, publique.share_link_id)
    if lien is None or lien.revoked_at is not None or lien.expires_at <= maintenant:
        raise PartageRefuse(
            "link_unusable",
            "Ce lien n'est plus valable. Demandez-en un nouveau à l'entreprise.",
        )
    return lien


def devis_de_session(session: Session, *, jeton: str) -> tuple[QuoteShareLink, IssuedQuote]:
    lien = lien_de_session(session, jeton=jeton)
    devis = session.get(IssuedQuote, lien.issued_quote_id)
    if devis is None:
        raise PartageRefuse("link_unusable", "Ce devis n'est plus disponible.")
    return lien, devis
