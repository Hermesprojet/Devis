"""Le cache des tuiles : sa clé, ses deux plafonds, et ce qu'il laisse passer.

**Ce que ces tests protègent.** Une tuile est un dérivé : rien en base ne la
connaît, elle coûte cher à produire et elle pèse lourd sur le volume. Trois
propriétés sont donc vérifiées ici, et chacune se casserait en silence :

1. **la clé est arrondie** — sans cela, deux clics à un pixel d'écart
   produisent deux tuiles distinctes et le cache ne sert jamais ;
2. **un plafond atteint n'empêche pas de mesurer** — il arrête l'ÉCRITURE, pas
   le service, et la différence est toute la différence pour qui mesure ;
3. **la purge sait les retrouver** — sinon elles survivent à la révision dont
   la clé est leur seul lien.

Le plafond en OCTETS est le plus important des deux, et c'est celui que la
mesure a imposé : une tuile pèse de 26 à 246 Ko selon la densité du dessin
(ADR 0008), donc un plafond en nombre seul laisse passer un facteur dix.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from metreo_api.services import tuiles
from metreo_api.services.document_storage import StockageLocal

#: Un PNG minuscule mais VALIDE : signature, puis un IHDR qui annonce 7 × 5.
#: Fabriqué à la main pour que `_dimensions_du_png` soit vérifié sur des octets
#: dont la réponse attendue est connue, et non sur ce qu'un rendu a produit.
_PNG_7x5 = (
    b"\x89PNG\r\n\x1a\n"
    + (13).to_bytes(4, "big")
    + b"IHDR"
    + (7).to_bytes(4, "big")
    + (5).to_bytes(4, "big")
    + b"\x08\x06\x00\x00\x00"
)


@pytest.fixture()
def stockage(tmp_path: Path) -> StockageLocal:
    return StockageLocal(tmp_path)


def _poser_des_tuiles(
    stockage: StockageLocal, *, nombre: int, octets_chacune: int, revision: str = "rev-1"
) -> None:
    """Pose `nombre` fausses tuiles d'un poids choisi, par le vrai chemin.

    Par `ecrire_octets`, et non par un `write_bytes` direct : le comptage lit
    le dossier que le stockage a construit, et écrire à côté vérifierait une
    convention de nommage inventée pour le test.
    """
    for rang in range(nombre):
        stockage.ecrire_octets(
            organization_id="org-1",
            dossier=tuiles.DOSSIER_TUILES,
            identifiant=f"{revision}-p1-{rang:06d}",
            extension=".png",
            contenu=b"\x00" * octets_chacune,
            media_type="image/png",
        )


def test_the_key_rounds_the_zone_so_two_nearby_clicks_share_a_tile() -> None:
    """Deux zones distantes d'un cent-millième de page ont la MÊME clé.

    C'est la condition pour que le cache serve à quelque chose : sur un aperçu
    de deux mille pixels, un cent-millième de page est un centième de pixel, et
    deux clics humains n'ont aucune raison de tomber sur le même flottant.
    """
    premiere = tuiles.cle_de_la_tuile("org-1", "rev-1", 1, (0.40, 0.40, 0.45, 0.45))
    voisine = tuiles.cle_de_la_tuile("org-1", "rev-1", 1, (0.400001, 0.40, 0.45, 0.45))
    assert premiere == voisine


def test_the_key_separates_pages_zones_revisions_and_organizations() -> None:
    """Ce qui doit distinguer deux tuiles les distingue bien.

    L'organisation et la révision vivent dans le CHEMIN, la page et la zone
    dans l'empreinte. Les quatre sont vérifiés ensemble parce qu'une clé qui
    confond deux d'entre eux affiche la mauvaise zone — ou, pire, la zone d'un
    autre document.
    """
    reference = tuiles.cle_de_la_tuile("org-1", "rev-1", 1, (0.10, 0.10, 0.15, 0.15))
    assert reference != tuiles.cle_de_la_tuile("org-1", "rev-1", 2, (0.10, 0.10, 0.15, 0.15))
    assert reference != tuiles.cle_de_la_tuile("org-1", "rev-1", 1, (0.20, 0.10, 0.25, 0.15))
    assert reference != tuiles.cle_de_la_tuile("org-1", "rev-2", 1, (0.10, 0.10, 0.15, 0.15))
    assert reference != tuiles.cle_de_la_tuile("org-2", "rev-1", 1, (0.10, 0.10, 0.15, 0.15))


def test_a_cached_tile_is_served_without_rendering(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La tuile déjà sur le volume est rendue telle quelle, sans lancer le fils.

    Le rendu est remplacé par une explosion : si la fonction était appelée, le
    test échouerait au lieu de passer en silence sur un cache inutile. C'est
    tout l'enjeu — mesuré, le fils coûte jusqu'à 5,3 secondes, la lecture du
    volume 0,3 milliseconde.
    """
    zone = (0.40, 0.40, 0.45, 0.45)
    cle = tuiles.cle_de_la_tuile("org-1", "rev-1", 1, zone)
    chemin = stockage.chemin(cle)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(_PNG_7x5)

    def _interdit(*_: object, **__: object) -> None:
        raise AssertionError("le fils a été lancé alors que la tuile était en cache")

    monkeypatch.setattr(tuiles, "_rendre_hors_processus", _interdit)

    tuile = tuiles.obtenir(
        stockage,
        organization_id="org-1",
        revision_id="rev-1",
        original=Path("/inexistant.pdf"),
        page=1,
        zone=zone,
    )
    assert tuile.depuis_le_cache is True
    assert (tuile.largeur, tuile.hauteur) == (7, 5)
    # Inconnue depuis le cache, et annoncée comme telle plutôt que devinée :
    # elle dépend de la hauteur du texte d'origine, qui n'est pas dans le PNG.
    assert tuile.hauteur_du_texte_px == 0.0


def test_the_byte_ceiling_stops_writing_but_still_serves_the_tile(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Volume plein : la mesure reste possible, elle n'est plus mise en cache.

    Le plafond en octets est atteint avec DIX tuiles d'un poids gonflé, loin
    des cinq cents du plafond en nombre : c'est exactement le cas que le
    plafond en nombre ne voit pas, et la raison pour laquelle il y en a deux.
    """
    gros = tuiles.PLAFOND_D_OCTETS_PAR_REVISION // 10
    _poser_des_tuiles(stockage, nombre=10, octets_chacune=gros)

    zone = (0.40, 0.40, 0.45, 0.45)
    monkeypatch.setattr(
        tuiles,
        "_rendre_hors_processus",
        lambda original, *, page, zone, sortie: (
            sortie.write_bytes(_PNG_7x5),
            (7, 5, 18.0),
        )[1],
    )

    with caplog.at_level(logging.INFO, logger="metreo.api"):
        tuile = tuiles.obtenir(
            stockage,
            organization_id="org-1",
            revision_id="rev-1",
            original=Path("/peu-importe.pdf"),
            page=1,
            zone=zone,
        )

    # Servie, et avec sa hauteur de texte : le propriétaire peut relire sa cote.
    assert tuile.png == _PNG_7x5
    assert tuile.hauteur_du_texte_px == 18.0
    assert tuile.depuis_le_cache is False
    # Mais PAS écrite : la demande suivante repayera le rendu.
    assert stockage.taille(tuiles.cle_de_la_tuile("org-1", "rev-1", 1, zone)) is None
    # Et dite, parce qu'un volume qui se remplit doit être visible avant d'être
    # plein. Sans rien du contenu du plan.
    assert any(
        enregistrement.message == "cache_de_tuiles_plein" for enregistrement in caplog.records
    )


def test_the_count_ceiling_also_stops_writing(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beaucoup de tuiles minuscules : le garde-fou en nombre joue quand même.

    Cinq cents tuiles d'un octet ne pèsent rien, donc le plafond en octets ne
    les verrait pas. Le garde-fou en nombre existe pour ce cas, et pour ne pas
    laisser un dossier accumuler des dizaines de milliers d'entrées.
    """
    _poser_des_tuiles(stockage, nombre=tuiles.PLAFOND_DE_TUILES_PAR_REVISION, octets_chacune=1)
    zone = (0.40, 0.40, 0.45, 0.45)
    monkeypatch.setattr(
        tuiles,
        "_rendre_hors_processus",
        lambda original, *, page, zone, sortie: (
            sortie.write_bytes(_PNG_7x5),
            (7, 5, 18.0),
        )[1],
    )
    tuile = tuiles.obtenir(
        stockage,
        organization_id="org-1",
        revision_id="rev-1",
        original=Path("/peu-importe.pdf"),
        page=1,
        zone=zone,
    )
    assert tuile.png == _PNG_7x5
    assert stockage.taille(tuiles.cle_de_la_tuile("org-1", "rev-1", 1, zone)) is None


def test_a_tile_below_both_ceilings_is_written_to_the_volume(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cas ordinaire : rendue une fois, relue ensuite.

    Vérifié en deux appels, et c'est le second qui compte : il doit venir du
    cache, ce qui prouve que le premier a bien écrit sous la clé que le second
    calcule.
    """
    appels = {"nombre": 0}

    def _rendre(original: Path, *, page: int, zone: tuple[float, ...], sortie: Path):
        appels["nombre"] += 1
        sortie.write_bytes(_PNG_7x5)
        return 7, 5, 18.0

    monkeypatch.setattr(tuiles, "_rendre_hors_processus", _rendre)
    zone = (0.40, 0.40, 0.45, 0.45)
    arguments = {
        "organization_id": "org-1",
        "revision_id": "rev-1",
        "original": Path("/peu-importe.pdf"),
        "page": 1,
        "zone": zone,
    }
    premiere = tuiles.obtenir(stockage, **arguments)
    seconde = tuiles.obtenir(stockage, **arguments)
    assert premiere.depuis_le_cache is False
    assert seconde.depuis_le_cache is True
    assert appels["nombre"] == 1


def test_the_purge_can_list_a_revisions_tiles_and_only_its_own(
    stockage: StockageLocal,
) -> None:
    """Les clés d'une révision, sans celles de sa voisine.

    Une tuile n'a aucune ligne en base : cette liste est le seul moyen pour
    `conservation.py` de savoir quoi effacer. Si elle débordait sur une autre
    révision, la purge de l'une effacerait les dérivés de l'autre.
    """
    _poser_des_tuiles(stockage, nombre=3, octets_chacune=10, revision="rev-1")
    _poser_des_tuiles(stockage, nombre=2, octets_chacune=10, revision="rev-2")
    cles = tuiles.cles_des_tuiles(stockage, "org-1", "rev-1")
    assert len(cles) == 3
    assert all("/rev-1-p" in cle for cle in cles)


def test_listing_tiles_of_an_organization_without_any_returns_nothing(
    stockage: StockageLocal,
) -> None:
    """Un dossier absent n'est pas une erreur : c'est zéro tuile.

    La purge tourne aussi pour une organisation qui n'a jamais ouvert de plan,
    et une exception y transformerait un effacement normal en incident.
    """
    assert tuiles.cles_des_tuiles(stockage, "org-jamais-vue", "rev-1") == []


def test_a_refusal_from_the_child_is_reported_with_its_own_code(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le code du fils traverse, au lieu d'un « échec du rendu » générique.

    C'est la différence entre « ce PDF est chiffré » et « ça n'a pas marché ».
    Le premier se corrige par qui dépose le fichier ; le second fait ouvrir un
    ticket.
    """

    class _Resultat:
        returncode = 3
        stdout = ""
        stderr = "pdf_chiffre\n"

    monkeypatch.setattr(tuiles.subprocess, "run", lambda *a, **k: _Resultat())
    with pytest.raises(tuiles.TuileRefusee) as refus:
        tuiles.obtenir(
            stockage,
            organization_id="org-1",
            revision_id="rev-1",
            original=Path("/peu-importe.pdf"),
            page=1,
            zone=(0.40, 0.40, 0.45, 0.45),
        )
    assert refus.value.code == "rendu_impossible"
    assert "pdf_chiffre" in refus.value.message


def test_a_child_that_never_finishes_is_killed_and_said_so(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le délai maximal existe parce qu'un fils bloqué retient 500 Mo.

    Pas « parce que c'est propre » : un processus arrêté au milieu d'un rendu
    garde sa page de PDF en mémoire aussi longtemps qu'il vit, et rien ne le
    tuerait sans ce délai.
    """

    def _bloque(*_: object, **__: object) -> None:
        raise tuiles.subprocess.TimeoutExpired(
            cmd="rendu", timeout=tuiles.DELAI_MAXIMAL_EN_SECONDES
        )

    monkeypatch.setattr(tuiles.subprocess, "run", _bloque)
    with pytest.raises(tuiles.TuileRefusee) as refus:
        tuiles.obtenir(
            stockage,
            organization_id="org-1",
            revision_id="rev-1",
            original=Path("/peu-importe.pdf"),
            page=1,
            zone=(0.40, 0.40, 0.45, 0.45),
        )
    assert refus.value.code == "rendu_trop_long"


def test_a_png_that_is_not_one_reports_no_dimensions_instead_of_guessing(
    stockage: StockageLocal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Des octets qui ne sont pas un PNG donnent 0 × 0, pas une exception.

    Le cas arrive si un fichier du volume est tronqué par un disque plein. Zéro
    est faux mais visible ; une exception ferait échouer la lecture d'une tuile
    au lieu de la signaler.
    """
    zone = (0.40, 0.40, 0.45, 0.45)
    chemin = stockage.chemin(tuiles.cle_de_la_tuile("org-1", "rev-1", 1, zone))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(b"pas un png")
    tuile = tuiles.obtenir(
        stockage,
        organization_id="org-1",
        revision_id="rev-1",
        original=Path("/peu-importe.pdf"),
        page=1,
        zone=zone,
    )
    assert (tuile.largeur, tuile.hauteur) == (0, 0)
