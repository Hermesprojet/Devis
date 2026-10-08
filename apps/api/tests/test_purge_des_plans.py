"""Détruire une organisation détruit AUSSI les plans qu'elle avait déposés.

**Le défaut que ce fichier ferme.** `conservation.documents_a_detruire`
inscrivait au registre les PDF de devis, les logos, et — depuis la lecture de
plans — les artefacts dérivés d'un plan. Elle n'inscrivait **pas les
ORIGINAUX**, c'est-à-dire `document_revisions.storage_key`. Or `executer`
supprime la ligne de la révision : la clé partait avec elle, et le fichier
restait sur le volume **sans qu'aucune ligne ne le désigne plus**.

Le défaut est antérieur à la lecture de plans. Ce qui a changé est ce que ces
fichiers contiennent : un import de prix orphelin est un déchet, un plan
d'exécution client orphelin est une fuite — et le propriétaire a posé comme
règle permanente que les plans de ses clients sont confidentiels.

**Ce que ces tests regardent, et qui n'est pas la base.** Ils recensent les
fichiers RÉELLEMENT présents sous la racine de stockage, avant et après. Un
test qui se contenterait de compter des lignes passerait exactement comme le
code passait avant la correction.

Aucune suppression n'est faite ailleurs que sous la racine jetable du test :
ces tests ne touchent aucune machine.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from metreo_api.config import get_settings
from metreo_api.db import get_session_factory
from metreo_api.models import OrganizationPurge
from metreo_api.services import conservation
from metreo_api.services.document_storage import StockageLocal

from .conftest import login

pytest.importorskip(
    "pypdfium2",
    reason="l'extra « pdf » n'est pas installé : pip install './apps/api[pdf]'",
)

import fabriquer_pdf_de_test as pdf_fixtures

ADMIN = "admin@dubois.demo"
#: Le second jeu semé par le dépôt : une autre organisation, un autre compte.
AUTRE = "admin@janssens.demo"


def _stockage() -> StockageLocal:
    return StockageLocal(get_settings().storage_root)


def _fichiers_sous(racine: Path) -> list[Path]:
    return sorted(p for p in racine.rglob("*") if p.is_file())


def _deposer_un_plan(
    client: TestClient,
    entetes: dict[str, str],
    reference: str,
    *,
    revisions: int = 1,
    analyser: bool = True,
) -> tuple[str, list[str]]:
    """Un chantier, un document, et `revisions` révisions de PDF déposées.

    Plusieurs révisions parce que c'est le cas réel : un plan est redessiné, et
    chaque version laisse SON fichier sur le volume. Un registre qui n'en
    nommerait qu'une — la dernière, par exemple — laisserait les autres.
    """
    projet = client.post(
        "/api/v1/projects",
        headers=entetes,
        json={"reference": reference, "name": f"Chantier {reference}"},
    )
    assert projet.status_code == 201, projet.text
    document = client.post(
        f"/api/v1/projects/{projet.json()['id']}/documents",
        headers=entetes,
        json={"title": "Plan de façade"},
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]

    identifiants: list[str] = []
    for numero in range(revisions):
        # Des octets DIFFÉRENTS à chaque révision : deux fichiers identiques
        # pourraient partager une clé de stockage, et le test ne prouverait
        # plus qu'il y en a bien deux à détruire.
        octets = pdf_fixtures.page_avec_plusieurs_textes(
            placements=(
                *pdf_fixtures.PLACEMENTS_DU_PLAN,
                (f"IND {numero}", 200.0, 200.0),
            )
        )
        revision = client.post(
            f"/api/v1/documents/{document_id}/revisions",
            headers=entetes,
            files={"file": (f"facade-{numero}.pdf", octets, "application/pdf")},
        )
        assert revision.status_code == 201, revision.text
        revision_id = revision.json()["id"]
        identifiants.append(revision_id)

        if analyser:
            analyse = client.post(
                f"/api/v1/documents/{document_id}/revisions/{revision_id}/plan/analyse",
                headers=entetes,
            )
            assert analyse.status_code == 200, analyse.text

    return document_id, identifiants


def _purger(organization_id: str, reference: str) -> OrganizationPurge:
    """Le cycle complet : demander, autoriser, détruire les lignes, puis les fichiers.

    `sans_retention` est la porte qu'emprunte `seed --reset`. La durée de
    conservation n'est pas le sujet ici — ce qui reste sur le volume l'est.
    """
    stockage = _stockage()
    with get_session_factory()() as session:
        purge = conservation.demander(
            session,
            organization_id=organization_id,
            reason_code="test_fixture",
            reference=reference,
            sans_retention=True,
            stockage=stockage,
        )
        identifiant = purge.id
        conservation.autoriser(session, purge)
        conservation.executer(session, purge)
        session.commit()

    with get_session_factory()() as session:
        purge = session.get(OrganizationPurge, identifiant)
        assert purge is not None
        conservation.retirer_les_fichiers(session, purge, stockage)
        session.commit()
        session.refresh(purge)
        session.expunge(purge)
        return purge


# ---------------------------------------------------------------------------
# L'original
# ---------------------------------------------------------------------------


def test_le_registre_nomme_l_original_de_chaque_revision(seeded_client: TestClient) -> None:
    """Le défaut exact : `document_revisions.storage_key` manquait à l'écrit.

    Le registre doit NOMMER ce qu'il va détruire, et survivre à la destruction.
    Un fichier non nommé survit sans que l'écrit puisse dire lequel.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-1", revisions=2, analyser=False)

    with get_session_factory()() as session:
        inscrits = conservation.documents_a_detruire(session, organisation, _stockage())

    originaux = [d for d in inscrits if "(original)" in d.number]
    assert len(originaux) == 2, [d.number for d in originaux]
    for original in originaux:
        assert original.storage_key
        assert original.sha256, "l'empreinte du dépôt fait du registre un écrit vérifiable"


def test_une_purge_ne_laisse_aucun_plan_sur_le_volume(seeded_client: TestClient) -> None:
    """Le test qui aurait rougi : il recense les FICHIERS, pas les lignes.

    Deux révisions analysées posent, chacune, son original plus ses dérivés —
    constat, textes, et un aperçu PNG par page. Rien ne doit rester.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-2", revisions=2)

    racine = _stockage().racine
    avant = _fichiers_sous(racine)
    assert len(avant) >= 6, (
        "deux révisions analysées devraient poser au moins deux originaux et "
        f"leurs dérivés : {[p.name for p in avant]}"
    )

    purge = _purger(organisation, "DOSSIER-PLAN-2026-001")

    assert purge.status == "completed", purge.files_failed
    assert purge.files_failed == []
    restants = _fichiers_sous(racine)
    assert restants == [], f"la purge a laissé {len(restants)} fichier(s) : {restants}"


def test_l_original_est_inscrit_avant_ses_derives(seeded_client: TestClient) -> None:
    """L'ordre compte, et il n'est pas cosmétique.

    `retirer_les_fichiers` parcourt le registre dans l'ordre. Une interruption
    doit laisser des dérivés sans original — refaisables depuis la sauvegarde,
    et sans valeur seuls — plutôt qu'un original sans ses dérivés, qui est
    exactement le fichier confidentiel qu'on voulait détruire.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _, revisions = _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-3")

    with get_session_factory()() as session:
        inscrits = conservation.documents_a_detruire(session, organisation, _stockage())

    pour_la_revision = [d for d in inscrits if d.quote_id == revisions[0]]
    assert pour_la_revision, "la révision n'a rien inscrit"
    assert "(original)" in pour_la_revision[0].number, [d.number for d in pour_la_revision]
    assert len(pour_la_revision) > 1, "un plan analysé pose aussi des dérivés"


def test_un_plan_jamais_analyse_laisse_quand_meme_son_original(
    seeded_client: TestClient,
) -> None:
    """Le cas le plus fréquent, et celui qu'on oublie.

    La plupart des documents déposés ne sont jamais analysés : ils n'ont aucun
    dérivé, donc aucune des clés que la correction précédente inscrivait. Si
    l'original n'était pas inscrit, ces documents-là ne laisseraient RIEN au
    registre — et tout sur le volume.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-4", analyser=False)

    racine = _stockage().racine
    assert _fichiers_sous(racine), "le dépôt a bien posé un fichier"

    purge = _purger(organisation, "DOSSIER-PLAN-2026-002")

    assert purge.status == "completed"
    assert _fichiers_sous(racine) == []


# ---------------------------------------------------------------------------
# L'isolation entre organisations
# ---------------------------------------------------------------------------


def test_purger_une_organisation_ne_touche_pas_aux_plans_d_une_autre(
    seeded_client: TestClient,
) -> None:
    """La propriété la plus grave à casser, et la moins visible.

    Les clés de stockage portent l'identifiant de l'organisation dans leur
    CHEMIN. Un registre qui les collecterait par un parcours du volume plutôt
    que par une requête portée par `organization_id` emporterait les plans du
    voisin — et personne ne s'en apercevrait avant que le voisin n'ouvre son
    écran.
    """
    premier = login(seeded_client, ADMIN)
    second = login(seeded_client, AUTRE)
    organisation_a = seeded_client.get("/api/v1/auth/me", headers=premier).json()["organization_id"]
    organisation_b = seeded_client.get("/api/v1/auth/me", headers=second).json()["organization_id"]
    assert organisation_a != organisation_b

    _deposer_un_plan(seeded_client, premier, "PURGE-ISOLATION-A")
    _deposer_un_plan(seeded_client, second, "PURGE-ISOLATION-B")

    racine = _stockage().racine
    de_b_avant = [p for p in _fichiers_sous(racine) if organisation_b in str(p)]
    assert de_b_avant, "la seconde organisation a bien posé des fichiers"

    # Le registre d'A ne doit nommer AUCUN fichier de B — c'est là que la
    # fuite se produirait, avant même la suppression.
    with get_session_factory()() as session:
        inscrits = conservation.documents_a_detruire(session, organisation_a, _stockage())
    assert inscrits, "l'organisation A a bien quelque chose à détruire"
    assert all(organisation_b not in d.storage_key for d in inscrits), [
        d.storage_key for d in inscrits if organisation_b in d.storage_key
    ]

    purge = _purger(organisation_a, "DOSSIER-ISOLATION-2026")
    assert purge.status == "completed"

    de_a_apres = [p for p in _fichiers_sous(racine) if organisation_a in str(p)]
    de_b_apres = [p for p in _fichiers_sous(racine) if organisation_b in str(p)]
    assert de_a_apres == [], f"l'organisation purgée a laissé {de_a_apres}"
    assert de_b_apres == de_b_avant, "la purge d'une organisation a touché aux fichiers d'une autre"


def test_le_registre_d_une_purge_de_plans_ne_conserve_aucun_nom_de_fichier_utilisateur(
    seeded_client: TestClient,
) -> None:
    """L'écrit survit à l'organisation : il ne doit pas la décrire.

    Le registre nomme des CLÉS de stockage et des numéros de révision. Le nom
    que l'utilisateur avait donné à son fichier — qui porte souvent celui du
    client ou du chantier — n'y entre pas, et ne doit pas y entrer : cet écrit
    est précisément ce qui subsiste après l'effacement.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-5", analyser=False)

    with get_session_factory()() as session:
        inscrits = conservation.documents_a_detruire(session, organisation, _stockage())

    ecrit = " ".join(f"{d.number} {d.storage_key} {d.sha256}" for d in inscrits)
    assert "facade-0.pdf" not in ecrit
    assert "Plan de façade" not in ecrit
    assert "Chantier" not in ecrit


def test_la_decision_de_conservation_reste_exigee_pour_un_plan(
    seeded_client: TestClient,
) -> None:
    """Déposer des plans ne crée aucune porte de sortie.

    `sans_retention` est employé par les tests ci-dessus pour éprouver le
    VOLUME ; la règle ordinaire, elle, continue de s'appliquer : sans décision
    de conservation écrite, rien ne se détruit.
    """
    entetes = login(seeded_client, ADMIN)
    organisation = seeded_client.get("/api/v1/auth/me", headers=entetes).json()["organization_id"]
    _deposer_un_plan(seeded_client, entetes, "PURGE-PLAN-6", analyser=False)

    with get_session_factory()() as session, pytest.raises(conservation.PurgeRefusee) as refus:
        conservation.demander(
            session,
            organization_id=organisation,
            reason_code="test_fixture",
            reference="DOSSIER-SANS-DECISION",
            stockage=_stockage(),
        )
    assert refus.value.code

    # Et avec une décision échue, la demande passe — la règle n'est pas un mur,
    # c'est un écrit préalable.
    with get_session_factory()() as session:
        conservation.decider(
            session,
            organization_id=organisation,
            years=0,
            jurisdiction="BE-WAL",
            source_label="Source fictive de recette — aucune valeur juridique",
            source_checked_on=date(2026, 1, 15),
            effective_from=date(2026, 1, 1),
        )
        session.commit()
    with get_session_factory()() as session:
        purge = conservation.demander(
            session,
            organization_id=organisation,
            reason_code="test_fixture",
            reference="DOSSIER-AVEC-DECISION",
            stockage=_stockage(),
        )
        assert any("(original)" in str(d["number"]) for d in purge.documents)
        session.rollback()
