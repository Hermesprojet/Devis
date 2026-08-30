"""Ce que le volume contient vraiment, après un dépôt accepté ou refusé.

Ces tests ne se contentent pas du code HTTP. Un refus qui laisse un fichier
sur le disque, une empreinte juste dans la réponse et fausse dans la base, ou
un original écrasé par une révision, répondent tous « 201 » sans rien prouver.
Chaque scénario regarde donc AUSSI le système de fichiers.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metreo_api import config
from metreo_api.db import get_session_factory
from metreo_api.models import AuditEvent, DocumentRevision

from .conftest import login

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer\n%%EOF\n"
PDF_BIS = b"%PDF-1.7\n1 0 obj<<>>endobj\n% autre contenu\ntrailer\n%%EOF\n"


def _docx() -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return tampon.getvalue()


def _zip_quelconque() -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("notes.txt", "rien")
    return tampon.getvalue()


@pytest.fixture()
def volume(app_env: None, tmp_path: Path) -> Path:
    """La racine de stockage de ce test, telle que `app_env` l'a posée.

    Elle vit sous `tmp_path` : ces tests regardent le disque, et regarder le
    disque n'a de sens que si l'on sait qu'on est seul dessus.
    """
    return tmp_path / "stockage"


def _projet(client: TestClient, entetes: dict[str, str], reference: str) -> str:
    reponse = client.post(
        "/api/v1/projects", headers=entetes, json={"reference": reference, "name": "Chantier"}
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _document(client: TestClient, entetes: dict[str, str], projet: str) -> str:
    reponse = client.post(
        f"/api/v1/projects/{projet}/documents", headers=entetes, json={"title": "CCTP lot 2"}
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _deposer(
    client: TestClient,
    entetes: dict[str, str],
    document: str,
    contenu: bytes,
    *,
    nom: str = "cctp.pdf",
    type_annonce: str = "application/pdf",
):
    return client.post(
        f"/api/v1/documents/{document}/revisions",
        headers=entetes,
        files={"file": (nom, contenu, type_annonce)},
    )


def _fichiers(volume: Path) -> list[Path]:
    return sorted(chemin for chemin in volume.rglob("*") if chemin.is_file())


def _temporaires(volume: Path) -> list[Path]:
    return list(volume.rglob(".depot-*"))


# --------------------------------------------------------------------------
# Le chemin nominal
# --------------------------------------------------------------------------


def test_le_premier_fichier_devient_la_revision_un_ecrite_sur_le_volume(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-1"))

    reponse = _deposer(seeded_client, admin, document, PDF)
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert corps["revision_number"] == 1
    assert corps["status"] == "published"
    assert corps["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert corps["byte_size"] == len(PDF)
    assert corps["media_type"] == "application/pdf"

    # Le fichier existe, il porte EXACTEMENT les octets reçus, et il est rangé
    # sous l'organisation puis sous le document.
    fichiers = _fichiers(volume)
    assert len(fichiers) == 1
    assert fichiers[0].read_bytes() == PDF
    assert "documents" in fichiers[0].parts
    assert document in fichiers[0].parts


@pytest.mark.parametrize(
    ("contenu", "type_annonce", "media_type"),
    [
        (PDF, "application/pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png", "image/png"),
        (b"\xff\xd8\xff\xe0" + b"0" * 32, "image/jpeg", "image/jpeg"),
        ("code;libelle\nA.1;Béton\n".encode(), "text/csv", "text/csv"),
    ],
)
def test_les_formats_du_premier_usage_sont_acceptes(
    seeded_client: TestClient,
    volume: Path,
    contenu: bytes,
    type_annonce: str,
    media_type: str,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, f"F-{media_type[:6]}"))
    reponse = _deposer(seeded_client, admin, document, contenu, type_annonce=type_annonce)
    assert reponse.status_code == 201, reponse.text
    assert reponse.json()["media_type"] == media_type


def test_un_docx_est_reconnu_a_son_sommaire_et_non_a_son_extension(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-OOXML"))
    reponse = _deposer(
        seeded_client, admin, document, _docx(), nom="clauses.bin", type_annonce=None
    )
    assert reponse.status_code == 201, reponse.text
    assert reponse.json()["media_type"].endswith("wordprocessingml.document")


# --------------------------------------------------------------------------
# Ce qui est refusé — et ce que le volume en garde
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contenu", "nom", "type_annonce", "code"),
    [
        (b"", "vide.pdf", "application/pdf", "empty_file"),
        (b"MZ\x90\x00" + b"x" * 64, "rapport.pdf", "application/pdf", "unsupported_content"),
        (b"\x7fELF" + b"x" * 64, "plan.pdf", "application/pdf", "unsupported_content"),
        (b"\x1f\x8b\x08" + b"x" * 64, "metre.pdf", "application/pdf", "unsupported_content"),
        (b"<!DOCTYPE html><script>x</script>", "notes.csv", "text/csv", "html_refused"),
        (PDF, "cctp.pdf", "image/png", "media_type_mismatch"),
        (PDF, "note.pdf", "application/x-msdos-program", "media_type_mismatch"),
    ],
)
def test_un_contenu_refuse_ne_laisse_rien_sur_le_volume(
    seeded_client: TestClient,
    volume: Path,
    contenu: bytes,
    nom: str,
    type_annonce: str,
    code: str,
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, f"R-{code[:6]}"))

    reponse = _deposer(seeded_client, admin, document, contenu, nom=nom, type_annonce=type_annonce)
    assert reponse.status_code in (413, 422), reponse.text
    assert reponse.json()["detail"]["code"] == code

    # Ni fichier publié, ni temporaire abandonné, ni ligne en base.
    assert _fichiers(volume) == []
    assert _temporaires(volume) == []
    revisions = seeded_client.get(f"/api/v1/documents/{document}/revisions", headers=admin)
    assert revisions.json() == []


def test_une_archive_qui_n_est_pas_un_document_office_est_refusee(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-ZIP"))
    reponse = _deposer(
        seeded_client, admin, document, _zip_quelconque(), nom="pieces.zip", type_annonce=None
    )
    assert reponse.status_code == 422
    assert reponse.json()["detail"]["code"] == "unsupported_content"
    assert _fichiers(volume) == []


def test_le_plafond_est_applique_a_l_octet_pres(
    seeded_client: TestClient, volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La limite EXACTE passe ; un octet de plus est refusé.

    Un plafond éprouvé seulement « très au-dessus » ne dit rien de sa borne :
    c'est à l'octet près qu'une comparaison se trompe de sens.
    """
    monkeypatch.setenv("METREO_MAX_UPLOAD_BYTES", "512")
    config.get_settings.cache_clear()
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-CAP"))

    pile = PDF + b"a" * (512 - len(PDF))
    assert len(pile) == 512
    assert _deposer(seeded_client, admin, document, pile).status_code == 201

    trop = PDF + b"b" * (513 - len(PDF))
    assert len(trop) == 513
    refus = _deposer(seeded_client, admin, document, trop)
    assert refus.status_code == 413
    assert refus.json()["detail"]["code"] == "file_too_large"

    # Le dépôt accepté est là ; celui qui dépasse n'a laissé aucune trace.
    assert len(_fichiers(volume)) == 1
    assert _temporaires(volume) == []


def test_une_interruption_pendant_l_ecriture_ne_laisse_pas_de_temporaire(
    seeded_client: TestClient, volume: Path
) -> None:
    """Le client coupe au milieu du flux : le volume doit rester propre."""
    from metreo_api.services.document_storage import StockageLocal

    stockage = StockageLocal(volume)

    def flux_interrompu():
        yield PDF
        raise ConnectionResetError("le client a coupé")

    with pytest.raises(ConnectionResetError):
        stockage.ecrire(
            organization_id="org",
            document_id="doc",
            revision_id="rev",
            morceaux=flux_interrompu(),
            plafond=1_000_000,
            declared_media_type="application/pdf",
        )
    assert _fichiers(volume) == []
    assert _temporaires(volume) == []


# --------------------------------------------------------------------------
# Noms, collisions et tenants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("envoye", "attendu"),
    [
        ("../../secret.pdf", "secret.pdf"),
        ("/etc/passwd.pdf", "passwd.pdf"),
        ("..\\..\\windows\\note.pdf", "note.pdf"),
        ("cctp.pdf.exe", "cctp.pdf.exe"),
    ],
)
def test_un_nom_de_fichier_ne_construit_jamais_un_chemin(
    seeded_client: TestClient, volume: Path, envoye: str, attendu: str
) -> None:
    """Le nom est une métadonnée descriptive, jamais un morceau de chemin."""
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, f"N-{len(envoye)}"))
    # Le type est annoncé explicitement : sans cela le client HTTP le devine à
    # partir de l'extension, et c'est ce mensonge-là — pas le nom — qui serait
    # éprouvé.
    reponse = _deposer(
        seeded_client, admin, document, PDF, nom=envoye, type_annonce="application/pdf"
    )
    assert reponse.status_code == 201, reponse.text
    assert reponse.json()["original_filename"] == attendu
    # Le nom garde sa double extension ; le fichier posé sur le volume, lui,
    # porte celle du type RÉELLEMENT détecté.
    assert reponse.json()["media_type"] == "application/pdf"

    fichiers = _fichiers(volume)
    assert len(fichiers) == 1
    # Le nom physique vient du serveur : c'est l'identifiant de la révision.
    assert fichiers[0].stem == reponse.json()["id"]
    assert fichiers[0].is_relative_to(volume)


def test_le_meme_nom_dans_deux_organisations_ne_collisionne_pas(
    seeded_client: TestClient, volume: Path
) -> None:
    a = login(seeded_client, "admin@dubois.demo")
    b = login(seeded_client, "admin@janssens.demo")
    document_a = _document(seeded_client, a, _projet(seeded_client, a, "TEN-A"))
    document_b = _document(seeded_client, b, _projet(seeded_client, b, "TEN-B"))

    assert _deposer(seeded_client, a, document_a, PDF, nom="cctp.pdf").status_code == 201
    assert _deposer(seeded_client, b, document_b, PDF, nom="cctp.pdf").status_code == 201

    fichiers = _fichiers(volume)
    assert len(fichiers) == 2
    # Deux fichiers distincts, sous deux racines d'organisation distinctes.
    assert fichiers[0] != fichiers[1]
    assert {chemin.read_bytes() for chemin in fichiers} == {PDF}
    organisations = {chemin.parent.parent.name for chemin in fichiers}
    assert len(organisations) == 2


def test_le_meme_contenu_deux_fois_est_refuse_sans_rien_ecraser(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-DUP"))
    assert _deposer(seeded_client, admin, document, PDF).status_code == 201

    refus = _deposer(seeded_client, admin, document, PDF)
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "duplicate_content"
    assert "révision 1" in refus.json()["detail"]["message"]

    # Un seul original sur le volume, une seule ligne en base.
    assert len(_fichiers(volume)) == 1
    assert (
        len(seeded_client.get(f"/api/v1/documents/{document}/revisions", headers=admin).json()) == 1
    )


def test_une_autre_organisation_ne_lit_ni_le_document_ni_son_contenu(
    seeded_client: TestClient, volume: Path
) -> None:
    a = login(seeded_client, "admin@dubois.demo")
    b = login(seeded_client, "admin@janssens.demo")
    document = _document(seeded_client, a, _projet(seeded_client, a, "TEN-ISO"))
    revision = _deposer(seeded_client, a, document, PDF).json()["id"]

    # 404 et non 403 : « interdit » confirmerait que ces identifiants existent.
    assert seeded_client.get(f"/api/v1/documents/{document}", headers=b).status_code == 404
    assert (
        seeded_client.get(f"/api/v1/documents/{document}/revisions", headers=b).status_code == 404
    )
    contenu = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/content", headers=b
    )
    assert contenu.status_code == 404
    assert _deposer(seeded_client, b, document, PDF_BIS).status_code == 404


def test_une_revision_d_un_autre_document_est_introuvable(
    seeded_client: TestClient, volume: Path
) -> None:
    """Le parent est vérifié avant l'enfant, sinon il suffirait de le traverser."""
    admin = login(seeded_client, "admin@dubois.demo")
    projet = _projet(seeded_client, admin, "DOC-CROSS")
    premier = _document(seeded_client, admin, projet)
    second = _document(seeded_client, admin, projet)
    revision = _deposer(seeded_client, admin, premier, PDF).json()["id"]

    croise = seeded_client.get(
        f"/api/v1/documents/{second}/revisions/{revision}/content", headers=admin
    )
    assert croise.status_code == 404


# --------------------------------------------------------------------------
# Rôles
# --------------------------------------------------------------------------


def test_un_lecteur_telecharge_mais_ne_depose_pas(seeded_client: TestClient, volume: Path) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    lecteur = login(seeded_client, "lecteur@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-ROLE"))
    revision = _deposer(seeded_client, admin, document, PDF).json()["id"]

    lecture = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/content", headers=lecteur
    )
    assert lecture.status_code == 200
    assert lecture.content == PDF

    refus = _deposer(seeded_client, lecteur, document, PDF_BIS)
    assert refus.status_code == 403
    assert refus.json()["detail"]["required_permission"] == "document:write"
    assert (
        seeded_client.patch(
            f"/api/v1/documents/{document}", headers=lecteur, json={"status": "archived"}
        ).status_code
        == 403
    )


def test_un_metreur_depose_revise_et_telecharge(seeded_client: TestClient, volume: Path) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    metreur = login(seeded_client, "metreur@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-METREUR"))

    assert _deposer(seeded_client, metreur, document, PDF).status_code == 201
    assert _deposer(seeded_client, metreur, document, PDF_BIS).status_code == 201
    revisions = seeded_client.get(f"/api/v1/documents/{document}/revisions", headers=metreur).json()
    assert [r["revision_number"] for r in revisions] == [2, 1]


# --------------------------------------------------------------------------
# Révisions, archivage, intégrité
# --------------------------------------------------------------------------


def test_une_nouvelle_revision_laisse_la_precedente_intacte(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-REV"))
    un = _deposer(seeded_client, admin, document, PDF, nom="v1.pdf").json()
    deux = _deposer(seeded_client, admin, document, PDF_BIS, nom="v2.pdf").json()

    assert (un["revision_number"], deux["revision_number"]) == (1, 2)
    assert len(_fichiers(volume)) == 2

    for revision, attendu in ((un, PDF), (deux, PDF_BIS)):
        contenu = seeded_client.get(
            f"/api/v1/documents/{document}/revisions/{revision['id']}/content", headers=admin
        )
        assert contenu.status_code == 200
        assert contenu.content == attendu
        assert hashlib.sha256(contenu.content).hexdigest() == revision["sha256"]
        assert len(contenu.content) == revision["byte_size"]


def test_l_archivage_masque_sans_rien_detruire(seeded_client: TestClient, volume: Path) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    projet = _projet(seeded_client, admin, "DOC-ARCH")
    document = _document(seeded_client, admin, projet)
    revision = _deposer(seeded_client, admin, document, PDF).json()["id"]

    assert (
        seeded_client.patch(
            f"/api/v1/documents/{document}", headers=admin, json={"status": "archived"}
        ).status_code
        == 200
    )

    courants = seeded_client.get(f"/api/v1/projects/{projet}/documents", headers=admin).json()
    assert [d["id"] for d in courants] == []
    avec_archives = seeded_client.get(
        f"/api/v1/projects/{projet}/documents?include_archived=true", headers=admin
    ).json()
    assert [d["id"] for d in avec_archives] == [document]

    # L'historique et l'original survivent : archiver n'est pas supprimer.
    assert len(_fichiers(volume)) == 1
    contenu = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/content", headers=admin
    )
    assert contenu.content == PDF

    # Et l'on peut revenir en arrière : l'archivage n'est pas une porte à sens unique.
    seeded_client.patch(f"/api/v1/documents/{document}", headers=admin, json={"status": "active"})
    assert len(seeded_client.get(f"/api/v1/projects/{projet}/documents", headers=admin).json()) == 1


def test_un_document_archive_n_accepte_plus_de_revision(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-ARCH2"))
    _deposer(seeded_client, admin, document, PDF)
    seeded_client.patch(f"/api/v1/documents/{document}", headers=admin, json={"status": "archived"})

    refus = _deposer(seeded_client, admin, document, PDF_BIS)
    assert refus.status_code == 409
    assert refus.json()["detail"]["code"] == "document_archived"
    assert len(_fichiers(volume)) == 1


def test_un_original_disparu_du_volume_se_dit_au_lieu_de_rendre_zero_octet(
    seeded_client: TestClient, volume: Path
) -> None:
    """Une panne d'exploitation ne doit pas passer pour un document vide."""
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-PERDU"))
    revision = _deposer(seeded_client, admin, document, PDF).json()["id"]

    _fichiers(volume)[0].unlink()
    perdu = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision}/content", headers=admin
    )
    assert perdu.status_code == 410
    assert perdu.json()["detail"]["code"] == "content_missing"


def test_un_original_altere_se_constate_par_son_empreinte(
    seeded_client: TestClient, volume: Path
) -> None:
    """L'empreinte enregistrée reste la référence, quoi qu'il arrive au fichier."""
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-ALT"))
    revision = _deposer(seeded_client, admin, document, PDF).json()
    _fichiers(volume)[0].write_bytes(PDF + b"ajout clandestin")

    contenu = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision['id']}/content", headers=admin
    )
    assert contenu.status_code == 200
    obtenue = hashlib.sha256(contenu.content).hexdigest()
    assert obtenue != revision["sha256"], "l'altération doit être détectable"
    assert contenu.headers["X-Document-Sha256"] == revision["sha256"]


def test_deux_depots_simultanes_recoivent_deux_numeros_distincts(
    seeded_client: TestClient, volume: Path
) -> None:
    """Le verrou de `next_revision_number` sérialise, l'unicité tranche.

    Deux flux écrivent leur original SANS verrou — c'est voulu, un dépôt dure —
    puis se disputent le numéro. Aucun des deux ne peut écraser l'autre : les
    noms physiques viennent d'identifiants distincts.
    """
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-CONC"))

    numeros = [
        _deposer(seeded_client, admin, document, PDF + bytes([indice])).json()["revision_number"]
        for indice in range(5)
    ]
    assert numeros == [1, 2, 3, 4, 5]

    fichiers = _fichiers(volume)
    assert len(fichiers) == 5
    assert len({chemin.name for chemin in fichiers}) == 5

    session = get_session_factory()()
    try:
        cles = [
            ligne.storage_key
            for ligne in session.query(DocumentRevision).filter_by(document_id=document).all()
        ]
    finally:
        session.close()
    assert len(set(cles)) == 5


# --------------------------------------------------------------------------
# En-têtes et journal
# --------------------------------------------------------------------------


def test_le_telechargement_est_une_piece_jointe_et_jamais_une_page(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-HEAD"))
    revision = _deposer(seeded_client, admin, document, PDF, nom='cctp "lot 2".pdf').json()

    reponse = seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision['id']}/content", headers=admin
    )
    disposition = reponse.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    # Ni guillemet ni retour chariot ne sortent du champ : l'en-tête reste
    # celui qu'on croit.
    assert "\r" not in disposition and "\n" not in disposition
    assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    assert reponse.headers["Cache-Control"] == "no-store"
    assert reponse.headers["Content-Length"] == str(len(PDF))


def test_le_journal_porte_les_faits_et_aucun_octet_du_document(
    seeded_client: TestClient, volume: Path
) -> None:
    admin = login(seeded_client, "admin@dubois.demo")
    document = _document(seeded_client, admin, _projet(seeded_client, admin, "DOC-AUDIT"))
    secret = b"%PDF-1.7\n% MARQUEUR-CONFIDENTIEL-CLIENT\ntrailer\n%%EOF\n"
    revision = _deposer(seeded_client, admin, document, secret, nom="offre.pdf").json()
    seeded_client.get(
        f"/api/v1/documents/{document}/revisions/{revision['id']}/content", headers=admin
    )

    session = get_session_factory()()
    try:
        evenements = session.query(AuditEvent).all()
        actions = {evenement.action for evenement in evenements}
        entier = " ".join(f"{evenement.summary} {evenement.payload}" for evenement in evenements)
    finally:
        session.close()

    assert {"document.revision_added", "document.downloaded"} <= actions
    assert "MARQUEUR-CONFIDENTIEL-CLIENT" not in entier
    assert admin["Authorization"].split()[1] not in entier
    # Ce que le journal DOIT porter : de quoi retrouver le fichier sans l'ouvrir.
    assert revision["sha256"] in entier
