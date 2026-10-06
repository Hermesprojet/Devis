"""Le parcours complet d'un plan, par l'API : déposer, analyser, voir, corriger.

Tout passe par le client HTTP et par un VRAI DXF déposé en multipart, comme
`test_documents_api.py` dépose un original. Rien n'est simulé : la détection
de type, les deux étapes de pipeline, les artefacts posés sur le volume et les
propositions écrites en base sont ceux de la production.

Le fichier est fabriqué avec ezdxf dans le `tmp_path` du test. Il porte
exprès un nom de calque et un texte de cotation reconnaissables — un plan
réel nomme ses calques d'après le chantier, et ces deux chaînes servent au
dernier test à prouver qu'elles ne finissent pas dans le journal.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from metreo_api import config
from metreo_api.db import get_session_factory
from metreo_api.models import DocumentRevision, ExtractionProposal, SourceCitation

from .conftest import login
from .test_journaux import _Capture

ezdxf = pytest.importorskip(
    "ezdxf",
    reason="l'extra « plans » n'est pas installé : pip install './apps/api[plans]'",
)


@pytest.fixture()
def journal() -> Iterator[_Capture]:
    """Le journal de production, capté au moment de l'ÉMISSION.

    `_Capture` est partagée avec `test_journaux.py` : c'est elle qui porte la
    substance — le vrai `JsonFormatter`, appelé pendant la requête, parce que
    `request_id` vient d'une variable de contexte remise à sa valeur par
    défaut dès la requête terminée. La fixture, en revanche, est déclarée ici
    plutôt qu'importée : un nom de fixture importé puis reçu en paramètre est
    une redéfinition que le lint refuse, à juste titre.
    """
    handler = _Capture()
    logger = logging.getLogger("metreo.api")
    niveau = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(niveau)


#: Du contenu documentaire, au sens strict : **un nom de calque est du contenu**
#: — un plan réel les nomme d'après le chantier, le lot ou le client.
CALQUE = "CHANTIER-DUBOIS-NIVEAU-2"
TEXTE_DE_COTATION = "7777 confidentiel"
NOM_DEPOSE = "plan-rue-du-client-42.dxf"

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer\n%%EOF\n"


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


def _dxf(
    chemin: Path,
    *,
    insunits: int | None = 4,
    texte: str | None = None,
    longueur: float = 5000.0,
) -> bytes:
    """Un DXF portant des murs et UNE cotation linéaire, rendu en octets.

    `insunits=None` laisse l'en-tête sans la variable : c'est ce qu'un export
    d'ancien logiciel produit, et c'est le cas du plan consultable mais non
    mesurable.
    """
    doc = ezdxf.new("R2013", setup=True)
    if insunits is None:
        del doc.header["$INSUNITS"]
    else:
        doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_line((0, 0), (longueur, 0), dxfattribs={"layer": CALQUE})
    msp.add_line((0, 0), (0, 3000), dxfattribs={"layer": CALQUE})
    cote = msp.add_linear_dim(
        base=(0, -1000), p1=(0, 0), p2=(longueur, 0), dxfattribs={"layer": CALQUE}
    )
    # `render()` n'est pas décoratif : sans lui la cotation n'a pas de bloc
    # géométrique, l'audit la retire au rechargement, et l'espace modèle
    # revient VIDE.
    cote.render()
    if texte is not None:
        cote.dimension.dxf.text = texte
    doc.saveas(chemin)
    return chemin.read_bytes()


def _projet(client: TestClient, entetes: dict[str, str], reference: str) -> str:
    reponse = client.post(
        "/api/v1/projects",
        headers=entetes,
        json={"reference": reference, "name": "Chantier avec plans"},
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _document(client: TestClient, entetes: dict[str, str], projet: str) -> str:
    reponse = client.post(
        f"/api/v1/projects/{projet}/documents",
        headers=entetes,
        json={"title": "Plan d'exécution lot 3"},
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _deposer(
    client: TestClient,
    entetes: dict[str, str],
    document: str,
    contenu: bytes,
    *,
    nom: str = NOM_DEPOSE,
    type_annonce: str = "image/vnd.dxf",
) -> str:
    reponse = client.post(
        f"/api/v1/documents/{document}/revisions",
        headers=entetes,
        files={"file": (nom, contenu, type_annonce)},
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _plan_depose(
    client: TestClient,
    entetes: dict[str, str],
    reference: str,
    *,
    contenu: bytes,
    nom: str = NOM_DEPOSE,
    type_annonce: str = "image/vnd.dxf",
) -> tuple[str, str]:
    """Projet, document, révision : rend `(document_id, revision_id)`."""
    document = _document(client, entetes, _projet(client, entetes, reference))
    revision = _deposer(client, entetes, document, contenu, nom=nom, type_annonce=type_annonce)
    return document, revision


def _analyser(client: TestClient, entetes: dict[str, str], document: str, revision: str):
    return client.post(
        f"/api/v1/documents/{document}/revisions/{revision}/plan/analyse",
        headers=entetes,
    )


def _relire(client: TestClient, entetes: dict[str, str], document: str, revision: str):
    return client.get(f"/api/v1/documents/{document}/revisions/{revision}/plan", headers=entetes)


def _image(client: TestClient, entetes: dict[str, str], document: str, revision: str):
    return client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/plan/image", headers=entetes
    )


def _compter_propositions() -> int:
    session = get_session_factory()()
    try:
        return int(session.scalar(select(func.count()).select_from(ExtractionProposal)) or 0)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Le chemin nominal
# ---------------------------------------------------------------------------


def test_reading_a_plan_proposes_measurements_and_renders_an_image(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """Le parcours entier en une requête : constat, mesures, image.

    Les deux étapes sont jouées dans cet ordre — `cad_read` puis
    `page_render` — et chacune laisse son état séparément. Ce test fixe
    qu'une analyse réussie rend les TROIS choses dont l'écran a besoin :
    l'unité lue dans le fichier, au moins une mesure proposée, et une image
    réellement servable.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-OK", contenu=_dxf(tmp_path / "ok.dxf")
    )

    analyse = _analyser(seeded_client, admin, document, revision)
    assert analyse.status_code == 200, analyse.text
    corps = analyse.json()

    assert corps["revision_id"] == revision
    assert corps["refuse"] is False
    assert corps["mesurable"] is True
    assert corps["unite_source"] == "mm"
    assert corps["insunits"] == 4
    assert len(corps["mesures"]) >= 1
    assert corps["image_disponible"] is True
    assert CALQUE in corps["calques"]

    image = _image(seeded_client, admin, document, revision)
    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == "image/svg+xml"
    assert image.text.lstrip().startswith(("<?xml", "<svg"))

    # La politique de sécurité interdit le script : un SVG atteint directement
    # par son URL vit dans l'origine de l'application, là où est le jeton.
    politique = image.headers["content-security-policy"]
    assert "default-src 'none'" in politique
    assert "script-src" not in politique, "aucune source de script n'est autorisée"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in image.text.lower()


def test_a_plan_without_a_declared_unit_is_still_viewable_but_proposes_nothing(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """L'unité absente n'est PAS un échec d'étape, et ce n'en est pas un ici.

    Refuser l'analyse priverait l'utilisateur de la VISUALISATION, qui n'a
    jamais eu besoin d'unité : un plan sans `$INSUNITS` se regarde, s'archive
    et se télécharge parfaitement. Ce qui est interdit est d'en tirer une
    quantité, parce qu'un 5000 peut alors valoir 5 mètres comme 5 kilomètres.

    L'analyse réussit donc, l'anomalie est NOMMÉE dans la réponse — un écran
    ne peut pas deviner la cause d'une liste vide — et l'image est là.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client,
        admin,
        "PLAN-SANS-UNITE",
        contenu=_dxf(tmp_path / "sans-unite.dxf", insunits=None),
        nom="sans-unites.dxf",
    )

    analyse = _analyser(seeded_client, admin, document, revision)
    assert analyse.status_code == 200, analyse.text
    corps = analyse.json()

    assert corps["refuse"] is False, "le fichier n'est pas refusé : il est illisible en MESURE"
    assert corps["mesurable"] is False
    assert corps["unite_source"] is None
    assert corps["mesures"] == []
    assert "unite_absente" in [a["code"] for a in corps["anomalies"]]
    assert corps["image_disponible"] is True

    assert _image(seeded_client, admin, document, revision).status_code == 200


def test_the_proposed_measurement_keeps_the_document_unit_and_no_converted_value(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """**Aucune conversion à l'extraction**, pour quatre raisons énumérées.

    La docstring d'`ExtractionProposal` les écrit : l'unité cible n'est pas
    connue à l'extraction, la conversion n'est pas toujours possible — le
    domaine ne connaît ni `in` ni `ft` —, elle n'est pas réversible au chiffre
    près, et elle détruirait la provenance, puisque le nombre stocké doit être
    celui qu'on retrouve en rouvrant l'objet cité.

    La mesure est donc rendue en CHAÎNE décimale, avec l'unité du document, et
    la réponse ne porte AUCUNE clé de valeur convertie. Ce test liste les
    noms interdits plutôt que de se contenter de vérifier ceux qui sont là :
    une clé ajoutée par confort serait une conversion silencieuse.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-UNITE", contenu=_dxf(tmp_path / "unite.dxf")
    )
    analyse = _analyser(seeded_client, admin, document, revision)
    assert analyse.status_code == 200, analyse.text

    (mesure,) = analyse.json()["mesures"]
    assert isinstance(mesure["valeur_document"], str), "un flottant perdrait des chiffres"
    assert mesure["unite_document"] == "mm"
    assert mesure["valeur_document"].startswith("5000")
    assert mesure["famille"] == "lineaire"
    assert mesure["fiabilite"] == "mesurable"
    assert mesure["origine_de_la_mesure"] in ("cote_42", "recalcul")
    assert mesure["calque"] == CALQUE
    assert mesure["object_ref"]

    interdites = {
        "valeur",
        "valeur_convertie",
        "valeur_si",
        "unite",
        "unite_cible",
        "quantity",
        "quantite",
        "valeur_m",
        "valeur_mm",
    }
    assert interdites.isdisjoint(mesure), f"clé de conversion rendue : {interdites & set(mesure)}"

    # Et la même exigence DANS la base : une conversion rangée dans `value`
    # serait invisible de l'API mais reprise par le prochain lecteur.
    session = get_session_factory()()
    try:
        proposition = session.get(ExtractionProposal, mesure["proposal_id"])
        assert proposition is not None
        assert proposition.schema_name == "mesure_de_plan"
        assert proposition.schema_version == "1"
        assert proposition.model_version == "aucun-modele"
        assert proposition.prompt_version == "none"
        assert interdites.isdisjoint(proposition.value)
        assert proposition.value["unite_document"] == "mm"
        assert proposition.value["insunits"] == 4
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Les refus, chacun par son nom
# ---------------------------------------------------------------------------


def test_a_pdf_revision_is_refused_by_name_and_not_silently_ignored(
    seeded_client: TestClient,
) -> None:
    """Le PDF est accepté au DÉPÔT et reste téléchargeable : la lecture, non.

    Dire « plan » en acceptant un PDF ici laisserait croire qu'il va être
    mesuré, et l'utilisateur attendrait des mesures qui ne viendraient jamais.
    Le refus nomme le code ET rappelle ce qui reste possible — sans quoi il
    ressemblerait à la perte du fichier.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client,
        admin,
        "PLAN-PDF",
        contenu=PDF,
        nom="cctp.pdf",
        type_annonce="application/pdf",
    )

    refus = _analyser(seeded_client, admin, document, revision)
    assert refus.status_code == 422, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "type_non_lisible"
    assert "téléchargeable" in detail["message"]


def test_a_plan_never_analysed_answers_that_it_was_never_analysed(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """« Jamais analysé » est un ÉTAT, pas une erreur, et il se dit.

    L'écran doit pouvoir proposer le bouton « analyser » au lieu d'afficher
    une panne. Le code `plan_non_analyse` est ce qui l'autorise.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-VIERGE", contenu=_dxf(tmp_path / "vierge.dxf")
    )

    relecture = _relire(seeded_client, admin, document, revision)
    assert relecture.status_code == 404, relecture.text
    assert relecture.json()["detail"]["code"] == "plan_non_analyse"


def test_an_image_asked_before_any_analysis_is_not_confused_with_a_missing_revision(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """409 et NON 404, et la différence est une question de cloisonnement.

    Un 404 sur cette route se confondrait avec le refus d'une révision d'un
    AUTRE tenant, qui doit rester indiscernable d'une révision inexistante. Le
    même code pour les deux cas rendrait les deux situations équivalentes à
    l'écran, et surtout il offrirait à un tiers un moyen de distinguer « cette
    révision n'existe pas » de « elle existe, chez quelqu'un d'autre, et son
    image n'est pas prête » — c'est-à-dire de constater son existence.

    Le test vérifie donc les deux codes dans la même fonction : c'est leur
    DIFFÉRENCE qui porte la propriété, pas l'un des deux.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-IMAGE-ABSENTE", contenu=_dxf(tmp_path / "pas-encore.dxf")
    )

    sans_image = _image(seeded_client, admin, document, revision)
    assert sans_image.status_code == 409, sans_image.text
    assert sans_image.json()["detail"]["code"] == "image_de_plan_absente"

    etranger = login(seeded_client, "admin@janssens.demo")
    chez_un_autre = _image(seeded_client, etranger, document, revision)
    assert chez_un_autre.status_code == 404
    assert chez_un_autre.status_code != sans_image.status_code


def test_a_plan_of_another_organisation_is_never_read(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """Les trois routes rendent 404, et une révision inexistante aussi.

    Le même code pour les deux : c'est la règle du dépôt — une ressource d'un
    autre tenant est indiscernable d'une ressource qui n'existe pas. Le test
    compare les deux réponses au lieu de se contenter du nombre 404, parce
    qu'un message différent suffirait à trahir l'existence.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    etranger = login(seeded_client, "admin@janssens.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-TENANT", contenu=_dxf(tmp_path / "tenant.dxf")
    )
    assert _analyser(seeded_client, admin, document, revision).status_code == 200

    inexistante = "00000000-0000-4000-8000-000000000000"
    for appel in (_analyser, _relire, _image):
        etrangere = appel(seeded_client, etranger, document, revision)
        fantome = appel(seeded_client, etranger, document, inexistante)
        assert etrangere.status_code == 404, (appel.__name__, etrangere.text)
        assert fantome.status_code == 404, (appel.__name__, fantome.text)
        assert etrangere.json() == fantome.json(), (
            f"{appel.__name__} distingue une révision étrangère d'une révision inexistante"
        )
        assert CALQUE not in etrangere.text
        assert NOM_DEPOSE not in etrangere.text


def test_a_plan_heavier_than_the_synchronous_ceiling_is_refused_by_name(
    seeded_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le plafond est ABAISSÉ, au lieu de fabriquer douze mébioctets.

    Ce qui est éprouvé est la borne et son nom, pas sa valeur : un DXF de
    douze mébioctets coûterait des secondes de fabrication et autant de
    lecture, pour prouver exactement la même comparaison. Le réglage existe
    précisément pour qu'un exploitant puisse le changer.

    Le message doit nommer la sortie — le traitement déporté — sinon le refus
    ressemble à un plafond arbitraire sans recours.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-LOURD", contenu=_dxf(tmp_path / "lourd.dxf")
    )

    monkeypatch.setenv("METREO_PLAN_SYNC_MAX_BYTES", "1024")
    config.get_settings.cache_clear()

    refus = _analyser(seeded_client, admin, document, revision)
    assert refus.status_code == 413, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "plan_trop_lourd"
    assert "déporté" in detail["message"]
    # Rien n'a été analysé : le refus tombe AVANT la première étape.
    assert _relire(seeded_client, admin, document, revision).status_code == 404


# ---------------------------------------------------------------------------
# Ce qu'une relecture ne doit pas défaire
# ---------------------------------------------------------------------------


def test_reading_the_same_plan_twice_does_not_duplicate_its_proposals(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """**L'invariant qui protège une décision humaine d'une relecture.**

    Une proposition porte peut-être une acceptation ou une correction. La
    recréer à l'identique sous un NOUVEL identifiant laisserait la décision
    rattachée à une ligne que plus personne ne lit : le travail de la
    personne serait toujours en base, et invisible.

    Le test compare les identifiants et pas seulement le compte : deux
    propositions créées et deux supprimées garderaient le compte stable.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-DEUX-FOIS", contenu=_dxf(tmp_path / "deux-fois.dxf")
    )

    premiere = _analyser(seeded_client, admin, document, revision)
    assert premiere.status_code == 200, premiere.text
    identifiants = sorted(m["proposal_id"] for m in premiere.json()["mesures"])
    assert identifiants, "rien à protéger si rien n'a été proposé"
    compte = _compter_propositions()

    seconde = _analyser(seeded_client, admin, document, revision)
    assert seconde.status_code == 200, seconde.text

    assert sorted(m["proposal_id"] for m in seconde.json()["mesures"]) == identifiants
    assert _compter_propositions() == compte

    session = get_session_factory()()
    try:
        citations = int(session.scalar(select(func.count()).select_from(SourceCitation)) or 0)
    finally:
        session.close()
    assert citations == len(identifiants), "une citation par mesure, pas deux"


def test_a_human_decision_is_recorded_without_rewriting_the_machine_proposal(
    seeded_client: TestClient, tmp_path: Path
) -> None:
    """La proposition machine n'est JAMAIS réécrite, ici comme sur le socle.

    Deux tests du dépôt fixent déjà cet invariant côté documents de texte ;
    celui-ci le prouve côté plan, c'est-à-dire sur le chemin où la valeur
    corrigée doit redescendre jusqu'à l'écran. Les deux sont rendues — la
    mesure du fichier et ce que l'humain a retenu — parce que l'écart est
    précisément ce qu'un métreur doit pouvoir regarder.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client, admin, "PLAN-CORRIGE", contenu=_dxf(tmp_path / "corrige.dxf")
    )
    analyse = _analyser(seeded_client, admin, document, revision)
    assert analyse.status_code == 200, analyse.text
    (mesure,) = analyse.json()["mesures"]
    valeur_machine = mesure["valeur_document"]

    decision = seeded_client.post(
        f"/api/v1/extraction-proposals/{mesure['proposal_id']}/decisions",
        headers=admin,
        json={
            "decision": "corrected",
            "reason": "Relevé sur place : la cote du plan est périmée.",
            "before_value": {"valeur_document": valeur_machine},
            "after_value": {"valeur_document": "4850"},
        },
    )
    assert decision.status_code == 201, decision.text

    relecture = _relire(seeded_client, admin, document, revision)
    assert relecture.status_code == 200, relecture.text
    (relue,) = relecture.json()["mesures"]

    assert relue["proposal_id"] == mesure["proposal_id"]
    assert relue["valeur_document"] == valeur_machine, "la proposition machine a été réécrite"
    assert relue["decision"] == "corrected"
    assert relue["valeur_corrigee"] == "4850"

    session = get_session_factory()()
    try:
        proposition = session.get(ExtractionProposal, mesure["proposal_id"])
        assert proposition is not None
        assert proposition.value["valeur_document"] == valeur_machine
        assert proposition.status == "proposed", "le statut machine reste celui de la machine"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Le journal
# ---------------------------------------------------------------------------


def test_reading_a_plan_writes_no_document_content_in_the_journal(
    seeded_client: TestClient, tmp_path: Path, journal: _Capture
) -> None:
    """Un journal est lu par des gens sans accès aux données, et expédié ailleurs.

    **Un nom de calque est du contenu documentaire** : un plan réel les nomme
    d'après le chantier, le lot ou le client — `CHANTIER-DUBOIS-NIVEAU-2` dans
    cette fixture. Un texte de cotation l'est aussi, puisqu'un dessinateur y
    écrit ce qu'il veut.

    Ce qui a le droit d'y figurer : des identifiants, des compteurs, des codes
    et des durées. Ce qui n'y entre jamais : la clé de stockage, le chemin
    absolu du fichier, le nom d'origine, un nom de calque, un texte de
    cotation.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _plan_depose(
        seeded_client,
        admin,
        "PLAN-JOURNAL",
        contenu=_dxf(tmp_path / "journal.dxf", texte=TEXTE_DE_COTATION),
    )

    session = get_session_factory()()
    try:
        enregistrement = session.get(DocumentRevision, revision)
        assert enregistrement is not None
        cle_de_stockage = enregistrement.storage_key
        nom_original = enregistrement.original_filename
    finally:
        session.close()

    # Le journal n'est écouté qu'à partir d'ici : le dépôt a sa propre
    # promesse, déjà tenue par `test_journaux.py`.
    journal.lignes.clear()
    analyse = _analyser(seeded_client, admin, document, revision)
    assert analyse.status_code == 200, analyse.text
    assert analyse.json()["mesurable"] is True

    assert journal.lignes, "l'analyse n'a rien journalisé : ce test ne prouverait rien"
    sortie = "\n".join(json.dumps(ligne, ensure_ascii=False) for ligne in journal.lignes)

    for interdit in (
        cle_de_stockage,
        nom_original,
        NOM_DEPOSE,
        CALQUE,
        TEXTE_DE_COTATION,
        str(tmp_path),
        "rendus-de-plan",
        ".dxf",
        ".svg",
    ):
        assert interdit not in sortie, f"le journal porte « {interdit} »"

    # La racine de stockage ne doit pas non plus s'y trouver : un chemin
    # absolu renseigne sur la machine autant que sur le document.
    racine = config.get_settings().storage_root
    assert racine not in sortie

    # Et le journal reste UTILE : l'étape et son issue y sont, par leur code.
    etapes = [
        ligne for ligne in journal.lignes if ligne["message"] == "etape_documentaire_terminee"
    ]
    assert {ligne["step"] for ligne in etapes} == {"cad_read", "page_render"}
    assert {ligne["statut"] for ligne in etapes} == {"succeeded"}
