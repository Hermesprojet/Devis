"""Acceptance scenario 2: preview shows errors before anything is written."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import login

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "imports"


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version_id(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    return seeded_client.get(f"/api/v1/price-books/{book['id']}/versions", headers=headers).json()[
        0
    ]["id"]


def upload(client: TestClient, headers: dict[str, str], version_id: str, name: str):
    payload = (FIXTURES / name).read_bytes()
    return client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": (name, payload, "text/csv")},
    )


def count_items(client: TestClient, headers: dict[str, str], version_id: str) -> int:
    return client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["page"]["total"]


def test_preview_reports_five_valid_and_two_broken_rows(seeded_client, headers, version_id):
    response = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["row_count"] == 7
    assert report["valid_count"] == 5
    assert report["error_count"] == 2


def test_preview_writes_nothing_into_the_library(seeded_client, headers, version_id):
    before = count_items(seeded_client, headers, version_id)
    upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv")
    assert count_items(seeded_client, headers, version_id) == before


def test_errors_name_the_line_the_column_and_the_reason(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv").json()
    failing = {row["line_number"]: row["errors"] for row in report["rows"] if not row["is_valid"]}
    assert set(failing) == {4, 7}
    assert {e["code"] for e in failing[4]} == {"unknown_unit"}
    assert {e["column"] for e in failing[4]} == {"unit_code"}
    assert {e["code"] for e in failing[7]} == {"required"}
    assert {e["column"] for e in failing[7]} == {"code", "unit_price"}


def test_commit_creates_exactly_the_valid_rows(seeded_client, headers, version_id):
    before = count_items(seeded_client, headers, version_id)
    batch = upload(seeded_client, headers, version_id, "prix_5_valides_2_erreurs.csv").json()
    response = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 5
    assert count_items(seeded_client, headers, version_id) == before + 5


def test_commit_requires_an_explicit_confirmation(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    response = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_a_batch_cannot_be_committed_twice(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    body = {"strategy": "create", "confirm": True}
    first = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit", headers=headers, json=body
    )
    second = seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit", headers=headers, json=body
    )
    assert first.status_code == 200
    assert second.status_code == 409


def test_french_decimal_comma_and_semicolon_are_detected(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert report["meta"]["delimiter"] == ";"
    sand = next(r for r in report["rows"] if r["normalized"]["code"] == "MAT-SAB-001")
    assert sand["normalized"]["unit_price"] == "41.50"


def test_headers_are_mapped_from_french_labels(seeded_client, headers, version_id):
    report = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert report["column_mapping"]["libelle"] == "label"
    assert report["column_mapping"]["prix_unitaire"] == "unit_price"
    assert report["meta"]["unmapped_headers"] == []


def test_second_import_of_the_same_codes_flags_duplicates(seeded_client, headers, version_id):
    batch = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{batch['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    again = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    assert again["duplicate_count"] == 5


def test_replace_strategy_updates_instead_of_creating(seeded_client, headers, version_id):
    first = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{first['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    before = count_items(seeded_client, headers, version_id)
    second = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    outcome = seeded_client.post(
        f"/api/v1/price-books/imports/{second['batch_id']}/commit",
        headers=headers,
        json={"strategy": "replace", "confirm": True},
    ).json()
    assert outcome["updated"] == 5
    assert count_items(seeded_client, headers, version_id) == before


def test_create_strategy_reports_conflicts_without_overwriting(seeded_client, headers, version_id):
    first = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    seeded_client.post(
        f"/api/v1/price-books/imports/{first['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    second = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv").json()
    outcome = seeded_client.post(
        f"/api/v1/price-books/imports/{second['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    ).json()
    assert outcome["created"] == 0
    assert outcome["conflicted"] == 5


def test_an_empty_file_is_reported_not_crashed(seeded_client, headers, version_id):
    response = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("vide.csv", b"", "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["meta"]["fatal"] == "empty_file"


def test_a_file_without_the_required_columns_is_rejected_before_parsing(
    seeded_client, headers, version_id
):
    response = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("bizarre.csv", b"colonne_a;colonne_b\n1;2\n", "text/csv")},
    )
    report = response.json()
    assert report["meta"]["fatal"] == "missing_required_columns"
    assert set(report["meta"]["missing_required_columns"]) == {
        "code",
        "label",
        "unit_code",
        "unit_price",
    }


def test_import_into_a_published_version_is_refused(seeded_client, headers, version_id):
    seeded_client.post(f"/api/v1/price-books/versions/{version_id}/publish", headers=headers)
    response = upload(seeded_client, headers, version_id, "prix_valides_5_lignes.csv")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "version_published"


def test_a_reader_cannot_import(seeded_client, version_id):
    reader = login(seeded_client, "lecteur@dubois.demo")
    response = upload(seeded_client, reader, version_id, "prix_valides_5_lignes.csv")
    assert response.status_code == 403
    assert response.json()["detail"]["required_permission"] == "pricebook:write"


# --------------------------------------------------------------------------
# Les deux stratégies d'import que rien n'exerçait
#
# `create` et `replace` avaient leur test ; `ignore` et `merge` n'en avaient
# aucun. Mesuré par une campagne de mutation : en faisant écraser `ignore`
# comme `replace`, et en retirant à `merge` son filtre sur les valeurs vides,
# la suite complète restait verte dans les deux cas.
#
# Ces fichiers sont construits ici plutôt que dans `fixtures/imports/` parce
# que ce qui compte est le COUPLE : le même code, une valeur changée et une
# autre laissée vide. Le second fichier n'a de sens que par rapport au premier.
# --------------------------------------------------------------------------

_CSV_AVEC_FOURNISSEUR = (
    "code;libelle;famille;type;unite;prix_unitaire;devise;fournisseur\n"
    "IMP-001;Poste d'essai;Matériaux;materiau;m3;10,00;EUR;Fournisseur Alpha\n"
)

#: Même code, prix changé, fournisseur laissé vide — c'est ce vide qui sépare
#: `merge` de `replace`.
_CSV_SANS_FOURNISSEUR = (
    "code;libelle;famille;type;unite;prix_unitaire;devise;fournisseur\n"
    "IMP-001;Poste d'essai;Matériaux;materiau;m3;99,00;EUR;\n"
)


def _televerser(client: TestClient, headers: dict[str, str], version_id: str, contenu: str):
    return client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": ("essai.csv", contenu.encode("utf-8"), "text/csv")},
    )


def _valider(client: TestClient, headers: dict[str, str], batch_id: str, strategie: str):
    return client.post(
        f"/api/v1/price-books/imports/{batch_id}/commit",
        headers=headers,
        json={"strategy": strategie, "confirm": True},
    )


def _importer(
    client: TestClient, headers: dict[str, str], version_id: str, contenu: str, strategie: str
) -> dict:
    apercu = _televerser(client, headers, version_id, contenu)
    assert apercu.status_code == 200, apercu.text
    resultat = _valider(client, headers, apercu.json()["batch_id"], strategie)
    assert resultat.status_code == 200, resultat.text
    return resultat.json()


def _poste(client: TestClient, headers: dict[str, str], version_id: str, code: str) -> dict:
    items = client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["items"]
    trouve = next((item for item in items if item["code"] == code), None)
    assert trouve is not None, f"le poste {code} devrait exister"
    return trouve


def test_ignore_strategy_leaves_the_existing_row_exactly_as_it_was(
    seeded_client, headers, version_id
):
    """« Ignorer » compte les lignes sautées ET ne touche à rien.

    Le compteur seul ne suffirait pas : une stratégie qui écrase tout en
    annonçant « ignoré » afficherait le même chiffre.
    """
    _importer(seeded_client, headers, version_id, _CSV_AVEC_FOURNISSEUR, "create")
    avant = _poste(seeded_client, headers, version_id, "IMP-001")
    assert avant["unit_price"] == "10"
    assert avant["supplier_name"] == "Fournisseur Alpha"

    resultat = _importer(seeded_client, headers, version_id, _CSV_SANS_FOURNISSEUR, "ignore")
    assert resultat["skipped"] == 1
    assert resultat["updated"] == 0
    assert resultat["created"] == 0

    apres = _poste(seeded_client, headers, version_id, "IMP-001")
    assert apres["unit_price"] == "10", "le prix ne devait pas changer"
    assert apres["supplier_name"] == "Fournisseur Alpha"


def test_merge_updates_what_is_given_and_keeps_what_is_left_blank(
    seeded_client, headers, version_id
):
    """Ce qui sépare `merge` de `replace` : une colonne vide n'efface pas.

    Un fichier partiel est le cas courant — on remonte des prix sans réémettre
    les fournisseurs. Avec `replace`, ce même fichier viderait la colonne.
    """
    _importer(seeded_client, headers, version_id, _CSV_AVEC_FOURNISSEUR, "create")

    resultat = _importer(seeded_client, headers, version_id, _CSV_SANS_FOURNISSEUR, "merge")
    assert resultat["updated"] == 1

    apres = _poste(seeded_client, headers, version_id, "IMP-001")
    assert apres["unit_price"] == "99", "la valeur fournie devait être reprise"
    assert apres["supplier_name"] == "Fournisseur Alpha", (
        "une colonne laissée vide ne doit pas effacer la valeur existante"
    )


def test_replace_does_erase_what_merge_preserves(seeded_client, headers, version_id):
    """Le contraste, sans lequel le test de `merge` ne prouverait pas grand-chose.

    Si `replace` conservait lui aussi les valeurs existantes, les deux
    stratégies seraient identiques et le test ci-dessus passerait sur les deux.
    """
    _importer(seeded_client, headers, version_id, _CSV_AVEC_FOURNISSEUR, "create")

    resultat = _importer(seeded_client, headers, version_id, _CSV_SANS_FOURNISSEUR, "replace")
    assert resultat["updated"] == 1

    apres = _poste(seeded_client, headers, version_id, "IMP-001")
    assert apres["unit_price"] == "99"
    assert apres["supplier_name"] is None, "`replace` doit bien effacer, lui"


def test_publishing_a_version_twice_is_refused(seeded_client, headers, version_id):
    """Publier est irréversible ; le redemander est un conflit, pas un succès.

    Placé ici auprès de `test_import_into_a_published_version_is_refused`, qui
    vérifie l'autre moitié de la même règle : ce qu'une version publiée
    n'accepte plus.

    Mesuré : en retirant le refus, la suite complète restait verte — une
    seconde publication réécrivait `published_at` et produisait un second
    événement d'audit pour un acte qui n'a eu lieu qu'une fois.
    """
    premiere = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/publish", headers=headers
    )
    assert premiere.status_code == 200, premiere.text
    assert premiere.json()["status"] == "published"
    publiee_le = premiere.json()["published_at"]

    seconde = seeded_client.post(
        f"/api/v1/price-books/versions/{version_id}/publish", headers=headers
    )
    assert seconde.status_code == 409
    assert seconde.json()["detail"]["code"] == "already_published"

    # La date de publication d'origine n'a pas bougé.
    versions = seeded_client.get(
        f"/api/v1/price-books/{seeded_client.get('/api/v1/price-books', headers=headers).json()[0]['id']}/versions",
        headers=headers,
    ).json()
    actuelle = next(v for v in versions if v["id"] == version_id)
    assert actuelle["published_at"] == publiee_le
