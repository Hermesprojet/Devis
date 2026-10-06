"""Exécuter une étape documentaire HORS requête : ce qu'un worker promet.

Les quatre fonctions d'étape — `claim`, `succeed`, `fail`, `retry` — existent
depuis la phase 2A et **personne ne les appelait** : elles supposent un
appelant qui ouvre une session, valide la transaction et traduit les refus.
Ce module de test éprouve cet appelant, et surtout les quatre pièges du socle
qu'il désarme : le 404 de FastAPI levé hors requête, le `flush` sans `commit`,
l'absence de verrou sous SQLite, et le journal qui ne porte pas de contenu.

Tout part d'un dépôt RÉEL par l'API : c'est la seule façon d'obtenir une clé
de stockage que personne n'a choisie, et c'est exactement ce que le premier
test interroge. Le `travail` lui-même est le plus souvent une fonction de
quatre lignes, parce que le sujet ici n'est pas ce qui est fait de l'original
mais la façon dont son exécution est consignée.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from metreo_api.config import get_settings
from metreo_api.db import get_session_factory
from metreo_api.models import DocumentRevision, DocumentStepRun
from metreo_api.services import documents, lecture_de_plan, travail_documentaire
from metreo_api.services.document_storage import StockageLocal

from .conftest import login

#: Un DXF ASCII minimal, écrit à la main : il est reconnu par la détection de
#: type et se relit sans ezdxf. Les tests qui ont besoin d'une géométrie, eux,
#: fabriquent leur fichier et portent leur propre garde d'extra.
DXF_MINIMAL = b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1009\n  0\nENDSEC\n  0\nEOF\n"

PIPELINE = "travail-documentaire-de-test-v1"
PROMPT = "none"
MODELE = "aucun-modele"


@dataclass(frozen=True)
class Depot:
    """Un original réellement déposé, et de quoi le retrouver sans le deviner."""

    organization_id: str
    document_id: str
    revision_id: str
    contenu: bytes
    sha256: str
    storage_key: str
    stockage: StockageLocal


def _deposer(
    client: TestClient,
    entetes: dict[str, str],
    reference: str,
    *,
    contenu: bytes = DXF_MINIMAL,
    nom: str = "piece-jointe.bin",
    type_annonce: str = "application/octet-stream",
) -> Depot:
    """Projet, document, révision — par l'API, comme un utilisateur le ferait.

    Le nom et le type annoncés sont volontairement FAUX : c'est la signature
    des octets qui décide, et la clé de stockage en découle. Un worker qui
    recomposerait la clé depuis le nom reçu chercherait au mauvais endroit, et
    c'est ce que le premier test rend visible.
    """
    identite = client.get("/api/v1/auth/me", headers=entetes)
    assert identite.status_code == 200, identite.text
    organization_id = str(identite.json()["organization_id"])

    projet = client.post(
        "/api/v1/projects",
        headers=entetes,
        json={"reference": reference, "name": "Chantier hors requête"},
    )
    assert projet.status_code == 201, projet.text
    document = client.post(
        f"/api/v1/projects/{projet.json()['id']}/documents",
        headers=entetes,
        json={"title": "Pièce à traiter"},
    )
    assert document.status_code == 201, document.text
    revision = client.post(
        f"/api/v1/documents/{document.json()['id']}/revisions",
        headers=entetes,
        files={"file": (nom, contenu, type_annonce)},
    )
    assert revision.status_code == 201, revision.text
    corps = revision.json()

    session = get_session_factory()()
    try:
        enregistree = session.get(DocumentRevision, str(corps["id"]))
        assert enregistree is not None
        storage_key = enregistree.storage_key
    finally:
        session.close()

    return Depot(
        organization_id=organization_id,
        document_id=str(document.json()["id"]),
        revision_id=str(corps["id"]),
        contenu=contenu,
        sha256=str(corps["sha256"]),
        storage_key=storage_key,
        stockage=StockageLocal(get_settings().storage_root),
    )


def _executer(
    depot: Depot,
    *,
    step: str = "cad_read",
    travail: lecture_de_plan.Travail,
    relancer: bool = False,
) -> travail_documentaire.Issue:
    return travail_documentaire.executer_etape(
        organization_id=depot.organization_id,
        revision_id=depot.revision_id,
        step=step,
        pipeline_version=PIPELINE,
        prompt_version=PROMPT,
        model_version=MODELE,
        stockage=depot.stockage,
        travail=travail,
        relancer=relancer,
    )


def _etapes(depot: Depot) -> list[DocumentStepRun]:
    session = get_session_factory()()
    try:
        return list(
            session.scalars(
                select(DocumentStepRun)
                .where(DocumentStepRun.revision_id == depot.revision_id)
                .order_by(DocumentStepRun.created_at)
            ).all()
        )
    finally:
        session.close()


def _rien(_session: Session, _revision: DocumentRevision, _chemin: Path) -> str | None:
    """Un travail qui réussit sans rien écrire."""
    return None


# ---------------------------------------------------------------------------
# Retrouver l'original
# ---------------------------------------------------------------------------


def test_the_worker_opens_the_stored_original_from_the_organisation_and_revision_alone(
    seeded_client: TestClient,
) -> None:
    """La clé de stockage est écrite UNE fois, au dépôt, et jamais recomposée.

    Son extension dépend du type réellement détecté dans les octets — et le
    type est précisément ce qui décide l'extension. Ce dépôt le met en scène :
    le fichier est annoncé « piece-jointe.bin / application/octet-stream », et
    il est rangé en `.dxf` parce que ses octets sont ceux d'un dessin.

    Un worker qui recomposerait la clé depuis (organisation, document,
    révision) plus une extension devinée chercherait `.bin` ou `.pdf`, et ne
    trouverait rien. Les deux dernières assertions le montrent : les chemins
    qu'une telle reconstruction viserait n'existent pas sur le volume.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-CLE")

    assert depot.storage_key.endswith(".dxf"), depot.storage_key
    session = get_session_factory()()
    try:
        revision, chemin = travail_documentaire.revision_et_chemin(
            session,
            organization_id=depot.organization_id,
            revision_id=depot.revision_id,
            stockage=depot.stockage,
        )
    finally:
        session.close()

    octets = chemin.read_bytes()
    assert octets == depot.contenu
    assert hashlib.sha256(octets).hexdigest() == depot.sha256 == revision.sha256
    assert revision.organization_id == depot.organization_id

    for extension in (".bin", ".pdf"):
        devinee = (
            f"documents/{depot.organization_id}/{depot.document_id}/{depot.revision_id}{extension}"
        )
        assert depot.stockage.taille(devinee) is None, (
            f"une clé reconstruite en {extension} trouve un fichier : le test ne prouve plus rien"
        )


def test_a_revision_of_another_organisation_is_never_found(
    seeded_client: TestClient,
) -> None:
    """Le 404 de FastAPI, levé HORS d'un serveur HTTP, est traduit et non subi.

    `get_owned` et `lock_owned` lèvent une `HTTPException` même dans un
    worker. La laisser remonter donnerait une trace illisible pour un fait
    banal — une révision effacée, ou une révision qui n'est pas à nous. Le
    code `revision_introuvable` est ce que l'exploitant lira.

    Le même `revision_id` est demandé par les deux organisations : c'est
    l'organisation SEULE qui change, et le refus porte donc sur le
    cloisonnement et sur rien d'autre.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    etranger = login(seeded_client, "admin@janssens.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-TENANT")
    autre = seeded_client.get("/api/v1/auth/me", headers=etranger).json()["organization_id"]
    assert autre != depot.organization_id

    with pytest.raises(travail_documentaire.TravailRefuse) as refus:
        travail_documentaire.executer_etape(
            organization_id=str(autre),
            revision_id=depot.revision_id,
            step="cad_read",
            pipeline_version=PIPELINE,
            prompt_version=PROMPT,
            model_version=MODELE,
            stockage=depot.stockage,
            travail=_rien,
        )
    assert refus.value.code == "revision_introuvable"
    assert depot.revision_id not in refus.value.message, (
        "le message d'un refus peut finir dans un journal"
    )
    assert _etapes(depot) == [], "aucune étape n'a été prise pour l'organisation étrangère"


# ---------------------------------------------------------------------------
# L'idempotence et les reprises
# ---------------------------------------------------------------------------


def test_a_step_claimed_twice_runs_only_once(seeded_client: TestClient) -> None:
    """La clé d'idempotence est (organisation, révision, étape, trois versions).

    Sous SQLite, `lock_owned` ne pose AUCUN verrou : la contrainte d'unicité
    est la seule sérialisation. Deux exécutions successives doivent donc
    donner une ligne et une seule, et la seconde doit être REFUSÉE par son
    nom — pas réussir en silence, ce qui laisserait croire que le travail a
    été refait.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-DEUX-FOIS")
    passages: list[str] = []

    def travail(_session: Session, revision: DocumentRevision, _chemin: Path) -> str | None:
        passages.append(revision.id)
        return None

    issue = _executer(depot, travail=travail)
    assert issue.statut == "succeeded"

    with pytest.raises(travail_documentaire.TravailRefuse) as refus:
        _executer(depot, travail=travail)
    assert refus.value.code == "etape_deja_reussie"

    assert passages == [depot.revision_id], "le travail a été exécuté deux fois"
    (etape,) = _etapes(depot)
    assert etape.status == "succeeded"
    assert etape.attempt == 1
    assert etape.id == issue.step_run_id


def test_a_failed_step_records_only_a_bounded_error_code(seeded_client: TestClient) -> None:
    """Un code de six valeurs possibles, et AUCUN résumé libre.

    Le fichier déposé ici porte un cycle de référence de bloc : le lecteur le
    refuse, parce que le parcourir serait une récursion sans fin. Le travail
    réel — `travail_de_lecture` — traduit ce refus en l'un des codes de
    `SAFE_STEP_ERROR_CODES`, et c'est la seule chose qui atteint la base.

    `error_summary` reste `None` délibérément : un message de bibliothèque ou
    un extrait de document y entrerait, et cette colonne est lue par des gens
    qui n'ont pas accès aux documents. Le détail, lui, vit dans le journal
    sous forme de code.
    """
    ezdxf = pytest.importorskip(
        "ezdxf",
        reason="l'extra « plans » n'est pas installé : pip install './apps/api[plans]'",
    )
    document = ezdxf.new("R2013", setup=True)
    premier = document.blocks.new("PREMIER")
    second = document.blocks.new("SECOND")
    premier.add_blockref("SECOND", (0, 0))
    second.add_blockref("PREMIER", (0, 0))
    document.modelspace().add_blockref("PREMIER", (0, 0))
    tampon = io.StringIO()
    document.write(tampon)

    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(
        seeded_client,
        admin,
        "TRAVAIL-ECHEC",
        contenu=tampon.getvalue().encode("utf-8"),
        nom="cycle.dxf",
        type_annonce="image/vnd.dxf",
    )

    issue = _executer(depot, travail=lecture_de_plan.travail_de_lecture(depot.stockage))

    assert issue.statut == "failed"
    assert issue.code_erreur in documents.SAFE_STEP_ERROR_CODES
    (etape,) = _etapes(depot)
    assert etape.status == "failed"
    assert etape.error_code == issue.code_erreur
    assert etape.error_summary is None, "un résumé libre a atteint la base"
    assert etape.finished_at is not None
    assert etape.duration_ms is not None and etape.duration_ms >= 0


def test_a_step_interrupted_by_an_exception_does_not_stay_running_for_ever(
    seeded_client: TestClient,
) -> None:
    """**Le cas qui justifie `_clore_une_prise_orpheline`.**

    Un worker valide sa prise TOUT DE SUITE, pour qu'un second processus la
    voie : le travail peut durer plusieurs secondes. Le prix de cette
    visibilité est qu'une exception quelconque — une panne de réseau, un bug,
    un `MemoryError` — laisserait la ligne « running » POUR TOUJOURS. Elle ne
    serait alors ni relançable, `retry_failed_step_run` n'acceptant qu'un
    échec, ni reprenable, sa clé d'idempotence étant occupée : l'étape
    deviendrait définitivement inatteignable pour cette révision.

    L'exception d'origine doit remonter TELLE QUELLE — la clôture ne doit
    jamais masquer la cause — et la ligne doit être en échec, donc
    relançable. Les deux sont vérifiés, et la relance l'est aussi : une ligne
    « failed » qu'on ne pourrait pas reprendre ne vaudrait pas mieux.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-COUPE")

    def travail_qui_casse(
        _session: Session, _revision: DocumentRevision, _chemin: Path
    ) -> str | None:
        raise RuntimeError("une panne quelconque, pas un refus métier")

    with pytest.raises(RuntimeError, match="une panne quelconque"):
        _executer(depot, travail=travail_qui_casse)

    (etape,) = _etapes(depot)
    assert etape.status == "failed", "la prise est restée « running » : la clé est condamnée"
    assert etape.error_code == "processing_failed"
    assert etape.error_summary is None, "le texte de l'exception a atteint la base"
    assert etape.attempt == 1

    # Et elle est bien relançable : c'est tout l'intérêt de la clore.
    reprise = _executer(depot, travail=_rien, relancer=True)
    assert reprise.statut == "succeeded"
    assert reprise.step_run_id == etape.id
    assert reprise.tentative == 2


def test_a_failed_step_can_be_retried_without_changing_its_idempotence_key(
    seeded_client: TestClient,
) -> None:
    """Relancer incrémente la tentative, et ne touche à AUCUNE des trois versions.

    Relancer en changeant un numéro de version créerait une SECONDE ligne : la
    clé d'idempotence est (organisation, révision, étape, pipeline, prompt,
    modèle). L'historique des tentatives se perdrait, et l'on ne saurait plus
    qu'une première exécution a échoué — c'est-à-dire précisément ce qu'on
    cherche à savoir.

    La relance doit aussi être EXPLICITE : sans `relancer=True`, une étape en
    échec reste en échec, parce que relancer automatiquement une étape qui
    vient d'échouer est une boucle, pas une reprise.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-RELANCE")

    def travail_en_echec(
        _session: Session, _revision: DocumentRevision, _chemin: Path
    ) -> str | None:
        return "provider_unavailable"

    premier = _executer(depot, travail=travail_en_echec)
    assert premier.statut == "failed"
    (avant,) = _etapes(depot)
    versions = (avant.pipeline_version, avant.prompt_version, avant.model_version)

    with pytest.raises(travail_documentaire.TravailRefuse) as refus:
        _executer(depot, travail=_rien)
    assert refus.value.code == "etape_en_echec"

    reprise = _executer(depot, travail=_rien, relancer=True)
    assert reprise.statut == "succeeded"

    (apres,) = _etapes(depot)
    assert apres.id == avant.id, "une seconde ligne a été créée"
    assert apres.attempt == 2
    assert (apres.pipeline_version, apres.prompt_version, apres.model_version) == versions
    assert apres.status == "succeeded"
    assert apres.error_code is None


def test_a_step_outside_the_declared_list_is_refused(seeded_client: TestClient) -> None:
    """`claim_step_run` refuse AVANT la base, et il faut que ce soit vrai.

    La liste des étapes vit à trois endroits, et celui-ci est le seul que
    l'API traverse : une étape acceptée en SQL mais absente du service serait
    inatteignable, et une étape acceptée par le service mais absente de la
    contrainte ferait tomber la transaction loin de sa cause.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-ETAPE-INCONNUE")

    with pytest.raises(travail_documentaire.TravailRefuse) as refus:
        _executer(depot, step="dxf", travail=_rien)
    assert refus.value.code == "invalid_document_step"
    assert _etapes(depot) == [], "une étape inconnue a quand même laissé une ligne"


# ---------------------------------------------------------------------------
# Ce dans quoi le worker n'écrit pas
# ---------------------------------------------------------------------------


def test_the_worker_never_writes_into_the_immutable_revision(
    seeded_client: TestClient,
) -> None:
    """Une révision publiée est immuable PAR DÉCLENCHEUR, sur les deux moteurs.

    Le worker n'écrit que dans ses propres tables : l'état d'exécution et, le
    cas échéant, les propositions. S'il touchait la révision — ne serait-ce
    que pour y poser une date de traitement — le déclencheur ferait tomber sa
    transaction, et l'étape échouerait sans que le code en soit la cause.

    Les deux moitiés du test sont nécessaires. La première : l'étape réussit
    et la ligne de révision est inchangée, champ par champ. La seconde : une
    écriture délibérée est bien REFUSÉE — sans elle, le test passerait tout
    aussi bien sur une base où le déclencheur aurait disparu.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    depot = _deposer(seeded_client, admin, "TRAVAIL-IMMUABLE")

    session = get_session_factory()()
    try:
        avant = session.get(DocumentRevision, depot.revision_id)
        assert avant is not None
        assert avant.published_at is not None, "une révision déposée est publiée"
        temoin = (
            avant.storage_key,
            avant.sha256,
            avant.byte_size,
            avant.media_type,
            avant.original_filename,
            avant.status,
            avant.published_at,
            avant.updated_at,
        )
    finally:
        session.close()

    assert _executer(depot, travail=_rien).statut == "succeeded"

    session = get_session_factory()()
    try:
        apres = session.get(DocumentRevision, depot.revision_id)
        assert apres is not None
        assert (
            apres.storage_key,
            apres.sha256,
            apres.byte_size,
            apres.media_type,
            apres.original_filename,
            apres.status,
            apres.published_at,
            apres.updated_at,
        ) == temoin

        with pytest.raises(IntegrityError, match="published document revision is immutable"):
            session.execute(
                text("UPDATE document_revisions SET original_filename = :v WHERE id = :i"),
                {"v": "renommee-par-le-worker.dxf", "i": depot.revision_id},
            )
            session.commit()
        session.rollback()

        # Et ce que le worker écrit, lui, est bien là : une ligne d'étape.
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentStepRun)
                .where(DocumentStepRun.revision_id == depot.revision_id)
            )
            == 1
        )
    finally:
        session.close()
