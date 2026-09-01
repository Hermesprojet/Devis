"""Le profil de l'entreprise émettrice, et le logo qui l'accompagne.

Ce fichier éprouve trois choses distinctes, et la troisième est la plus
délicate :

1. **ce que le décodeur accepte et refuse** — six types de couleur PNG, les
   profondeurs sous-octet, la palette, l'alpha, et les refus nommés ;
2. **ce que l'API garde et rend** — le profil, les permissions, l'audit, et le
   fait que le chemin de stockage ne sort jamais ;
3. **ce qui reste sur le disque** — un fichier refusé ne doit rien laisser, un
   remplacement doit retirer l'ancien, et une transaction annulée doit rendre
   le volume à son état d'avant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metreo_api.services import images

from . import images_fictives as fixtures
from .conftest import login, running_on_postgresql


@pytest.fixture()
def admin(seeded_client: TestClient) -> dict[str, str]:
    return login(seeded_client, "admin@dubois.demo")


@pytest.fixture()
def racine(monkeypatch: pytest.MonkeyPatch) -> Path:
    """La racine de stockage du test, telle que l'application la voit."""
    from metreo_api.config import get_settings

    return Path(get_settings().storage_root).resolve()


def _logos(racine: Path) -> list[Path]:
    dossier = racine / "logos"
    return sorted(p for p in dossier.rglob("*") if p.is_file()) if dossier.exists() else []


# --------------------------------------------------------------------------
# 1. Le décodeur : ce qu'il sait lire, et ce qu'il refuse en le nommant
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nom", "octets", "espace", "attend_alpha"),
    [
        ("rvb", fixtures.carre(), "DeviceRGB", False),
        ("rvb+alpha", fixtures.carre(alpha=True), "DeviceRGB", True),
        ("gris", fixtures.gris(), "DeviceGray", False),
        ("palette+transparence", fixtures.palette_transparente(), "DeviceRGB", True),
        ("palette 1 bit", fixtures.un_bit(), "DeviceRGB", False),
        ("rvb 16 bits", fixtures.seize_bits(), "DeviceRGB", False),
    ],
)
def test_chaque_type_de_couleur_png_se_decode(
    nom: str, octets: bytes, espace: str, attend_alpha: bool
) -> None:
    """Les six formes qu'un logo peut prendre, toutes ramenées à 8 bits.

    Palette et transparence sont DÉVELOPPÉES en RVB plus alpha : le PDF reçoit
    la même forme quel que soit le fichier d'origine, et un seul chemin de code
    est à éprouver en aval.
    """
    image = images.verifier_un_logo(octets)
    assert image.espace == espace, nom
    assert (image.alpha is not None) is attend_alpha, nom
    canaux = 1 if espace == "DeviceGray" else 3
    assert len(image.couleur) == image.largeur * image.hauteur * canaux
    if attend_alpha:
        assert image.alpha is not None
        assert len(image.alpha) == image.largeur * image.hauteur


@pytest.mark.parametrize(
    ("nom", "octets"),
    [("gris", fixtures.gris_avec_trns()), ("couleurs vraies", fixtures.rvb_avec_trns())],
)
def test_la_transparence_par_couleur_est_appliquee(nom: str, octets: bytes) -> None:
    """`tRNS` ne vaut pas que pour une palette, et l'ignorer se voit.

    Un PNG en gris ou en couleurs vraies peut déclarer UNE valeur transparente.
    Collecter le morceau sans l'appliquer faisait perdre au logo sa
    transparence en silence — un aplat opaque là où l'entreprise avait dessiné
    du vide, sur tous ses devis, sans qu'aucun message ne le dise.
    """
    image = images.verifier_un_logo(octets)
    assert image.alpha is not None, nom
    assert any(valeur == 0 for valeur in image.alpha), nom
    assert any(valeur == 255 for valeur in image.alpha), nom


def test_les_seize_bits_retiennent_l_octet_de_poids_fort() -> None:
    """65535 devient 255, 32768 devient 128 — pas une valeur approchée."""
    image = images.verifier_un_logo(fixtures.seize_bits())
    assert image.couleur[:3] == bytes([255, 0, 128])


def test_un_svg_deguise_en_png_est_refuse_sur_son_contenu() -> None:
    """L'extension ne décide de rien : la signature, si.

    Un SVG est un document XML exécutable. Accepté ici, il serait ensuite servi
    à des navigateurs sous l'origine de l'application — le script qu'il porte
    lirait le jeton de session. Ce refus est le seul qui compte, et il ne
    regarde pas le nom du fichier.
    """
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(fixtures.SVG_DEGUISE)
    assert refus.value.code == "format_non_supporte"


def test_un_png_entrelace_est_refuse_en_disant_quoi_faire() -> None:
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(fixtures.entrelace())
    assert refus.value.code == "png_entrelace"
    assert "entrelacement" in refus.value.message


def test_un_png_tronque_ne_fait_pas_lire_au_dela_de_la_fin() -> None:
    entier = fixtures.carre()
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(entier[: len(entier) // 2])
    assert refus.value.code in {"png_tronque", "png_illisible", "png_sans_donnees"}


def test_une_image_trop_petite_est_refusee_avec_ses_dimensions() -> None:
    minuscule = fixtures.png(
        largeur=8, hauteur=8, type_couleur=2, profondeur=8, lignes=[bytes(24)] * 8
    )
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(minuscule)
    assert refus.value.code == "image_trop_petite"
    assert refus.value.context["width"] == 8


def test_un_fichier_trop_volumineux_est_refuse_avant_tout_decodage() -> None:
    """Le plafond se lit sur la taille du fichier, pas sur l'image développée.

    Refuser après décompression obligerait à développer en mémoire ce qu'on
    s'apprête à rejeter — exactement ce contre quoi le plafond existe.
    """
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(b"\x89PNG\r\n\x1a\n" + b"\x00" * (images.OCTETS_MAXIMUM + 1))
    assert refus.value.code == "fichier_trop_volumineux"


def test_une_bombe_de_decompression_est_refusee_sans_la_developper() -> None:
    """Le plafond sur le FICHIER ne borne pas la mémoire. La borne est ailleurs.

    Mesuré : 204 Ko d'IDAT portent 200 Mio de zéros, et le pire cas sous le
    plafond de 2 Mio en porte deux gigaoctets. La décompression est donc bornée
    à ce que l'en-tête — déjà validé — autorise, et un reste non consommé
    prouve que les données mentent sur les dimensions.

    Ce test ne mesure pas le temps ni la mémoire : il vérifie le REFUS, qui est
    ce qui rend l'un et l'autre impossibles.
    """
    bombe = fixtures.bombe_de_decompression()
    assert len(bombe) < images.OCTETS_MAXIMUM, "la bombe doit passer sous le plafond de taille"
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(bombe)
    assert refus.value.code == "png_incoherent"


def test_la_route_refuse_la_bombe_et_ne_laisse_rien(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    """Bout en bout : 422, aucun fichier, aucun logo enregistré."""
    reponse = seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.bombe_de_decompression(), "image/png")},
    )
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == "png_incoherent"
    assert _logos(racine) == []
    assert seeded_client.get("/api/v1/organization", headers=admin).json()["logo"] is None


def test_une_image_au_dela_du_plafond_de_pixels_est_refusee() -> None:
    """Le coût du défiltrage est linéaire en pixels : il doit être borné.

    Mesuré : quatre millions de pixels occupaient le décodeur plus de dix
    secondes pour un fichier de trente kilooctets — des lignes constantes se
    compriment presque à néant. Le plafond de pixels ramène le pire cas sous
    la seconde et demie, et la route s'exécute désormais hors de la boucle
    d'événements pour que ce temps ne gèle personne.
    """
    trop = fixtures.png(
        largeur=1600,
        hauteur=1600,
        type_couleur=2,
        profondeur=8,
        lignes=[bytes(1600 * 3)] * 1600,
    )
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(trop)
    assert refus.value.code == "image_trop_grande"
    assert refus.value.context["width"] == 1600


@pytest.mark.skipif(
    not running_on_postgresql(),
    reason=(
        "La sérialisation repose sur un verrou de LIGNE. SQLite n'en a pas — "
        "il verrouille la base entière au moment d'écrire, ce qui ne recouvre "
        "pas la fenêtre entre la lecture de l'ancienne clé et l'écriture de la "
        "nouvelle. Éprouver la garantie là où elle n'existe pas ne prouverait "
        "rien sur le moteur de production."
    ),
)
def test_deux_poses_concurrentes_ne_laissent_qu_un_fichier(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    """Un double-clic sur « Enregistrer » ne doit pas laisser d'orphelin.

    Sans verrou, deux poses simultanées lisent la même ancienne clé, écrivent
    chacune leur fichier, et retirent toutes deux l'ancienne. La base n'en
    retient qu'une ; l'autre n'est plus désignée par personne, et aucun
    remplacement futur ne la retirera jamais — la sauvegarde l'emporte, et
    l'écrit de purge ne sait pas le nommer.
    """
    import threading

    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("origine.png", fixtures.carre(), "image/png")},
    )
    assert len(_logos(racine)) == 1

    depart = threading.Barrier(2)
    codes: list[int] = []

    def poser(couleur: int) -> None:
        depart.wait()
        reponse = seeded_client.put(
            "/api/v1/organization/logo",
            headers=admin,
            files={"file": ("neuf.png", fixtures.carre(cote=32 + couleur), "image/png")},
        )
        codes.append(reponse.status_code)

    fils = [threading.Thread(target=poser, args=(i,)) for i in range(2)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    assert all(code in {200, 409, 500} for code in codes), codes
    restants = _logos(racine)
    assert len(restants) <= 1, f"{len(restants)} fichiers pour une seule ligne : {restants}"


def test_un_fichier_vide_est_refuse() -> None:
    with pytest.raises(images.ImageRefusee) as refus:
        images.verifier_un_logo(b"")
    assert refus.value.code == "fichier_vide"


# --------------------------------------------------------------------------
# 2. L'API : le profil, ses permissions, son audit
# --------------------------------------------------------------------------


def test_le_profil_se_lit_et_se_modifie(seeded_client: TestClient, admin: dict[str, str]) -> None:
    avant = seeded_client.get("/api/v1/organization", headers=admin)
    assert avant.status_code == 200, avant.text
    assert avant.json()["missing_for_issue"] == []

    modif = seeded_client.patch(
        "/api/v1/organization",
        headers=admin,
        json={
            "address": "Avenue Fictive 99",
            "address_complement": "Boîte 4",
            "postal_code": "1000",
            "city": "Bruxelles",
            "email": "devis@fictif.demo",
            "phone": "+32 2 000 00 00",
            "website": "https://fictif.demo",
        },
    )
    assert modif.status_code == 200, modif.text
    profil = modif.json()
    assert profil["address"] == "Avenue Fictive 99"
    assert profil["address_complement"] == "Boîte 4"
    assert profil["city"] == "Bruxelles"
    assert profil["missing_for_issue"] == []


def test_un_champ_vide_efface_au_lieu_d_etre_ignore(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Retirer un site web qu'on n'a plus doit se faire depuis l'écran."""
    seeded_client.patch(
        "/api/v1/organization", headers=admin, json={"website": "https://fictif.demo"}
    )
    vide = seeded_client.patch("/api/v1/organization", headers=admin, json={"website": ""})
    assert vide.status_code == 200, vide.text
    assert vide.json()["website"] is None


def test_vider_l_adresse_reapparait_dans_les_manques(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """La liste des manques est CALCULÉE, jamais mémorisée."""
    reponse = seeded_client.patch(
        "/api/v1/organization", headers=admin, json={"address": "", "city": ""}
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["missing_for_issue"] == ["address", "city"]


def test_le_nom_ne_peut_pas_devenir_vide(seeded_client: TestClient, admin: dict[str, str]) -> None:
    """Une organisation sans nom ne s'imprime nulle part."""
    refus = seeded_client.patch("/api/v1/organization", headers=admin, json={"name": ""})
    assert refus.status_code == 422, refus.text


def test_modifier_le_profil_est_journalise(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    seeded_client.patch("/api/v1/organization", headers=admin, json={"city": "Liège"})
    journal = seeded_client.get("/api/v1/audit/events", headers=admin).json()
    actions = [e["action"] for e in journal["items"]]
    assert "organization.profile.updated" in actions


def test_un_metreur_ne_modifie_pas_le_profil(seeded_client: TestClient) -> None:
    metreur = login(seeded_client, "metreur@dubois.demo")
    refus = seeded_client.patch("/api/v1/organization", headers=metreur, json={"city": "Anvers"})
    assert refus.status_code == 403, refus.text
    assert refus.json()["detail"]["required_permission"] == "org:manage"


# --------------------------------------------------------------------------
# 3. Le logo : ce qui atteint le disque, et ce qui n'y reste pas
# --------------------------------------------------------------------------


def test_charger_un_logo_le_decrit_sans_dire_ou_il_est(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    """Le chemin interne ne sort JAMAIS : le rendre inviterait à le demander."""
    octets = fixtures.carre(alpha=True)
    reponse = seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", octets, "image/png")},
    )
    assert reponse.status_code == 200, reponse.text
    logo = reponse.json()["logo"]
    assert logo["width"] == 64 and logo["height"] == 64
    assert logo["media_type"] == "image/png"
    assert logo["byte_size"] == len(octets)
    assert len(logo["sha256"]) == 64
    assert "storage_key" not in reponse.text
    assert "logos/" not in reponse.text
    assert len(_logos(racine)) == 1


def test_le_logo_se_relit_avec_son_type_reel(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    octets = fixtures.horizontal()
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("bandeau.png", octets, "image/png")},
    )
    lu = seeded_client.get("/api/v1/organization/logo", headers=admin)
    assert lu.status_code == 200
    assert lu.headers["content-type"] == "image/png"
    assert lu.headers["x-content-type-options"] == "nosniff"
    assert lu.content == octets


def test_sans_logo_la_route_repond_404(seeded_client: TestClient, admin: dict[str, str]) -> None:
    absent = seeded_client.get("/api/v1/organization/logo", headers=admin)
    assert absent.status_code == 404
    assert absent.json()["detail"]["code"] == "no_logo"


def test_un_fichier_refuse_ne_laisse_rien_sur_le_volume(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    """La validation précède l'écriture : il n'y a pas de résidu à nettoyer."""
    refus = seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.SVG_DEGUISE, "image/png")},
    )
    assert refus.status_code == 422, refus.text
    assert refus.json()["detail"]["code"] == "format_non_supporte"
    assert _logos(racine) == []
    assert seeded_client.get("/api/v1/organization", headers=admin).json()["logo"] is None


def test_remplacer_un_logo_retire_l_ancien_du_volume(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    """Un seul fichier par organisation après un remplacement, jamais deux.

    L'ancien ne part qu'APRÈS la validation : c'est ce que `achever` garantit,
    et c'est ce qui empêche une transaction échouée de détruire le logo
    courant.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("un.png", fixtures.carre(), "image/png")},
    )
    premier = _logos(racine)
    assert len(premier) == 1

    remplacant = fixtures.horizontal()
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("deux.png", remplacant, "image/png")},
    )
    apres = _logos(racine)
    assert len(apres) == 1, [p.name for p in apres]
    assert apres[0] != premier[0]
    assert apres[0].read_bytes() == remplacant


def test_retirer_le_logo_efface_la_ligne_et_le_fichier(
    seeded_client: TestClient, admin: dict[str, str], racine: Path
) -> None:
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    assert len(_logos(racine)) == 1
    retire = seeded_client.delete("/api/v1/organization/logo", headers=admin)
    assert retire.status_code == 200, retire.text
    assert retire.json()["logo"] is None
    assert _logos(racine) == []


def test_retirer_un_logo_absent_n_est_pas_une_erreur(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Pour qui clique, « je n'en veux plus » est la même chose des deux côtés."""
    reponse = seeded_client.delete("/api/v1/organization/logo", headers=admin)
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["logo"] is None


def test_un_metreur_ne_charge_ni_ne_retire_de_logo(seeded_client: TestClient) -> None:
    metreur = login(seeded_client, "metreur@dubois.demo")
    charge = seeded_client.put(
        "/api/v1/organization/logo",
        headers=metreur,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    assert charge.status_code == 403, charge.text
    retire = seeded_client.delete("/api/v1/organization/logo", headers=metreur)
    assert retire.status_code == 403, retire.text


def test_le_logo_d_une_autre_organisation_est_inatteignable(
    seeded_client: TestClient, admin: dict[str, str]
) -> None:
    """Il n'y a pas d'identifiant dans le chemin : rien à forger.

    C'est la forme la plus sûre d'isolation — non pas un contrôle qu'on
    pourrait oublier, mais l'absence de la porte qu'il faudrait garder. Chacun
    ne peut demander que le logo de SA session.
    """
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("dubois.png", fixtures.carre(), "image/png")},
    )
    voisin = login(seeded_client, "admin@janssens.demo")
    assert seeded_client.get("/api/v1/organization/logo", headers=voisin).status_code == 404
    assert seeded_client.get("/api/v1/organization", headers=voisin).json()["logo"] is None
    # Et celui de Dubois est toujours là, intact.
    assert seeded_client.get("/api/v1/organization/logo", headers=admin).status_code == 200


def test_charger_un_logo_est_journalise(seeded_client: TestClient, admin: dict[str, str]) -> None:
    seeded_client.put(
        "/api/v1/organization/logo",
        headers=admin,
        files={"file": ("logo.png", fixtures.carre(), "image/png")},
    )
    seeded_client.delete("/api/v1/organization/logo", headers=admin)
    journal = seeded_client.get("/api/v1/audit/events", headers=admin).json()
    actions = [e["action"] for e in journal["items"]]
    assert "organization.logo.updated" in actions
    assert "organization.logo.removed" in actions
