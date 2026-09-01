"""Importer un classeur XLSX : la convergence avec le CSV, et ce qui est refusé.

Deux questions, et une seule réponse à chacune.

**Un CSV et un XLSX équivalents donnent-ils la même chose ?** C'est l'invariant
de cette tranche. Deux pipelines auraient divergé au premier alias ajouté, et
l'utilisateur aurait obtenu deux résultats pour un même tableau selon le format
sous lequel il l'enregistre. Le contrôle ne compare pas des chaînes mais les
lignes NORMALISÉES, puis les prix RÉELLEMENT écrits en base — c'est là que la
promesse se tient ou se rompt.

**Que refuse-t-on, et le dit-on ?** Un classeur est une archive : elle peut être
une bombe, porter du code, ou renvoyer à des fichiers qu'on n'ira pas chercher.
Chaque refus porte un code stable, parce que « macros » et « trop de lignes »
n'appellent pas la même correction.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import classeur, price_import

from . import classeurs_fictifs as faux
from .conftest import login

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "imports"

TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="session")
def classeurs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fabrique les classeurs de fixture, et rend le dossier qui les porte.

    Les fabriquer ici plutôt que de les commiter tient la même règle que le
    reste : un `.xlsx` est une archive binaire, et un binaire commité est un
    bloc que personne ne relit. Les fabriquer dans un dossier TEMPORAIRE plutôt
    que dans `fixtures/` évite en plus qu'une exécution de la suite laisse des
    fichiers derrière elle, ou qu'un test dépende d'un script lancé à la main.

    C'est le script du dépôt qui les fabrique, pas une copie de sa logique :
    une seconde implémentation divergerait, et le classeur éprouvé ne serait
    plus celui que le projet livre.
    """
    import sys

    racine = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(racine / "scripts"))
    try:
        from fabriquer_classeurs_de_test import CLASSEURS, fabriquer
    finally:
        sys.path.pop(0)

    dossier = tmp_path_factory.mktemp("classeurs")
    for nom_csv, nom_xlsx, feuille in CLASSEURS:
        fabriquer(FIXTURES / nom_csv, dossier / nom_xlsx, feuille=feuille)
    return dossier


@pytest.fixture()
def headers(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def version_id(seeded_client: TestClient, headers: dict[str, str]) -> str:
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]
    return seeded_client.get(f"/api/v1/price-books/{book['id']}/versions", headers=headers).json()[
        0
    ]["id"]


def previsualiser(
    client: TestClient,
    headers: dict[str, str],
    version_id: str,
    octets: bytes,
    *,
    nom: str = "prix.xlsx",
    feuille: str | None = None,
    strategy: str = "create",
) -> Any:
    donnees: dict[str, str] = {"strategy": strategy}
    if feuille is not None:
        donnees["feuille"] = feuille
    return client.post(
        f"/api/v1/price-books/versions/{version_id}/imports/preview",
        headers=headers,
        files={"file": (nom, octets, TYPE_XLSX)},
        data=donnees,
    )


# --------------------------------------------------------------------------
# 1. La convergence : un seul pipeline pour deux formats
# --------------------------------------------------------------------------


def _valeurs_comparables(lignes: list[price_import.ParsedRow]) -> list[dict[str, Any]]:
    """Les lignes normalisées, les nombres comparés comme des NOMBRES.

    `Decimal("41.50")` et `Decimal("41.5")` sont égaux et s'écrivent
    différemment. Le CSV porte « 41,50 » — du texte, donc une échelle ; le
    classeur porte le flottant 41,5, et cette échelle n'existe tout simplement
    pas dans le fichier. Exiger la même CHAÎNE demanderait au lecteur de
    classeur d'inventer des décimales que personne n'a écrites.
    """
    comparables = []
    for ligne in lignes:
        valeurs = dict(ligne.normalized or {})
        for colonne, brut in list(valeurs.items()):
            if brut in (None, ""):
                continue
            try:
                valeurs[colonne] = Decimal(str(brut))
            except InvalidOperation:
                # Ce n'est pas un nombre — un libellé, une unité, un code. On
                # le laisse tel quel : c'est alors la chaîne qui doit coïncider.
                continue
        comparables.append(valeurs)
    return comparables


@pytest.mark.parametrize("base", ["prix_valides_5_lignes", "prix_5_valides_2_erreurs"])
def test_un_csv_et_un_xlsx_equivalents_donnent_les_memes_lignes(base: str, classeurs: Path) -> None:
    """L'invariant de la tranche, mesuré sur les deux jeux de fixtures.

    Les classeurs sont fabriqués depuis les CSV par
    `scripts/fabriquer_classeurs_de_test.py`, qui écrit des NOMBRES et des
    DATES là où le CSV porte « 41,50 » et « 2026-01-01 ». C'est ce qui rend la
    comparaison probante : les deux fichiers disent la même chose sous deux
    formes que rien n'oblige à converger — sinon le pipeline commun.
    """
    lignes_csv, meta_csv = price_import.parse_csv((FIXTURES / f"{base}.csv").read_bytes())
    lignes_xlsx, meta_xlsx = price_import.parse_csv((classeurs / f"{base}.xlsx").read_bytes())

    assert meta_csv["format"] == "csv"
    assert meta_xlsx["format"] == "xlsx"
    assert _valeurs_comparables(lignes_csv) == _valeurs_comparables(lignes_xlsx)


@pytest.mark.parametrize("base", ["prix_valides_5_lignes", "prix_5_valides_2_erreurs"])
def test_les_memes_erreurs_tombent_sur_les_memes_lignes(base: str, classeurs: Path) -> None:
    """Une erreur doit désigner la ligne que l'utilisateur voit dans SON fichier.

    Le CSV compte des lignes, le classeur compte des rangs. Recalculer le
    numéro au lieu de le transporter décalerait le message dès la première
    ligne vide au milieu du fichier — et l'utilisateur chercherait l'erreur au
    mauvais endroit.
    """
    lignes_csv, _ = price_import.parse_csv((FIXTURES / f"{base}.csv").read_bytes())
    lignes_xlsx, _ = price_import.parse_csv((classeurs / f"{base}.xlsx").read_bytes())

    def signature(lignes: list[price_import.ParsedRow]) -> list[tuple[int, tuple[str, ...]]]:
        return [(ligne.line_number, tuple(e.code for e in ligne.errors)) for ligne in lignes]

    assert signature(lignes_csv) == signature(lignes_xlsx)
    # Et le jeu fautif porte bien ses deux erreurs, sinon ce test passerait
    # pour la mauvaise raison : deux listes vides sont égales.
    if base.endswith("2_erreurs"):
        assert sum(1 for ligne in lignes_xlsx if ligne.errors) == 2


def test_le_classeur_passe_par_le_VRAI_endpoint_et_les_prix_arrivent_en_base(
    seeded_client: TestClient, headers: dict[str, str], version_id: str, classeurs: Path
) -> None:
    """De bout en bout : le classeur monte, se confirme, et les prix existent.

    Le test qui compte. Les précédents éprouvent la lecture ; celui-ci éprouve
    la route réelle — permission, plafond de taille, détection de format,
    staging, puis écriture — sur le même chemin que l'écran emprunte.
    """
    octets = (classeurs / "prix_valides_5_lignes.xlsx").read_bytes()
    apercu = previsualiser(seeded_client, headers, version_id, octets)
    assert apercu.status_code == 200, apercu.text
    rapport = apercu.json()

    assert rapport["valid_count"] == 5
    assert rapport["error_count"] == 0
    # Le rapport dit de quel format il vient et quelle feuille il a lue : sans
    # cela, l'utilisateur d'un classeur à plusieurs feuilles ne saurait pas
    # laquelle a été prise.
    assert rapport["meta"]["format"] == "xlsx"
    assert rapport["meta"]["feuille"] == "Prix"

    confirme = seeded_client.post(
        f"/api/v1/price-books/imports/{rapport['batch_id']}/commit",
        headers=headers,
        json={"strategy": "create", "confirm": True},
    )
    assert confirme.status_code == 200, confirme.text
    assert confirme.json()["created"] == 5

    items = seeded_client.get(
        f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
    ).json()["items"]
    par_code = {item["code"]: item for item in items}
    assert "MAT-SAB-001" in par_code
    # Le prix est bien celui du classeur, à l'échelle de la base.
    assert Decimal(par_code["MAT-SAB-001"]["unit_price"]) == Decimal("41.50")


def test_un_csv_et_un_xlsx_ecrivent_des_prix_identiques_en_base(
    seeded_client: TestClient, headers: dict[str, str], classeurs: Path
) -> None:
    """L'équivalence là où elle engage le produit : les lignes ÉCRITES.

    Comparer les lignes normalisées prouve que le pipeline est commun ; comparer
    ce qui atterrit en base prouve que la promesse tient jusqu'au bout. Les deux
    imports vont dans DEUX versions distinctes de bibliothèque, sans quoi le
    second ne ferait que constater que le premier existe déjà.
    """
    book = seeded_client.get("/api/v1/price-books", headers=headers).json()[0]

    ecrits = {}
    for format_, nom in (
        ("csv", "prix_valides_5_lignes.csv"),
        ("xlsx", "prix_valides_5_lignes.xlsx"),
    ):
        version = seeded_client.post(
            f"/api/v1/price-books/{book['id']}/versions",
            headers=headers,
            json={"label": f"Comparaison {format_}"},
        )
        assert version.status_code in (200, 201), version.text
        version_id = version.json()["id"]

        apercu = previsualiser(
            seeded_client, headers, version_id, ((classeurs if nom.endswith(".xlsx") else FIXTURES) / nom).read_bytes(), nom=nom
        )
        assert apercu.status_code == 200, apercu.text
        lot = apercu.json()["batch_id"]
        confirme = seeded_client.post(
            f"/api/v1/price-books/imports/{lot}/commit",
            headers=headers,
            json={"strategy": "create", "confirm": True},
        )
        assert confirme.status_code == 200, confirme.text

        items = seeded_client.get(
            f"/api/v1/price-books/versions/{version_id}/items?limit=200", headers=headers
        ).json()["items"]
        # Les champs d'identité de la ligne, sans ceux qui changent d'un import
        # à l'autre par construction — identifiant, version, horodatage.
        ecrits[format_] = sorted(
            (
                item["code"],
                item["label"],
                item["unit_code"],
                Decimal(item["unit_price"]),
                item["currency"],
                item.get("resource_kind"),
                item.get("valid_from"),
            )
            for item in items
        )

    assert ecrits["csv"], "aucun prix écrit : le test passerait pour rien"
    assert ecrits["csv"] == ecrits["xlsx"]


# --------------------------------------------------------------------------
# 2. Ce qui est refusé, et ce que le refus dit
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cas", "octets_de", "code_attendu"),
    [
        ("un ancien .xls", lambda: faux.OLE2, "format_ole2"),
        ("une archive tronquée", lambda: faux.ZIP_TRONQUE, "archive_illisible"),
        ("un fichier vide", lambda: b"", "fichier_vide"),
        ("des macros", faux.avec_des_macros, "macros_refusees"),
        ("un lien externe", faux.avec_un_lien_externe, "liens_externes_refuses"),
        ("une formule", faux.avec_une_formule, "formules_refusees"),
        ("une bombe de décompression", faux.bombe_de_decompression, "decompresse_trop_grand"),
        ("un chemin remontant", faux.avec_un_chemin_remontant, "chemin_suspect"),
        ("une archive sans classeur", faux.archive_sans_classeur, "pas_un_classeur"),
    ],
)
def test_le_lecteur_refuse_en_nommant_le_motif(cas: str, octets_de: Any, code_attendu: str) -> None:
    """Chaque refus porte un CODE, pas seulement une phrase.

    « Macros », « formules » et « trop de lignes » n'appellent pas la même
    correction de la part de l'utilisateur. Un message unique les confondrait,
    et l'écran ne saurait pas quoi proposer.
    """
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets_de())
    assert refus.value.code == code_attendu, f"{cas} : {refus.value.message}"
    # Et le message est en français, adressé à qui téléverse — pas une trace.
    assert refus.value.message
    assert not refus.value.message.startswith("Traceback")


def test_une_bombe_de_decompression_est_refusee_SANS_etre_depliee() -> None:
    """La borne se lit dans l'en-tête de l'archive, avant toute extraction.

    C'est tout l'intérêt : refuser après extraction aurait déjà coûté la
    mémoire contre laquelle la borne existe. Mesuré ici — quelques kilooctets
    d'entrée annoncent près de cent mégaoctets.
    """
    octets = faux.bombe_de_decompression()
    assert len(octets) < 200 * 1024, "la bombe elle-même doit rester minuscule"

    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets)
    assert refus.value.code == "decompresse_trop_grand"


def test_un_classeur_refuse_donne_un_422_nomme_par_la_route(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """Le refus traverse la route sans devenir un 500.

    Une exception qui remonte nue rendrait « Internal Server Error » : l'écran
    afficherait une panne là où il s'agit d'un fichier à corriger, et
    l'utilisateur n'aurait rien à faire de cette information.
    """
    reponse = previsualiser(seeded_client, headers, version_id, faux.avec_des_macros())
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == "macros_refusees"


def test_le_format_est_detecte_par_le_CONTENU_et_non_par_le_nom(
    seeded_client: TestClient, headers: dict[str, str], version_id: str, classeurs: Path
) -> None:
    """Un classeur nommé `.csv` reste un classeur, et l'inverse aussi.

    Renommer un fichier est le geste le plus courant qui soit. Choisir le
    lecteur sur l'extension enverrait une archive au lecteur CSV, qui rendrait
    des lignes de charabia SANS RIEN REFUSER — le pire des deux mondes.
    """
    classeur_nomme_csv = previsualiser(
        seeded_client,
        headers,
        version_id,
        (classeurs / "prix_valides_5_lignes.xlsx").read_bytes(),
        nom="prix.csv",
    )
    assert classeur_nomme_csv.status_code == 200, classeur_nomme_csv.text
    assert classeur_nomme_csv.json()["meta"]["format"] == "xlsx"
    assert classeur_nomme_csv.json()["valid_count"] == 5

    csv_nomme_xlsx = previsualiser(
        seeded_client,
        headers,
        version_id,
        (FIXTURES / "prix_valides_5_lignes.csv").read_bytes(),
        nom="prix.xlsx",
    )
    assert csv_nomme_xlsx.status_code == 200, csv_nomme_xlsx.text
    assert csv_nomme_xlsx.json()["meta"]["format"] == "csv"
    assert csv_nomme_xlsx.json()["valid_count"] == 5


# --------------------------------------------------------------------------
# 3. La feuille : choisie, jamais devinée en silence
# --------------------------------------------------------------------------


ENTETES = ["code", "libelle", "unite", "prix_unitaire"]


def _a_trois_feuilles() -> bytes:
    """Un classeur réel : une garde, un barème, des notes."""
    return faux.classeur(
        feuilles={
            "Garde": [["Barème fictif"], ["Usage interne"]],
            "Barème": [ENTETES, ["MAT-FIC-002", "Grave fictive 0/31,5", "t", 18.4]],
            "Notes": [["Rien à importer ici"]],
        }
    )


def test_sans_choix_la_premiere_feuille_est_lue_ET_les_autres_sont_dites(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """Deviner en silence serait le défaut ; deviner en le disant ne l'est pas.

    Un classeur de prix réel porte souvent une feuille de garde avant le
    barème. Prendre la première sans le signaler ferait conclure à l'utilisateur
    que son fichier est vide ; rendre la liste lui permet de rectifier en un
    geste, depuis le rapport qu'il a déjà sous les yeux.
    """
    reponse = previsualiser(seeded_client, headers, version_id, _a_trois_feuilles())
    assert reponse.status_code == 200, reponse.text
    meta = reponse.json()["meta"]

    assert meta["feuille"] == "Garde"
    assert meta["feuilles"] == ["Garde", "Barème", "Notes"]
    # La garde ne porte pas les colonnes obligatoires : le rapport le dit,
    # plutôt que de rendre zéro ligne sans expliquer pourquoi.
    assert meta["fatal"] == "missing_required_columns"


def test_la_feuille_demandee_est_celle_qui_est_lue(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    reponse = previsualiser(
        seeded_client, headers, version_id, _a_trois_feuilles(), feuille="Barème"
    )
    assert reponse.status_code == 200, reponse.text
    rapport = reponse.json()
    assert rapport["meta"]["feuille"] == "Barème"
    assert rapport["valid_count"] == 1


def test_une_feuille_inconnue_est_refusee_en_nommant_celles_qui_existent(
    seeded_client: TestClient, headers: dict[str, str], version_id: str
) -> None:
    """Refuser sans dire ce qui existe obligerait à deviner le nom exact."""
    reponse = previsualiser(
        seeded_client, headers, version_id, _a_trois_feuilles(), feuille="Tarifs"
    )
    assert reponse.status_code == 422, reponse.text
    detail = reponse.json()["detail"]
    assert detail["code"] == "feuille_inconnue"
    assert detail["feuilles"] == ["Garde", "Barème", "Notes"]


def test_les_noms_de_feuilles_se_lisent_sans_importer(seeded_client: TestClient) -> None:
    """L'écran doit pouvoir proposer la liste avant que rien ne soit écrit."""
    assert classeur.noms_des_feuilles(_a_trois_feuilles()) == ["Garde", "Barème", "Notes"]


# --------------------------------------------------------------------------
# 4. Les bornes du tableau lui-même
# --------------------------------------------------------------------------


def test_trop_de_lignes_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """La borne est abaissée plutôt que de fabriquer cinquante mille lignes.

    Écrire le classeur réel coûterait des dizaines de secondes à chaque
    exécution de la suite, pour éprouver exactement la même comparaison.
    """
    monkeypatch.setattr(classeur, "LIGNES_MAXIMUM", 3)
    octets = faux.classeur(
        [[f"MAT-FIC-{rang:03d}", "Ligne fictive", "m3", 10.0] for rang in range(10)]
    )
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets)
    assert refus.value.code == "trop_de_lignes"


def test_trop_de_colonnes_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classeur, "COLONNES_MAXIMUM", 3)
    octets = faux.classeur([["a", "b", "c", "d", "e"]], entetes=["c1", "c2", "c3", "c4", "c5"])
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets)
    assert refus.value.code == "trop_de_colonnes"


def test_trop_de_feuilles_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classeur, "FEUILLES_MAXIMUM", 2)
    octets = faux.classeur(feuilles={f"F{rang}": [ENTETES] for rang in range(5)})
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets)
    assert refus.value.code == "trop_de_feuilles"


def test_un_ratio_de_compression_excessif_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le ratio attrape ce que la taille absolue laisse passer.

    Une archive qui reste sous le plafond du déplié mais se dilate cent fois
    n'est pas un tableau de prix : c'est une charge, et son coût de lecture est
    payé avant qu'on puisse constater qu'elle ne contient rien d'utile.
    """
    monkeypatch.setattr(classeur, "TAILLE_DECOMPRESSEE_MAXIMUM", 1024 * 1024 * 1024)
    monkeypatch.setattr(classeur, "RATIO_MAXIMUM", 5)
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(faux.bombe_de_decompression(entrees=2, par_entree=4 * 1024 * 1024))
    assert refus.value.code == "ratio_excessif"


def test_trop_d_entrees_dans_l_archive_est_refuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mille petits fichiers passent chacun sous les bornes précédentes."""
    monkeypatch.setattr(classeur, "ENTREES_MAXIMUM", 5)
    octets = faux.reecrire(faux.classeur(), {f"xl/bruit{rang}.xml": b"<x/>" for rang in range(20)})
    with pytest.raises(classeur.ClasseurRefuse) as refus:
        classeur.lire(octets)
    assert refus.value.code == "trop_d_entrees"
