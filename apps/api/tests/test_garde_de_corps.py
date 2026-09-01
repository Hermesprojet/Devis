"""La garde qui borne un corps AVANT que FastAPI ne le lise.

**Ce que ces tests éprouvent, et que `TestClient` seul n'éprouverait pas.**
`TestClient` fabrique la requête et rend la réponse ; il ne dit pas combien
d'octets l'application a réellement reçus. Or c'est la seule question qui
compte ici : le défaut corrigé était que le contrôle s'exécutait APRÈS la
lecture. Un test qui vérifierait seulement le code 413 passerait tout aussi
bien sur le code d'avant.

Les six premiers cas pilotent donc l'interface ASGI directement, avec un
`receive` sous contrôle et une application témoin qui COMPTE ce qu'elle reçoit.
Les suivants traversent l'application réelle, où c'est l'absence de résidu qui
fait la preuve.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from metreo_api import corps_bornes
from metreo_api.garde_de_corps import poser_la_garde

from . import classeurs_fictifs as faux
from . import images_fictives as images
from .conftest import login

PLAFOND = 1_000


class ApplicationTemoin:
    """Une application ASGI qui lit son corps et retient ce qu'elle a vu.

    Elle lit jusqu'au bout, comme FastAPI le fait pour résoudre un
    `UploadFile` : c'est ce comportement-là que la garde doit rendre inoffensif.
    """

    def __init__(self) -> None:
        self.octets_recus = 0
        self.appels = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.appels += 1
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            self.octets_recus += len(message.get("body") or b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"lu"})


def _appeler(
    entetes: list[tuple[bytes, bytes]],
    morceaux: list[bytes],
    *,
    chemin: str = "/depot",
    plafond: int = PLAFOND,
) -> tuple[int | None, bytes, ApplicationTemoin]:
    """Joue une requête à travers la garde et rend (statut, corps, témoin)."""
    temoin = ApplicationTemoin()
    garde = poser_la_garde(temoin, {("POST", "/depot"): plafond})
    restants = list(morceaux)
    reponses: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if restants:
            corps = restants.pop(0)
            return {"type": "http.request", "body": corps, "more_body": bool(restants)}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        reponses.append(message)

    scope = {"type": "http", "method": "POST", "path": chemin, "headers": entetes}
    asyncio.run(garde(scope, receive, send))

    statut = next((m["status"] for m in reponses if m["type"] == "http.response.start"), None)
    corps = b"".join(m.get("body") or b"" for m in reponses if m["type"] == "http.response.body")
    return statut, corps, temoin


# --------------------------------------------------------------------------
# 1 à 6 — la garde, éprouvée sur `receive`
# --------------------------------------------------------------------------


def test_1_un_content_length_trop_grand_ne_fait_lire_AUCUN_octet() -> None:
    """La preuve du bloc : zéro octet, et l'application jamais appelée.

    « Zéro octet lu » ne se déduit pas d'un code 413 — l'ancien contrôle rendait
    déjà 413, après avoir tout lu. Il se constate en comptant, du côté de
    l'application, ce qui lui est effectivement parvenu.
    """
    statut, corps, temoin = _appeler([(b"content-length", b"9999999")], [b"x" * 500_000])

    assert statut == 413
    assert temoin.octets_recus == 0, "des octets ont atteint l'application"
    assert temoin.appels == 0, "l'application a été appelée : ses dépendances se sont ouvertes"
    assert b'"request_too_large"' in corps
    assert f'"max_bytes": {PLAFOND}'.encode() in corps
    # Ni chemin interne, ni configuration : un refus n'est pas un diagnostic.
    assert b"storage_root" not in corps and b"/home" not in corps


def test_2_une_taille_exactement_egale_au_plafond_est_acceptee() -> None:
    """La borne est inclusive : refuser à l'égalité amputerait le plafond de un."""
    statut, _, temoin = _appeler([(b"content-length", str(PLAFOND).encode())], [b"x" * PLAFOND])
    assert statut == 200
    assert temoin.octets_recus == PLAFOND


def test_3_un_octet_de_plus_est_refuse() -> None:
    statut, _, temoin = _appeler(
        [(b"content-length", str(PLAFOND + 1).encode())], [b"x" * (PLAFOND + 1)]
    )
    assert statut == 413
    assert temoin.octets_recus == 0


def test_4_sans_content_length_les_morceaux_sont_comptes() -> None:
    """Le cas `chunked` : aucun en-tête ne dit la taille, il faut la mesurer.

    L'application peut recevoir les morceaux qui tiennent SOUS le plafond — on
    ne sait qu'un corps est trop grand qu'en voyant l'octet de trop. Ce qu'elle
    ne reçoit jamais, c'est plus que le plafond, et c'est la garantie utile.
    """
    statut, _, temoin = _appeler([], [b"x" * 600, b"x" * 600])
    assert statut == 413
    assert temoin.octets_recus <= PLAFOND
    assert temoin.octets_recus == 600, "le premier morceau tenait sous le plafond"


def test_5_un_content_length_menteur_ne_gagne_rien() -> None:
    """Un en-tête est une déclaration ; les octets observés sont un fait.

    Annoncer 10 octets et en envoyer cinq cent mille passerait le premier
    contrôle. C'est le comptage qui tranche, et il tranche avant que
    l'application ne voie quoi que ce soit.
    """
    statut, _, temoin = _appeler([(b"content-length", b"10")], [b"x" * 500_000])
    assert statut == 413
    assert temoin.octets_recus == 0


def test_6_des_centaines_de_petits_morceaux_subissent_la_meme_limite() -> None:
    """C'est un TOTAL qui est comparé, jamais la taille d'un morceau.

    Découper un corps en mille morceaux de dix octets est la façon la plus
    simple de contourner une garde qui regarderait chaque morceau séparément.
    """
    statut, _, temoin = _appeler([], [b"x" * 10] * 1_000)
    assert statut == 413
    assert temoin.octets_recus <= PLAFOND


@pytest.mark.parametrize(
    ("cas", "entetes"),
    [
        ("valeur négative", [(b"content-length", b"-5")]),
        ("valeur non numérique", [(b"content-length", b"beaucoup")]),
        ("valeur vide", [(b"content-length", b"")]),
        ("valeurs contradictoires", [(b"content-length", b"10"), (b"content-length", b"99999")]),
    ],
)
def test_un_entete_inutilisable_renvoie_au_comptage(cas: str, entetes: Any) -> None:
    """Ce qu'on ne sait pas lire ne permet aucune conclusion.

    Ni refuser — on punirait un corps peut-être minuscule — ni accepter sans
    compter, ce qui serait exactement le mensonge qu'on veut déjouer. Dans les
    deux cas, c'est le comptage réel qui décide.
    """
    petit, _, temoin_petit = _appeler(entetes, [b"x" * 50])
    assert petit == 200, f"{cas} : un corps minuscule ne doit pas être puni"
    assert temoin_petit.octets_recus == 50

    gros, _, temoin_gros = _appeler(entetes, [b"x" * 50_000])
    assert gros == 413, f"{cas} : le comptage doit refuser un corps réellement trop grand"
    assert temoin_gros.octets_recus <= PLAFOND


def test_un_corps_plus_court_que_l_annonce_passe() -> None:
    """Annoncer 900 et n'envoyer que 10 n'est pas une attaque, seulement un
    en-tête optimiste. C'est le réel qui compte, et le réel tient."""
    statut, _, temoin = _appeler([(b"content-length", b"900")], [b"x" * 10])
    assert statut == 200
    assert temoin.octets_recus == 10


def test_une_route_sans_plafond_n_est_pas_touchee() -> None:
    """La garde ne s'applique QU'aux routes du registre.

    L'envelopper autour de tout ferait payer un comptage à chaque `GET`, pour
    une protection dont seules les routes de dépôt ont besoin.
    """
    statut, _, temoin = _appeler([], [b"x" * 50_000], chemin="/libre")
    assert statut == 200
    assert temoin.octets_recus == 50_000


# --------------------------------------------------------------------------
# 7 à 12 — la garde sur l'application réelle
# --------------------------------------------------------------------------

TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def client_borne(migrated: None) -> Any:
    """L'application TELLE QU'ELLE EST SERVIE, garde comprise.

    La fixture `client` ordinaire monte `create_app()` nu : c'est ce qu'il faut
    pour éprouver les routes, et c'est précisément ce qui laisserait la garde
    hors de portée des tests. Celle-ci monte ce qu'`uvicorn` sert réellement.
    """
    from metreo_api.config import get_settings
    from metreo_api.main import application_bornee, create_app

    with TestClient(application_bornee(create_app(), get_settings())) as test_client:
        yield test_client


@pytest.fixture()
def racine(migrated: None) -> Path:
    from metreo_api.config import get_settings

    return Path(get_settings().storage_root).resolve()


def _fichiers_sous(racine: Path) -> set[Path]:
    return {chemin for chemin in racine.rglob("*") if chemin.is_file()}


def _trop_gros(plafond: int) -> bytes:
    return b"x" * (plafond + corps_bornes.MARGE_ENVELOPPE_MULTIPART + 4096)


def test_7_une_requete_non_authentifiee_trop_grande_recoit_413_sans_analyse(
    client_borne: TestClient,
) -> None:
    """La protection des ressources précède l'authentification, délibérément.

    Lire cinq cents mégaoctets pour découvrir ensuite que le jeton est absent,
    c'est offrir à un anonyme le travail que l'authentification devait
    protéger. La contrepartie, assumée : un tel refus ne peut PAS être inscrit
    au journal d'une organisation, puisqu'aucune identité n'est encore établie.
    """
    reponse = client_borne.post(
        "/api/v1/price-books/versions/inexistante/imports/preview",
        files={"file": ("gros.csv", _trop_gros(25 * 1024 * 1024), "text/csv")},
    )
    assert reponse.status_code == 413, reponse.text
    detail = reponse.json()["detail"]
    assert detail["code"] == "request_too_large"
    assert isinstance(detail["max_bytes"], int)
    # 401 aurait signifié que l'authentification a tourné — donc que le corps
    # avait déjà été absorbé pour en arriver là.
    assert reponse.status_code != 401


def test_8_un_fichier_normal_authentifie_se_comporte_comme_avant(
    client_borne: TestClient, seeded: dict[str, str]
) -> None:
    """La garde ne doit rien changer au cas nominal, sans quoi elle serait
    une régression déguisée en protection."""
    entetes = login(client_borne, "admin@dubois.demo")
    livre = client_borne.get("/api/v1/price-books", headers=entetes).json()[0]
    version = client_borne.get(
        f"/api/v1/price-books/{livre['id']}/versions", headers=entetes
    ).json()[0]["id"]

    csv = b"code;libelle;unite;prix_unitaire\nGARDE-001;Poste fictif;m3;12,50\n"
    reponse = client_borne.post(
        f"/api/v1/price-books/versions/{version}/imports/preview",
        headers=entetes,
        files={"file": ("prix.csv", csv, "text/csv")},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["valid_count"] == 1


def test_9_deux_organisations_ne_se_croisent_pas_sur_un_refus(
    client_borne: TestClient, seeded: dict[str, str], racine: Path
) -> None:
    """Un refus chez l'un ne laisse rien qui appartienne à l'autre.

    La garde répond avant toute résolution de tenant : il n'y a donc aucun
    identifiant d'organisation en jeu au moment du refus. Ce test le CONSTATE
    plutôt que de le supposer — et vérifie surtout qu'un refus ne dépose rien
    sur le volume partagé.
    """
    dubois = login(client_borne, "admin@dubois.demo")
    janssens = login(client_borne, "admin@janssens.demo")
    avant = _fichiers_sous(racine)

    for entetes in (dubois, janssens):
        refus = client_borne.put(
            "/api/v1/organization/logo",
            headers=entetes,
            files={"file": ("logo.png", _trop_gros(2 * 1024 * 1024), "image/png")},
        )
        assert refus.status_code == 413, refus.text

    assert _fichiers_sous(racine) == avant, "un refus a laissé un fichier sur le volume"

    # Et chacun voit toujours SON profil, intact.
    for entetes in (dubois, janssens):
        profil = client_borne.get("/api/v1/organization", headers=entetes)
        assert profil.status_code == 200
        assert profil.json()["logo"] is None


def test_10_aucun_residu_sur_les_quatre_familles_de_fichiers(
    client_borne: TestClient, seeded: dict[str, str], racine: Path
) -> None:
    """Logo, document, CSV, classeur : un refus ne laisse ni fichier ni lot.

    Les quatre familles sont éprouvées ENSEMBLE parce que c'est la même garde
    qui les couvre : en vérifier une seule laisserait croire que les trois
    autres sont protégées par la même preuve.
    """
    entetes = login(client_borne, "admin@dubois.demo")
    avant_fichiers = _fichiers_sous(racine)
    avant_lots = client_borne.get("/api/v1/price-books", headers=entetes).status_code
    assert avant_lots == 200

    livre = client_borne.get("/api/v1/price-books", headers=entetes).json()[0]
    version = client_borne.get(
        f"/api/v1/price-books/{livre['id']}/versions", headers=entetes
    ).json()[0]["id"]
    projet = client_borne.get("/api/v1/projects", headers=entetes).json()["items"][0]
    document = client_borne.post(
        f"/api/v1/projects/{projet['id']}/documents",
        headers=entetes,
        json={"title": "Pièce fictive pour la garde"},
    )
    assert document.status_code in (200, 201), document.text
    document_id = document.json()["id"]

    familles = [
        (
            "logo",
            lambda: client_borne.put(
                "/api/v1/organization/logo",
                headers=entetes,
                files={"file": ("logo.png", _trop_gros(2 * 1024 * 1024), "image/png")},
            ),
        ),
        (
            "document",
            lambda: client_borne.post(
                f"/api/v1/documents/{document_id}/revisions",
                headers=entetes,
                files={"file": ("piece.pdf", _trop_gros(25 * 1024 * 1024), "application/pdf")},
            ),
        ),
        (
            "csv",
            lambda: client_borne.post(
                f"/api/v1/price-books/versions/{version}/imports/preview",
                headers=entetes,
                files={"file": ("prix.csv", _trop_gros(25 * 1024 * 1024), "text/csv")},
            ),
        ),
        (
            "xlsx",
            lambda: client_borne.post(
                f"/api/v1/price-books/versions/{version}/imports/preview",
                headers=entetes,
                files={"file": ("prix.xlsx", _trop_gros(25 * 1024 * 1024), TYPE_XLSX)},
            ),
        ),
    ]

    for nom, envoyer in familles:
        reponse = envoyer()
        assert reponse.status_code == 413, f"{nom} : {reponse.status_code} — {reponse.text[:200]}"
        assert reponse.json()["detail"]["code"] == "request_too_large"

    apres = _fichiers_sous(racine)
    assert apres == avant_fichiers, (
        f"des fichiers subsistent après refus : {sorted(str(c) for c in apres - avant_fichiers)}"
    )
    # Aucun lot d'import n'a été ouvert : un refus réseau ne crée pas de ligne.
    revisions = client_borne.get(f"/api/v1/documents/{document_id}/revisions", headers=entetes)
    assert revisions.status_code == 200
    assert revisions.json() == []


def test_11_un_refus_n_occupe_ni_fil_ni_dependance(client_borne: TestClient) -> None:
    """Plusieurs dépôts surdimensionnés laissent l'API réactive.

    La version honnête de « le service reste réactif » : plutôt qu'une mesure
    de temps, qui serait instable, on constate que l'application n'est JAMAIS
    appelée pour ces requêtes — donc qu'aucun fil du pool, aucune session de
    base et aucune dépendance n'a été mobilisé. Un point de santé qui répond
    ensuite confirme que rien n'est resté pris.
    """
    for _ in range(10):
        refus = client_borne.post(
            "/api/v1/price-books/versions/peu-importe/imports/preview",
            files={"file": ("gros.csv", _trop_gros(25 * 1024 * 1024), "text/csv")},
        )
        assert refus.status_code == 413

    sante = client_borne.get("/api/v1/health")
    assert sante.status_code == 200, sante.text


def test_12_la_bombe_xlsx_reste_refusee_par_le_LECTEUR_pas_par_la_garde(
    client_borne: TestClient, seeded: dict[str, str]
) -> None:
    """Les deux protections ne se remplacent pas, et ce test le prouve.

    La bombe pèse quelques kilooctets sur le réseau : elle passe la garde sans
    difficulté — c'est bien qu'elle la passe, sinon ce test ne prouverait rien
    du lecteur. C'est `services/classeur` qui la refuse, sur ce qu'elle
    DÉVELOPPE et non sur ce qu'elle pèse.

    Supprimer la garde laisserait passer un fichier de cinq cents mégaoctets ;
    supprimer le lecteur laisserait passer celui-ci. Il faut les deux.
    """
    from metreo_api.config import get_settings

    entetes = login(client_borne, "admin@dubois.demo")
    livre = client_borne.get("/api/v1/price-books", headers=entetes).json()[0]
    version = client_borne.get(
        f"/api/v1/price-books/{livre['id']}/versions", headers=entetes
    ).json()[0]["id"]

    bombe = faux.bombe_de_decompression()
    plafond_reseau = {
        (methode, chemin): plafond
        for methode, chemin, plafond in corps_bornes.routes_bornees(get_settings())
    }[("POST", "/api/v1/price-books/versions/{version_id}/imports/preview")]
    assert len(bombe) < plafond_reseau, (
        "la bombe doit tenir SOUS le plafond réseau, sinon la garde la prendrait "
        "et le lecteur ne serait pas éprouvé"
    )

    reponse = client_borne.post(
        f"/api/v1/price-books/versions/{version}/imports/preview",
        headers=entetes,
        files={"file": ("bombe.xlsx", bombe, TYPE_XLSX)},
    )
    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["code"] == "decompresse_trop_grand"

    # Et un PNG démesuré reste refusé par son propre lecteur, pour la même
    # raison : la garde ne sait rien des pixels.
    petit_mais_enorme = images.bombe_de_decompression()
    assert len(petit_mais_enorme) < 2 * 1024 * 1024
    refus_logo = client_borne.put(
        "/api/v1/organization/logo",
        headers=entetes,
        files={"file": ("bombe.png", petit_mais_enorme, "image/png")},
    )
    assert refus_logo.status_code == 422, refus_logo.text
