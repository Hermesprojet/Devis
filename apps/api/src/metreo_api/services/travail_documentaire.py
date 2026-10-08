"""Exécuter une étape documentaire hors de toute requête HTTP.

C'est la pièce qui manquait pour qu'un traitement long existe : les quatre
fonctions d'étape (`claim`, `succeed`, `fail`, `retry`) sont écrites depuis la
phase 2A, et **personne ne les appelait**. Elles supposent un appelant qui
ouvre une session, valide la transaction et traduit les refus — ce que fait
une requête FastAPI, et ce que ce module fait pour un worker.

**Pourquoi un module et pas un script.** Un script n'est ni testé, ni typé,
ni relu par les mêmes portes que le reste. Le travail vit donc ici, et
`scripts/lire_un_plan.py` n'est qu'une porte d'entrée.

**Il n'y a pas de file d'attente, et ce module n'en invente pas.** Rien dans
le dépôt ne dépile quoi que ce soit : `apps/worker/` est vide et aucune table
de travaux n'existe. Un appel traite UNE révision, nommée, et s'arrête. C'est
la forme exigée par l'ADR 0007 — un processus par fichier, borné par son
appelant — et c'est aussi la seule honnête : prétendre dépiler demanderait
une file, donc une migration, donc une autre tranche.

**Quatre pièges du socle, et ce qui les désarme ici.**

1. `get_owned` et `lock_owned` lèvent une `HTTPException` 404, y compris hors
   requête. Elles sont attrapées et traduites : un worker qui laisserait
   remonter une exception HTTP hors d'un serveur HTTP donnerait une trace
   illisible pour un fait banal — une révision effacée.
2. Les quatre fonctions d'étape ne font que `flush`. Hors HTTP plus personne
   ne valide : c'est ce module qui valide, et dans l'ordre.
3. Sous SQLite, `lock_owned` ne pose AUCUN verrou. Deux workers simultanés ne
   sont alors sérialisés que par la contrainte d'unicité, qui lève une
   `IntegrityError` ; elle est attrapée et devient « étape déjà prise ».
4. Le journal ne porte jamais de contenu de document. Ni la clé de stockage,
   ni le chemin du fichier, ni le nom d'origine n'y entrent : seulement des
   identifiants, des compteurs, des codes et des durées.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_session_factory
from ..models import DocumentRevision, DocumentStepRun
from ..transactions import compenser_apres_annulation
from . import documents
from .document_storage import StockageLocal

logger = logging.getLogger("metreo.api")


class TravailRefuse(Exception):
    """Le travail n'a pas eu lieu, et le code dit lequel des cas c'est.

    Le message ne contient JAMAIS de chemin, de nom de fichier ni de contenu :
    il peut finir dans un journal, et le journal n'en porte pas.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Issue:
    """Ce qu'une étape a produit, du point de vue de l'état d'exécution."""

    step_run_id: str
    statut: str
    code_erreur: str | None
    duree_ms: int
    tentative: int


def ouvrir_session() -> Session:
    """Une session hors requête.

    `session_scope` est une dépendance FastAPI : elle exige un `Request` et
    pose la session sur `request.state` pour que la classe de route
    transactionnelle la retrouve. Hors requête il n'y a ni l'un ni l'autre, et
    c'est le fabricant qu'il faut appeler — précédent :
    `scripts/purger_organisation.py`.
    """
    return get_session_factory()()


def revision_et_chemin(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    stockage: StockageLocal,
) -> tuple[DocumentRevision, Path]:
    """La révision et le chemin de son original, sans recalculer la clé.

    La clé de stockage est écrite UNE fois, au dépôt, et conservée en base.
    La recomposer depuis (organisation, document, révision) plus une extension
    devinée casserait au premier type détecté autrement — et le type est
    précisément ce qui décide l'extension. On lit donc `revision.storage_key`,
    et le confinement sous la racine est vérifié par `chemin`.
    """
    revision = documents.revision_par_id(
        session, organization_id=organization_id, revision_id=revision_id
    )
    if stockage.taille(revision.storage_key) is None:
        raise TravailRefuse(
            "fichier_absent",
            "L'original de cette révision est introuvable sur le volume. La "
            "racine de stockage du worker est peut-être différente de celle "
            "de l'API.",
        )
    return revision, stockage.chemin(revision.storage_key)


def executer_etape_dans(
    session: Session,
    *,
    organization_id: str,
    revision_id: str,
    step: str,
    pipeline_version: str,
    prompt_version: str,
    model_version: str,
    stockage: StockageLocal,
    travail: Callable[[Session, DocumentRevision, Path], str | None],
    relancer: bool = False,
    a_la_prise: Callable[[DocumentStepRun], None] | None = None,
) -> Issue:
    """Prend une étape et l'exécute DANS la session qu'on lui donne.

    Elle ne valide rien : l'appelant décide quand. C'est ce qui permet aux
    deux appelants de coexister sans copier ce code.

    - **Une requête HTTP** la laisse dans sa transaction unique, validée par
      `RouteTransactionnelle` entre la réponse produite et la réponse envoyée.
      Conséquence voulue : si le travail échoue, la prise disparaît avec le
      reste et aucune ligne ne reste « running ».
    - **Un worker** passe `a_la_prise` pour valider la prise tout de suite :
      un second processus doit la voir, parce que le travail peut durer
      plusieurs secondes. Le prix de cette visibilité est qu'une interruption
      laisserait une ligne « running » ; `executer_etape` la clôt donc en
      échec avant de laisser remonter l'exception, et c'est précisément à
      quoi sert ce rappel — il donne l'étape à qui devra la clore.

    `travail` reçoit la session, la révision et le chemin de l'original ; il
    rend `None` en cas de succès, ou l'un des codes de
    `documents.SAFE_STEP_ERROR_CODES` en cas d'échec métier. Un code libre
    venu d'une bibliothèque ne doit jamais atteindre la base.

    `relancer=True` reprend une étape en échec par `retry_failed_step_run`,
    sans toucher à la clé d'idempotence : relancer en changeant un numéro de
    version créerait une SECONDE ligne, et l'historique des tentatives se
    perdrait.
    """
    debut = time.monotonic_ns()
    revision, chemin = revision_et_chemin(
        session,
        organization_id=organization_id,
        revision_id=revision_id,
        stockage=stockage,
    )

    try:
        etape, creee = documents.claim_step_run(
            session,
            organization_id=organization_id,
            revision_id=revision_id,
            step=step,
            pipeline_version=pipeline_version,
            prompt_version=prompt_version,
            model_version=model_version,
        )
    except IntegrityError as collision:
        # Sous SQLite, `lock_owned` ne verrouille rien : la contrainte
        # d'unicité est la seule sérialisation, et c'est ici qu'elle se
        # manifeste. L'étape est prise par un autre ; ce n'est pas un défaut
        # de programme.
        raise TravailRefuse(
            "etape_deja_prise",
            "Cette étape est déjà prise pour cette révision et ces versions.",
        ) from collision

    if not creee:
        if etape.status == "succeeded":
            raise TravailRefuse(
                "etape_deja_reussie",
                "Cette étape a déjà réussi pour cette révision et ces versions.",
            )
        if etape.status == "failed":
            if not relancer:
                raise TravailRefuse(
                    "etape_en_echec",
                    "Cette étape est en échec. Une relance explicite est demandée.",
                )
            etape, _ = documents.retry_failed_step_run(
                session, organization_id=organization_id, step_run_id=etape.id
            )
        else:
            raise TravailRefuse(
                "etape_deja_prise",
                "Cette étape est déjà en cours pour cette révision et ces versions.",
            )

    if a_la_prise is not None:
        a_la_prise(etape)

    code_erreur = travail(session, revision, chemin)
    duree_ms = max(0, (time.monotonic_ns() - debut) // 1_000_000)

    if code_erreur is None:
        documents.succeed_step_run(
            session,
            organization_id=organization_id,
            step_run_id=etape.id,
            duration_ms=duree_ms,
        )
    else:
        documents.fail_step_run(
            session,
            organization_id=organization_id,
            step_run_id=etape.id,
            error_code=code_erreur,
            duration_ms=duree_ms,
        )

    # Des identifiants, des codes, des compteurs et des durées. Jamais la clé
    # de stockage, le chemin du fichier, le nom d'origine, ni une valeur lue
    # dans le document.
    logger.info(
        "etape_documentaire_terminee",
        extra={
            "organization_id": organization_id,
            "revision_id": revision_id,
            "step": step,
            "statut": "succeeded" if code_erreur is None else "failed",
            "code_erreur": code_erreur,
            "duree_ms": duree_ms,
            "tentative": etape.attempt,
        },
    )
    return Issue(
        step_run_id=etape.id,
        statut="succeeded" if code_erreur is None else "failed",
        code_erreur=code_erreur,
        duree_ms=duree_ms,
        tentative=etape.attempt,
    )


def executer_etape(
    *,
    organization_id: str,
    revision_id: str,
    step: str,
    pipeline_version: str,
    prompt_version: str,
    model_version: str,
    stockage: StockageLocal,
    travail: Callable[[Session, DocumentRevision, Path], str | None],
    relancer: bool = False,
) -> Issue:
    """La même chose pour un worker : sa propre session, et ses `commit`.

    Hors requête, plus personne ne valide : les quatre fonctions d'étape ne
    font que `flush`. La transaction est donc validée ici, deux fois — la
    prise d'abord, pour qu'un second worker la voie, puis l'issue avec tout ce
    que le travail a écrit.
    """
    session = ouvrir_session()
    debut = time.monotonic_ns()
    prise: list[str] = []

    def _valider_la_prise(etape: DocumentStepRun) -> None:
        prise.append(etape.id)
        session.commit()

    try:
        issue = executer_etape_dans(
            session,
            organization_id=organization_id,
            revision_id=revision_id,
            step=step,
            pipeline_version=pipeline_version,
            prompt_version=prompt_version,
            model_version=model_version,
            stockage=stockage,
            travail=travail,
            relancer=relancer,
            a_la_prise=_valider_la_prise,
        )
        session.commit()
        return issue
    except (HTTPException, documents.RevisionRefusee) as refus:
        # `get_owned` et `lock_owned` lèvent un 404 de FastAPI, même ici : une
        # exception HTTP hors d'un serveur HTTP donnerait une trace illisible
        # pour un fait banal — une révision effacée.
        session.rollback()
        compenser_apres_annulation(session)
        raise TravailRefuse(
            "revision_introuvable",
            "Cette révision n'existe pas pour cette organisation.",
        ) from refus
    except documents.DocumentStepRunRefused as refus:
        session.rollback()
        compenser_apres_annulation(session)
        raise TravailRefuse(str(refus), "L'étape a été refusée par le service.") from refus
    except TravailRefuse:
        # L'ordre compte : défaire la base d'abord, le volume ensuite. Une
        # compensation jouée avant le rollback pourrait retirer un fichier que
        # la transaction, finalement, conserve.
        session.rollback()
        compenser_apres_annulation(session)
        raise
    except Exception:
        session.rollback()
        compenser_apres_annulation(session)
        _clore_une_prise_orpheline(session, organization_id, prise, debut)
        raise
    finally:
        session.close()


def _clore_une_prise_orpheline(
    session: Session,
    organization_id: str,
    prise: list[str],
    debut: int,
) -> None:
    """Clôt en échec une étape prise puis abandonnée par une exception.

    Sans elle, l'étape resterait « running » POUR TOUJOURS : la prise est
    déjà validée, et plus rien ne viendrait la clore. Elle ne serait alors ni
    relançable — `retry_failed_step_run` n'accepte qu'un échec — ni
    reprenable, sa clé d'idempotence étant occupée.

    Ce chemin ne s'emprunte que depuis le worker, parce que lui seul valide la
    prise avant le travail. Et il ne doit jamais masquer la cause : si clore
    échoue à son tour, on le journalise et on laisse remonter l'exception
    d'origine.
    """
    if not prise:
        return
    duree_ms = max(0, (time.monotonic_ns() - debut) // 1_000_000)
    try:
        documents.fail_step_run(
            session,
            organization_id=organization_id,
            step_run_id=prise[0],
            error_code="processing_failed",
            duration_ms=duree_ms,
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception(
            "cloture_de_letape_impossible",
            extra={"organization_id": organization_id, "step_run_id": prise[0]},
        )
    else:
        logger.warning(
            "etape_documentaire_interrompue",
            extra={
                "organization_id": organization_id,
                "step_run_id": prise[0],
                "duree_ms": duree_ms,
            },
        )
