"""Le parcours complet d'un PDF, par l'API : déposer, calibrer, mesurer, confirmer.

Tout passe par le client HTTP et par un vrai PDF déposé en multipart. Rien
n'est simulé : la détection de type, l'analyse, la calibration, le calcul et
les décisions humaines sont ceux de la production.

**Ce que ce fichier éprouve en priorité n'est pas la valeur mesurée** — elle
l'est dans `test_mesures_pdf.py`, sur des cas dont la réponse se vérifie de
tête. Ici, ce qui compte est l'enchaînement : qu'une mesure soit IMPOSSIBLE
avant calibration, qu'elle devienne possible après, qu'elle arrive avec sa
provenance, et qu'un humain puisse la confirmer ou la corriger sans que la
proposition machine soit réécrite.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from .conftest import login

pytest.importorskip(
    "pypdfium2",
    reason="l'extra « pdf » n'est pas installé : pip install './apps/api[pdf]'",
)

import fabriquer_pdf_de_test as pdf_fixtures

#: La page de la fixture fait **300 × 220 points PostScript**.
#:
#: Une calibration qui en couvre la moitié porte donc sur 150 points. Déclarée
#: à 7 500 mm, elle donne **50 mm par point**, et tout ce qui suit se vérifie
#: de tête. Le chiffre de 7 500 n'est pas rond par hasard : il est choisi pour
#: que le facteur le soit.
LARGEUR_EN_POINTS = 300.0
HAUTEUR_EN_POINTS = 220.0
DISTANCE_DE_CALIBRATION = "7500"

#: La tolérance des comparaisons de mesure.
#:
#: Les points traversent le repère de l'écran en flottants : un quart de page
#: ressort à 3 749,999999996 plutôt qu'à 3 750. Exiger l'égalité décimale
#: exacte éprouverait la représentation des flottants, pas la mesure — et le
#: module pur, lui, la rend exacte parce qu'il travaille déjà en coordonnées
#: de page (voir `test_mesures_pdf.py`).
#:
#: Un centième de millimètre est très en deçà de tout ce qui compte : le
#: plancher de pointage mesuré est de 0,6 mm sur une tuile agrandie.
TOLERANCE_EN_MM = Decimal("0.01")


def _deposer_un_pdf(client: TestClient, entetes: dict[str, str], reference: str):
    """Projet, document, révision d'un PDF, puis analyse. Rend les identifiants."""
    projet = client.post(
        "/api/v1/projects",
        headers=entetes,
        json={"reference": reference, "name": f"Chantier {reference}"},
    )
    assert projet.status_code == 201, projet.text
    document = client.post(
        f"/api/v1/projects/{projet.json()['id']}/documents",
        headers=entetes,
        json={"title": "Façade sud"},
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]

    revision = client.post(
        f"/api/v1/documents/{document_id}/revisions",
        headers=entetes,
        files={
            "file": (
                "facade.pdf",
                pdf_fixtures.page_avec_plusieurs_textes(),
                "application/pdf",
            )
        },
    )
    assert revision.status_code == 201, revision.text
    revision_id = revision.json()["id"]

    analyse = client.post(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/analyse",
        headers=entetes,
    )
    assert analyse.status_code == 200, analyse.text
    return document_id, revision_id


def _calibrer(
    client: TestClient,
    entetes: dict[str, str],
    document_id: str,
    revision_id: str,
    *,
    distance: str = DISTANCE_DE_CALIBRATION,
    unite: str = "mm",
    resolution: str = "0.05",
    zone: list[float] | None = None,
    premier: tuple[float, float] = (0.0, 0.5),
    second: tuple[float, float] = (0.5, 0.5),
):
    """Par défaut : la moitié de la largeur de la page, déclarée à 5 000 mm."""
    corps: dict = {
        "page": 1,
        "premier": {"x": premier[0], "y": premier[1]},
        "second": {"x": second[0], "y": second[1]},
        "distance_reelle": distance,
        "unite": unite,
        "resolution_du_pointage": resolution,
        "motif": "Cote 7500 de la façade sud",
    }
    if zone is not None:
        corps["zone"] = zone
    return client.post(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/calibration",
        headers=entetes,
        json=corps,
    )


def _mesurer(
    client: TestClient,
    entetes: dict[str, str],
    document_id: str,
    revision_id: str,
    *,
    type_de_mesure: str = "segment",
    points: list[tuple[float, float]] | None = None,
    libelle: str = "Mur nord",
):
    points = points or [(0.0, 0.5), (0.25, 0.5)]
    return client.post(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/mesures",
        headers=entetes,
        json={
            "page": 1,
            "type": type_de_mesure,
            "points": [{"x": x, "y": y} for x, y in points],
            "libelle": libelle,
        },
    )


def _relire(client: TestClient, entetes: dict[str, str], document_id: str, revision_id: str):
    return client.get(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/mesures",
        headers=entetes,
    )


# ---------------------------------------------------------------------------
# Sans échelle, rien
# ---------------------------------------------------------------------------


def test_measuring_before_calibrating_is_refused_and_says_what_to_do(
    seeded_client: TestClient,
) -> None:
    """**Le refus qui tient tout le reste.**

    Un PDF ne porte aucune unité. Mesurer avant d'avoir déclaré une échelle
    n'est pas une erreur de l'utilisateur à corriger plus tard : c'est une
    opération qui n'a pas de sens. Le refus doit donc arriver AVANT tout
    calcul, et dire ce qu'il faut faire — un refus qui ne l'indique pas est un
    cul-de-sac.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-SANS-ECHELLE")

    refus = _mesurer(seeded_client, admin, document, revision)

    assert refus.status_code == 422, refus.text
    detail = refus.json()["detail"]
    assert detail["code"] == "sans_calibration"
    assert "échelle" in detail["message"]
    assert "cote longue" in detail["message"], "le refus dit quoi faire"


def test_nothing_is_written_when_the_measurement_is_refused(
    seeded_client: TestClient,
) -> None:
    """Un refus ne laisse ni citation ni proposition derrière lui."""
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-RIEN-ECRIT")

    _mesurer(seeded_client, admin, document, revision)

    relecture = _relire(seeded_client, admin, document, revision)
    assert relecture.status_code == 200, relecture.text
    assert relecture.json()["mesures"] == []
    assert relecture.json()["calibrations"] == []


# ---------------------------------------------------------------------------
# Calibrer
# ---------------------------------------------------------------------------


def test_a_calibration_is_recorded_with_what_the_person_declared(
    seeded_client: TestClient,
) -> None:
    """Ce qui est conservé est la DÉCLARATION, pas seulement son résultat.

    La distance saisie, l'unité, le motif et la finesse du pointage : c'est
    cela qu'on relira dans six mois pour juger si l'échelle était bonne. Le
    facteur, lui, se recalcule.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-CALIBRE")

    reponse = _calibrer(seeded_client, admin, document, revision)

    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert corps["distance_reelle"] == DISTANCE_DE_CALIBRATION
    assert corps["unite"] == "mm"
    assert corps["motif"] == "Cote 7500 de la façade sud"
    # La moitié d'une page de 300 points, soit 150 points, pour 7 500 mm.
    assert corps["facteur_lisible"] == "50 mm par point"


def test_a_calibration_too_short_to_mean_anything_is_refused(
    seeded_client: TestClient,
) -> None:
    """Deux points voisins ne déterminent aucune échelle.

    Le refus vient de `mesures_pdf.facteur`, c'est-à-dire de la MÊME fonction
    qui refusera les mesures : une seule règle, à un seul endroit.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-COURTE")

    refus = _calibrer(
        seeded_client,
        admin,
        document,
        revision,
        premier=(0.10, 0.5),
        second=(0.12, 0.5),  # quatre points de page
    )

    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "calibration_trop_courte"


def test_an_unknown_unit_is_refused_by_name(seeded_client: TestClient) -> None:
    """« pouces » n'est pas dans la table des unités, et on le dit."""
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-UNITE")

    refus = _calibrer(seeded_client, admin, document, revision, unite="pouces")

    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "unknown_unit"


def test_an_area_unit_cannot_calibrate_a_distance(seeded_client: TestClient) -> None:
    """Une calibration déclare une DISTANCE entre deux points."""
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-UNITE-2")

    refus = _calibrer(seeded_client, admin, document, revision, unite="m2")

    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "unite_non_lineaire"


def test_a_dxf_cannot_be_calibrated(seeded_client: TestClient, tmp_path) -> None:
    """Un DXF porte son unité : lui déclarer une échelle serait l'écraser.

    C'est la différence de fond entre les deux formats, et elle doit se dire —
    sans quoi un utilisateur calibrerait un DXF et croirait avoir amélioré
    quelque chose.
    """
    ezdxf = pytest.importorskip("ezdxf")
    admin = login(seeded_client, "admin@dubois.demo")

    projet = seeded_client.post(
        "/api/v1/projects",
        headers=admin,
        json={"reference": "DXF-CALIBRE", "name": "Chantier DXF"},
    )
    document = seeded_client.post(
        f"/api/v1/projects/{projet.json()['id']}/documents",
        headers=admin,
        json={"title": "Plan DXF"},
    )
    document_id = document.json()["id"]

    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line((0, 0), (5000, 0))
    chemin = tmp_path / "plan.dxf"
    doc.saveas(chemin)

    revision = seeded_client.post(
        f"/api/v1/documents/{document_id}/revisions",
        headers=admin,
        files={"file": ("plan.dxf", chemin.read_bytes(), "image/vnd.dxf")},
    )
    revision_id = revision.json()["id"]
    seeded_client.post(
        f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/analyse",
        headers=admin,
    )

    refus = _calibrer(seeded_client, admin, document_id, revision_id)
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "pas_un_pdf"


# ---------------------------------------------------------------------------
# Mesurer
# ---------------------------------------------------------------------------


def test_a_segment_is_measured_with_its_provenance(seeded_client: TestClient) -> None:
    """Le parcours nominal : calibrer, puis mesurer, et tout retrouver.

    La valeur se vérifie de tête : la calibration couvre la moitié de la page
    pour 7 500 mm, le segment en couvre le quart, donc 3 750 mm.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-SEGMENT")
    _calibrer(seeded_client, admin, document, revision)

    reponse = _mesurer(seeded_client, admin, document, revision)

    assert reponse.status_code == 201, reponse.text
    mesure = reponse.json()
    assert abs(Decimal(mesure["valeur"]) - Decimal("3750")) < TOLERANCE_EN_MM
    assert mesure["unite"] == "mm"
    assert mesure["libelle"] == "Mur nord"

    # La provenance : d'où vient ce nombre.
    assert mesure["calibration"]["distance_reelle"] == DISTANCE_DE_CALIBRATION
    assert mesure["calibration"]["motif"] == "Cote 7500 de la façade sud"
    assert Decimal(mesure["incertitude"]) > 0, "aucune mesure n'est exacte"

    # Et de quoi la redessiner sur l'aperçu.
    assert len(mesure["points"]) == 2
    assert mesure["cadre"] is not None
    assert mesure["page"] == 1


def test_a_closed_surface_is_measured_in_square_metres(
    seeded_client: TestClient,
) -> None:
    """Une surface sort en m², quelle que soit l'unité de la calibration.

    Le dépôt ne connaît pas de millimètre carré — `units.py` déclare `m2`,
    `cm2` et `ha`. Le mètre carré est l'unité canonique de la dimension, et
    c'est celle dans laquelle un métré de bâtiment se lit.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-SURFACE")
    _calibrer(seeded_client, admin, document, revision)

    reponse = _mesurer(
        seeded_client,
        admin,
        document,
        revision,
        type_de_mesure="surface",
        points=[(0.0, 0.0), (0.25, 0.0), (0.25, 0.5), (0.0, 0.5)],
        libelle="Dalle du séjour",
    )

    assert reponse.status_code == 201, reponse.text
    mesure = reponse.json()
    assert mesure["unite"] == "m2"
    # Un quart de la largeur (75 pt) sur la moitié de la hauteur (110 pt), à
    # 50 mm le point : 3 750 × 5 500 mm, soit 20,625 m².
    assert abs(Decimal(mesure["valeur"]) - Decimal("20.625")) < Decimal("0.001")


def test_a_coarse_pointing_keeps_the_measurement_to_be_verified(
    seeded_client: TestClient,
) -> None:
    """Pointé sur l'aperçu pleine page, le nombre est conservé et réservé.

    Mesuré : un pixel de l'aperçu d'un A0 vaut 12 à 42 mm d'ouvrage. Une
    mesure posée ainsi n'est pas fausse — elle est mal déterminée, et l'écran
    doit le dire plutôt que de l'afficher comme les autres.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-GROSSIER")
    _calibrer(seeded_client, admin, document, revision, resolution="1.0")

    reponse = _mesurer(
        seeded_client,
        admin,
        document,
        revision,
        points=[(0.40, 0.5), (0.50, 0.5)],
    )

    mesure = reponse.json()
    assert Decimal(mesure["valeur"]) > 0, "la mesure est conservée"
    assert mesure["fiabilite"] == "a_confirmer"
    assert "incertitude_elevee" in mesure["reserves"]


def test_a_measurement_outside_the_calibrated_zone_is_refused(
    seeded_client: TestClient,
) -> None:
    """Une page porte souvent deux échelles — un plan et un détail.

    Appliquer celle du plan au détail donnerait une mesure deux fois et demie
    trop grande, et parfaitement plausible. La calibration de zone existe pour
    cela, et une mesure qui en sort n'est pas à moitié juste.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-ZONE")
    # Une échelle déclarée pour la moitié GAUCHE de la page seulement.
    reponse = _calibrer(seeded_client, admin, document, revision, zone=[0.0, 0.0, 0.5, 1.0])
    assert reponse.status_code == 201, reponse.text

    dedans = _mesurer(seeded_client, admin, document, revision, points=[(0.1, 0.5), (0.3, 0.5)])
    assert dedans.status_code == 201, dedans.text

    dehors = _mesurer(seeded_client, admin, document, revision, points=[(0.1, 0.5), (0.9, 0.5)])
    assert dehors.status_code == 422, dehors.text
    assert dehors.json()["detail"]["code"] == "sans_calibration"


# ---------------------------------------------------------------------------
# La validation humaine
# ---------------------------------------------------------------------------


def test_a_human_can_correct_a_measurement_without_rewriting_the_machine_one(
    seeded_client: TestClient,
) -> None:
    """**L'invariant qui protège le travail d'une personne.**

    La proposition machine n'est jamais réécrite : la correction vit dans une
    décision à part. L'écran peut donc montrer les deux, et l'écart entre
    elles — ce qu'une mise à jour en place rendrait impossible.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-CORRIGE")
    _calibrer(seeded_client, admin, document, revision)
    mesure = _mesurer(seeded_client, admin, document, revision).json()

    decision = seeded_client.post(
        f"/api/v1/extraction-proposals/{mesure['proposal_id']}/decisions",
        headers=admin,
        json={
            "decision": "corrected",
            "reason": "Relevé sur le plan papier : 3 720 mm",
            "before_value": {"valeur": mesure["valeur"]},
            "after_value": {"valeur": "3720"},
        },
    )
    assert decision.status_code == 201, decision.text

    relecture = _relire(seeded_client, admin, document, revision).json()
    (relue,) = relecture["mesures"]

    assert relue["valeur"] == mesure["valeur"], "la proposition machine n'a pas bougé"
    assert relue["decision"] == "corrected"
    assert relue["valeur_corrigee"] == "3720"


def test_a_confirmed_measurement_shows_its_decision(seeded_client: TestClient) -> None:
    """Confirmer laisse une trace, et l'écran la relit."""
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-CONFIRME")
    _calibrer(seeded_client, admin, document, revision)
    mesure = _mesurer(seeded_client, admin, document, revision).json()

    seeded_client.post(
        f"/api/v1/extraction-proposals/{mesure['proposal_id']}/decisions",
        headers=admin,
        json={"decision": "accepted", "reason": "Vérifié sur la tuile agrandie"},
    )

    (relue,) = _relire(seeded_client, admin, document, revision).json()["mesures"]
    assert relue["decision"] == "accepted"
    assert relue["valeur_corrigee"] is None


# ---------------------------------------------------------------------------
# La tuile
# ---------------------------------------------------------------------------


def test_a_tile_is_served_and_the_second_request_comes_from_the_cache(
    seeded_client: TestClient,
) -> None:
    """La première demande rend, les suivantes relisent.

    Mesuré : 0,2 à 5,3 secondes au premier appel — le chargement de la page —
    et 0,3 milliseconde ensuite. L'en-tête le dit, pour que le diagnostic d'une
    lenteur ne demande pas de deviner.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-TUILE")

    chemin = f"/api/v1/documents/{document}/revisions/{revision}/plan/tuile"
    parametres = {"page": 1, "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.3}

    premiere = seeded_client.get(chemin, headers=admin, params=parametres)
    assert premiere.status_code == 200, premiere.text
    assert premiere.headers["content-type"] == "image/png"
    assert premiere.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert premiere.headers["x-metreo-tuile"] == "rendue"

    seconde = seeded_client.get(chemin, headers=admin, params=parametres)
    assert seconde.status_code == 200, seconde.text
    assert seconde.headers["x-metreo-tuile"] == "cache"
    assert seconde.content == premiere.content


def test_a_flat_zone_is_refused_rather_than_rendered(
    seeded_client: TestClient,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document, revision = _deposer_un_pdf(seeded_client, admin, "PDF-TUILE-PLATE")

    refus = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/plan/tuile",
        headers=admin,
        params={"page": 1, "x0": 0.5, "y0": 0.1, "x1": 0.5, "y1": 0.3},
    )
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "zone_invalide"
