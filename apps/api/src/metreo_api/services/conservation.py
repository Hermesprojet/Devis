"""Détruire une organisation : la seule porte, et ce qui la garde fermée.

Ce module tient la politique de conservation décidée pour ce dépôt. Elle tient
en une phrase :

    Rien ne se détruit sans un écrit préalable qui dit ce qui va être détruit,
    et qui survit à la destruction.

**Ce que le module décide.** La MÉCANIQUE : ce qui est refusé, dans quel ordre
les choses partent, ce qui reste inscrit après. C'est de l'architecture, elle
se démontre, et les tests la démontrent.

**Ce que le module refuse de décider.** La DURÉE de conservation. Une durée est
une règle réglementaire : elle a un pays, une version, une date d'effet et une
source officielle datée. Le dépôt n'en détient aucune — les packs régionaux
sont en `draft` et leur `sources` est vide. Le code n'en affirme donc aucune :
`OrganizationSettings.quote_retention_years` vaut `None` tant que personne ne
l'a tranchée, et `None` fait REFUSER la purge. Un défaut à sept ans, ou à dix,
serait un avis juridique rendu par une valeur par défaut.

**L'ordre, et pourquoi il n'y en a qu'un de sûr.** Deux ressources sans
transaction commune : des lignes en base, des fichiers sur un volume.

* fichiers d'abord, lignes ensuite — si la base échoue, il reste des lignes qui
  désignent des fichiers absents : un devis qui existe et ne se télécharge
  plus. Le pire des deux.
* lignes d'abord, fichiers ensuite — si le volume échoue, il reste des fichiers
  que plus rien ne désigne. Réparable, à une condition : que l'écrit dise
  lesquels.

C'est donc le second, et l'écrit est `OrganizationPurge.documents`. Une purge
interrompue se reprend par `reprendre()` sans rien redécouvrir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_domain.errors import DomainError

from ..models import IssuedQuote, Organization, OrganizationPurge, OrganizationSettings, utcnow
from ..services.document_storage import StockageLocal
from . import audit


class PurgeRefusee(DomainError):
    """La destruction est refusée, et le message dit par quoi."""

    def __init__(self, code: str, message: str, **contexte: object) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True, slots=True)
class Document:
    """Un PDF à détruire, décrit par ce qui suffit à le retrouver et à le nommer.

    Aucun nom de client ni de chantier : le registre prouve ce qui a été
    détruit, il ne conserve pas ce que la destruction visait à effacer.
    """

    quote_id: str
    number: str
    storage_key: str
    sha256: str

    def en_json(self) -> dict[str, str]:
        return {
            "quote_id": self.quote_id,
            "number": self.number,
            "storage_key": self.storage_key,
            "sha256": self.sha256,
        }


def politique(session: Session, organization_id: str) -> int | None:
    """La durée de conservation réglée, ou `None` si la question est ouverte."""
    reglages = session.get(OrganizationSettings, organization_id)
    return reglages.quote_retention_years if reglages else None


def echeance(emis_le: datetime, annees: int) -> date:
    """Le jour où la conservation d'un devis cesse d'être exigée.

    Des années CALENDAIRES, et non un nombre de jours moyen. Une première
    version comptait en tranches de 365,25 jours : le seuil tombait alors un
    jour à côté de la date anniversaire, et « sept ans » ne voulait plus dire
    ce que tout le monde entend par sept ans. Un seuil réglementaire qui
    dérive d'un jour est un seuil faux.

    Le 29 février se replie sur le 28 : c'est la convention la plus courante,
    et la seule qui ne fasse pas disparaître une date un an sur quatre.
    """
    jour = emis_le.date()
    try:
        return jour.replace(year=jour.year + annees)
    except ValueError:
        return jour.replace(year=jour.year + annees, day=28)


def devis_retenus(
    session: Session, organization_id: str, *, annees: int, aujourdhui: date
) -> list[IssuedQuote]:
    """Les devis encore dans leur durée de conservation. Vide autorise la purge."""
    devis = session.scalars(
        select(IssuedQuote).where(IssuedQuote.organization_id == organization_id)
    ).all()
    return [d for d in devis if aujourdhui < echeance(d.issued_at, annees)]


def documents_a_detruire(session: Session, organization_id: str) -> list[Document]:
    devis = session.scalars(
        select(IssuedQuote)
        .where(IssuedQuote.organization_id == organization_id)
        .order_by(IssuedQuote.number)
    ).all()
    return [
        Document(
            quote_id=d.id,
            number=d.number,
            storage_key=d.pdf_storage_key,
            sha256=d.pdf_sha256,
        )
        for d in devis
    ]


def demander(
    session: Session,
    *,
    organization_id: str,
    reason: str,
    requested_by: str | None = None,
    aujourdhui: date | None = None,
    sans_retention: bool = False,
) -> OrganizationPurge:
    """Inscrit la destruction AVANT de détruire quoi que ce soit.

    Trois refus, dans cet ordre : l'organisation n'existe pas ; le motif est
    vide ; la politique de conservation n'a pas été tranchée ou n'est pas
    échue. Aucun d'eux n'écrit quoi que ce soit.

    `sans_retention` saute le TROISIÈME refus, et lui seul. Un seul appelant
    l'emploie : `seed --reset`, qui ne détruit que les organisations semées par
    ce module, retrouvées par leur nom exact. Le paramètre est explicite et
    nommé plutôt que déduit, la ligne du registre l'enregistre
    (`retention_years_applied` à 0, motif nommant la réinitialisation), et les
    deux premiers refus continuent de s'appliquer : même le jeu de
    démonstration ne se détruit pas sans écrit.
    """
    aujourdhui = aujourdhui or utcnow().date()

    organisation = session.get(Organization, organization_id)
    if organisation is None:
        raise PurgeRefusee(
            "organization_unknown",
            "organisation introuvable : rien à détruire",
            organization_id=organization_id,
        )

    motif = (reason or "").strip()
    if not motif:
        raise PurgeRefusee(
            "purge_reason_required",
            "une destruction sans motif écrit n'est pas une destruction encadrée",
        )

    if sans_retention:
        annees = 0
    else:
        decidee = politique(session, organization_id)
        if decidee is None:
            raise PurgeRefusee(
                "quote_retention_undecided",
                "la durée de conservation des devis émis n'a pas été décidée pour "
                "cette organisation ; tant qu'elle ne l'est pas, la destruction est "
                "refusée plutôt qu'appliquée sur une durée supposée",
                organization_id=organization_id,
            )
        annees = decidee
        retenus = devis_retenus(session, organization_id, annees=annees, aujourdhui=aujourdhui)
        if retenus:
            raise PurgeRefusee(
                "quote_retention_not_elapsed",
                f"{len(retenus)} devis émis sont encore dans leur durée de "
                f"conservation de {annees} an(s)",
                organization_id=organization_id,
                retention_years=annees,
                retained=[d.number for d in retenus],
            )

    documents = documents_a_detruire(session, organization_id)
    purge = OrganizationPurge(
        organization_id=organization_id,
        status="requested",
        requested_by=requested_by,
        reason=motif[:500],
        retention_years_applied=annees,
        quote_count=len(documents),
        documents=[d.en_json() for d in documents],
        files_deleted=0,
        files_failed=[],
    )
    session.add(purge)

    # Le journal d'audit de l'organisation va disparaître avec elle. On y écrit
    # quand même : tant que la purge n'est pas exécutée, cette ligne est la
    # trace visible d'une demande, et une demande abandonnée doit se voir.
    audit.record(
        session,
        organization_id=organization_id,
        action="organization.purge_requested",
        object_type="organization_purge",
        object_id=purge.id,
        summary=f"destruction demandée : {len(documents)} devis émis",
        actor_user_id=requested_by,
        payload={"retention_years": annees, "quote_count": len(documents)},
    )
    session.flush()
    return purge


def executer(session: Session, purge: OrganizationPurge) -> OrganizationPurge:
    """Détruit les lignes. Les fichiers viennent après, et séparément.

    Cette fonction s'arrête à `rows_deleted` À DESSEIN : le volume ne participe
    pas à la transaction, et prétendre le contraire produirait le seul état
    vraiment mauvais — une base qui se croit propre et un volume qui ne l'est
    pas, sans écrit pour les réconcilier.
    """
    if purge.status not in {"requested"}:
        raise PurgeRefusee(
            "purge_not_pending",
            f"cette purge est en état « {purge.status} » : elle ne peut plus détruire de lignes",
            purge_id=purge.id,
        )

    # Les devis d'abord : `organization_id` retient en RESTRICT, l'organisation
    # ne partirait pas avant eux. Le déclencheur de conservation les laisse
    # passer PARCE QUE cette purge est inscrite et active — c'est la ligne du
    # registre qui autorise, rien d'autre.
    for devis in session.scalars(
        select(IssuedQuote).where(IssuedQuote.organization_id == purge.organization_id)
    ).all():
        session.delete(devis)
    session.flush()

    organisation = session.get(Organization, purge.organization_id)
    if organisation is not None:
        session.delete(organisation)
    session.flush()

    purge.status = "rows_deleted"
    session.flush()
    return purge


def retirer_les_fichiers(
    session: Session, purge: OrganizationPurge, stockage: StockageLocal
) -> OrganizationPurge:
    """Retire du volume ce que le registre a nommé, et referme la purge.

    Idempotente : un fichier déjà absent compte comme retiré. C'est ce qui rend
    `reprendre()` sûr après n'importe quelle interruption.
    """
    if purge.status not in {"rows_deleted", "failed"}:
        raise PurgeRefusee(
            "purge_rows_not_deleted",
            f"cette purge est en état « {purge.status} » : ses lignes ne sont pas encore détruites",
            purge_id=purge.id,
        )

    retires = 0
    echecs: list[dict[str, str]] = []
    for entree in purge.documents:
        cle = str(entree.get("storage_key", ""))
        try:
            stockage.supprimer(cle)
            retires += 1
        except OSError as erreur:  # pragma: no cover - dépend du volume
            echecs.append({"storage_key": cle, "erreur": str(erreur)})

    purge.files_deleted = retires
    purge.files_failed = echecs
    purge.status = "completed" if not echecs else "failed"
    purge.completed_at = utcnow()
    session.flush()
    return purge


def reprendre(
    session: Session, purge: OrganizationPurge, stockage: StockageLocal
) -> OrganizationPurge:
    """Achève une purge interrompue, depuis l'état où elle s'est arrêtée."""
    if purge.status == "completed":
        return purge
    if purge.status == "requested":
        executer(session, purge)
    return retirer_les_fichiers(session, purge, stockage)


def orphelins(session: Session, stockage: StockageLocal) -> list[str]:
    """Les fichiers qu'une purge a nommés et que le volume porte encore.

    Sert au contrôle, pas au fonctionnement : une purge terminée doit rendre
    une liste vide, et c'est vérifiable sans relire le code qui l'a produite.
    """
    restants: list[str] = []
    for purge in session.scalars(select(OrganizationPurge)).all():
        for entree in purge.documents:
            cle = str(entree.get("storage_key", ""))
            if cle and stockage.chemin(cle).exists():
                restants.append(cle)
    return restants
