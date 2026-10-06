"""Une citation de plan est-elle stockable, et reste-t-elle RÉSOLUBLE ?

Ces tests parlent SQL. Ils écrivent directement par l'ORM, sans route ni
service, parce que la base est la dernière frontière : une contrainte qui ne
mord pas ici ne mord nulle part, et c'est elle qui survivra au prochain
service écrit par quelqu'un qui n'a pas lu la docstring.

**Ce que la révision `d8e9fa010203` a rendu possible, et le trou qu'elle
ouvrait.** `page`, la plage de caractères et la boîte englobante sont devenues
nullables, parce qu'une cotation de DXF n'a ni page ni caractères. Mais une
contrainte CHECK est satisfaite quand son expression vaut NULL — sur SQLite
comme sur PostgreSQL — et la tolérance au NULL laissait donc passer une boîte
englobante à MOITIÉ écrite : `x0` posé, `x1` absent, `x0 < x1` indéterminé,
donc accepté. Six des tests ci-dessous visent précisément les bords de cette
nullabilité, du côté où elle devait rester fermée.

Un seul test de ce fichier a besoin d'ezdxf — celui qui rouvre le fichier
cité. Sa garde est donc POSÉE DANS CE TEST, et non en tête de module : une
garde de module ferait taire les neuf preuves SQL, qui ne dépendent d'aucun
extra, le jour où `plans` n'est pas installé.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from metreo_api.db import get_session_factory
from metreo_api.models import DocumentStepRun, SourceCitation
from metreo_api.services.documents import DOCUMENT_PIPELINE_STEPS

from .test_document_foundation import (
    Graph,
    _add_graph,
    _citation,
    _commit_is_refused,
    _step,
)


@pytest.fixture()
def db_session(migrated: None) -> Iterator[Session]:
    """La même session que `test_document_foundation`, déclarée ici.

    Les FABRIQUES sont importées — c'est ce qui ne doit pas être recopié, et
    c'est tout le propos du partage. Une fixture, elle, ne s'importe pas : son
    nom entre dans l'espace du module, puis chaque test le reçoit en
    paramètre, ce qui en fait une redéfinition que le lint refuse. Six lignes
    déclarées ici valent mieux qu'une dérogation au lint sur tout le fichier.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


#: La révision qui a rendu les trois colonnes de texte nullables. Elle est
#: cherchée par son identifiant et non par son nom de fichier : un fichier
#: renommé laisserait ce contrôle muet au lieu de le faire échouer.
REVISION_DES_CITATIONS_CAO = "d8e9fa010203"

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Le calque, la feuille et le handle d'une cotation, tels qu'un plan les
#: porte. `object_id` est un handle DXF : du hexadécimal, court et stable.
FEUILLE = "Model"
CALQUE = "COTATIONS"
HANDLE = "2F3A"


def _citation_de_plan(graph: Graph, **changes: object) -> SourceCitation:
    """Une citation ancrée sur un objet de dessin, et sur rien de textuel.

    Les sept colonnes de texte sont explicitement `None` : c'est le sujet. La
    fabrique partagée `_citation` les renseigne toutes, parce qu'elle a été
    écrite pour une citation de PDF.
    """
    valeurs: dict[str, object] = {
        "page": None,
        "char_start": None,
        "char_end": None,
        "x0": None,
        "y0": None,
        "x1": None,
        "y1": None,
        "sheet": FEUILLE,
        "layer": CALQUE,
        "object_id": HANDLE,
        "extractor": "lecture_dxf@ezdxf-1.4.4",
    }
    valeurs.update(changes)
    return _citation(graph, **valeurs)


# ---------------------------------------------------------------------------
# Ce que la nullabilité a rendu possible
# ---------------------------------------------------------------------------


def test_a_cad_citation_is_stored_without_a_page_or_a_character_range(
    db_session: Session,
) -> None:
    """Une cotation de DXF n'a ni page ni caractères, et ne doit pas en inventer.

    L'autre solution — écrire `page = 1` et une plage factice — ne coûtait
    aucune migration. Elle écrirait en base une provenance FAUSSE,
    indiscernable après coup d'une citation de texte dont on aurait perdu le
    texte. Ce test fixe que la base accepte l'absence au lieu de l'exiger.
    """
    graph = _add_graph(db_session, label="CAO-OK")
    citation = _citation_de_plan(graph)

    db_session.add(citation)
    db_session.commit()

    relue = db_session.get(SourceCitation, citation.id)
    assert relue is not None
    assert (relue.page, relue.char_start, relue.char_end) == (None, None, None)
    assert (relue.x0, relue.y0, relue.x1, relue.y1) == (None, None, None, None)
    assert (relue.sheet, relue.layer, relue.object_id) == (FEUILLE, CALQUE, HANDLE)


def test_a_cad_citation_reopens_on_its_sheet_layer_and_object_handle(
    db_session: Session, tmp_path: Path
) -> None:
    """**Le test que l'autre solution rendait impossible à écrire.**

    Une citation n'est une preuve que si l'on peut la ROUVRIR et retrouver ce
    qu'elle désigne. Avec une page inventée, il n'y aurait rien à l'adresse
    enregistrée : « page 1, caractères 0 à 1 » d'un fichier de dessin ne
    désigne aucun objet, et le contrôle se réduirait à relire ce qu'on vient
    d'écrire.

    Ici l'adresse est celle du fichier : feuille, calque, handle. Le test la
    relit en SQL, puis rouvre le DXF et vérifie que la même cotation y est —
    même handle, même calque. C'est la boucle complète.
    """
    ezdxf = pytest.importorskip(
        "ezdxf",
        reason="l'extra « plans » n'est pas installé : pip install './apps/api[plans]'",
    )
    from metreo_api.services import lecture_dxf

    document = ezdxf.new("R2013", setup=True)
    document.header["$INSUNITS"] = 4
    cote = document.modelspace().add_linear_dim(
        base=(0, -1000), p1=(0, 0), p2=(5000, 0), dxfattribs={"layer": CALQUE}
    )
    # Sans `render()`, la cotation n'a pas de bloc géométrique et l'audit la
    # retire au rechargement : l'espace modèle reviendrait vide.
    cote.render()
    chemin = tmp_path / "citee.dxf"
    document.saveas(chemin)

    constat = lecture_dxf.lire(chemin)
    (lue,) = constat.cotations
    assert lue.object_ref, "sans handle, il n'y a pas d'adresse à citer"

    graph = _add_graph(db_session, label="CAO-ROUVRE")
    citation = _citation_de_plan(
        graph,
        sheet=constat.feuilles[0],
        layer=lue.calque,
        object_id=lue.object_ref,
    )
    db_session.add(citation)
    db_session.commit()
    # Vidée de l'identité : ce qui est relu vient de la base, pas du cache.
    db_session.expunge_all()

    relue = db_session.scalars(select(SourceCitation).where(SourceCitation.id == citation.id)).one()
    assert relue.sheet == constat.feuilles[0]
    assert relue.layer == CALQUE
    assert relue.object_id == lue.object_ref
    assert (relue.page, relue.char_start, relue.char_end) == (None, None, None)
    assert (relue.x0, relue.y0, relue.x1, relue.y1) == (None, None, None, None)

    # Et maintenant la seule assertion qui compte : à cette adresse, dans ce
    # fichier, il y a bien l'objet cité.
    a_nouveau = lecture_dxf.lire(chemin)
    retrouvees = [
        cotation
        for cotation in a_nouveau.cotations
        if cotation.object_ref == relue.object_id and cotation.calque == relue.layer
    ]
    assert len(retrouvees) == 1, (
        "l'adresse enregistrée ne désigne plus rien dans le fichier : "
        f"handles présents = {[c.object_ref for c in a_nouveau.cotations]}"
    )
    assert relue.sheet in a_nouveau.feuilles


# ---------------------------------------------------------------------------
# Ce que la nullabilité ne doit PAS avoir ouvert
# ---------------------------------------------------------------------------


def test_a_citation_anchored_on_nothing_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """Une provenance vide qui passe pour une provenance est le pire des trois.

    Pire qu'une page inventée, pire qu'un refus : elle promet qu'on peut
    vérifier, et il n'y a rien à vérifier. `ck_source_citation_ancrage` exige
    donc l'un des deux ancrages.
    """
    graph = _add_graph(db_session, label="CAO-VIDE")
    _commit_is_refused(
        db_session,
        _citation_de_plan(graph, sheet=None, layer=None, object_id=None),
    )


def test_a_half_written_character_range_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """Un début de plage sans fin ne désigne pas un passage.

    L'ancrage CAO reste valide dans cette ligne — handle, calque, feuille :
    le refus porte donc UNIQUEMENT sur la plage amputée, et non sur l'absence
    d'ancrage. Sans cette précaution, le test passerait pour la mauvaise
    raison.
    """
    graph = _add_graph(db_session, label="CAO-PLAGE")
    _commit_is_refused(db_session, _citation_de_plan(graph, page=3, char_start=10, char_end=None))


def test_a_character_range_without_a_page_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """« Caractères 0 à 12 » de quel document ? La page fait partie de l'adresse.

    Une plage complète mais flottante serait irrésoluble : rien ne dirait où
    commencer à compter.
    """
    graph = _add_graph(db_session, label="CAO-SANS-PAGE")
    _commit_is_refused(
        db_session,
        _citation_de_plan(
            graph,
            page=None,
            char_start=0,
            char_end=12,
            sheet=None,
            layer=None,
            object_id=None,
        ),
    )


def test_a_half_written_bounding_box_is_refused_by_the_database(
    db_session: Session,
) -> None:
    """**Le trou qu'ouvrait la tolérance au NULL d'une contrainte CHECK.**

    `ck_source_citation_bbox` compare `x0 < x1`. Avec `x1` absent, la
    comparaison vaut NULL, donc la contrainte est SATISFAITE : une boîte à
    moitié écrite passait. Un surlignage posé sur une telle boîte n'aurait ni
    largeur ni position, et rien n'aurait signalé l'anomalie.

    `ck_source_citation_bbox_complete` est écrite uniquement en IS NULL /
    IS NOT NULL, et c'est ce qui la fait mordre là où l'autre s'efface.
    """
    graph = _add_graph(db_session, label="CAO-BOITE")
    _commit_is_refused(
        db_session,
        _citation_de_plan(
            graph,
            x0=Decimal("0.1"),
            y0=Decimal("0.2"),
            x1=None,
            y1=Decimal("0.9"),
        ),
    )


def test_an_empty_object_handle_is_refused_by_the_database(db_session: Session) -> None:
    """Un handle vide n'est pas NULL, et c'est tout le problème.

    `object_id IS NOT NULL` est satisfait par une chaîne vide : l'ancrage
    passerait alors en ne désignant rien. `ck_source_citation_reperes_cao_nonempty`
    exige une longueur non nulle après `trim`.
    """
    graph = _add_graph(db_session, label="CAO-HANDLE-VIDE")
    _commit_is_refused(db_session, _citation_de_plan(graph, object_id=""))


def test_a_textual_citation_still_refuses_a_page_below_one(db_session: Session) -> None:
    """La nullabilité n'a rien affaibli : `page = 0` reste refusé.

    C'est la contrepartie exacte de la décision de ne PAS réécrire les quatre
    contraintes de valeur. « page >= 1 » laisse passer une page absente —
    NULL — et continue de refuser une page zéro, qui n'existe dans aucun
    document.
    """
    graph = _add_graph(db_session, label="TEXTE-PAGE-0")
    _commit_is_refused(
        db_session,
        _citation_de_plan(
            graph,
            page=0,
            char_start=0,
            char_end=12,
            x0=Decimal("0.1"),
            y0=Decimal("0.2"),
            x1=Decimal("0.8"),
            y1=Decimal("0.9"),
        ),
    )


# ---------------------------------------------------------------------------
# Les bornes exactes, là où un décimal stocké en TEXTE se compare mal
# ---------------------------------------------------------------------------


def test_a_cad_citation_whose_confidence_is_exactly_one_is_accepted(
    db_session: Session,
) -> None:
    """**Constaté par l'exécution : la confiance 1 était refusée sur SQLite.**

    `CONFIANCE_DE_LA_CITATION` vaut 1, et pour une raison : un handle DXF
    désigne un objet sans ambiguïté — il n'y a pas de « peut-être cet objet ».
    C'est l'EMPLACEMENT qui est certain, pas la valeur qu'on en tire.

    Or `Amount` stocke un décimal en TEXTE sous SQLite, qui n'a pas de type
    décimal. Dans `confidence <= 1`, la colonne a l'affinité TEXT et le
    littéral n'en a aucune : SQLite applique alors TEXT au littéral, et
    compare les CHAÎNES. `'1.0000000000' <= '1'` est faux, alors que
    `'0.9500000000' <= '1'` est vrai — la contrainte refusait donc la seule
    valeur certaine, et toute lecture de plan tombait en `IntegrityError`.
    Reproduit avant le `CAST(confidence AS NUMERIC)` qui la corrige.

    Le refus de `1.0000000001` est dans le même test exprès : sans lui, une
    contrainte simplement supprimée passerait au vert.
    """
    graph = _add_graph(db_session, label="CAO-CONFIANCE")
    db_session.add(_citation_de_plan(graph, confidence=Decimal("1")))
    db_session.commit()

    _commit_is_refused(db_session, _citation_de_plan(graph, confidence=Decimal("1.0000000001")))
    _commit_is_refused(db_session, _citation_de_plan(graph, confidence=Decimal("-0.1")))


def test_a_citation_box_that_touches_the_image_border_is_accepted(
    db_session: Session,
) -> None:
    """Une cotation qui traverse tout le plan a un cadre de 0 à 1, exactement.

    Ce n'est pas un cas limite théorique : `lecture_dxf` normalise la boîte
    d'une cotation sur l'étendue du dessin, et une cotation qui COTE la
    largeur du plan en définit précisément le bord — mesuré,
    `Cadre(x0=0.0, …, x1=1.0, …)`. Les bornes sont donc atteintes en usage
    normal, et la contrainte doit les accepter.

    Même piège que la confiance : stockée en TEXTE, `x1 <= 1` se comparait
    comme une chaîne et refusait `'1.0000000000'`. Une cotation de bord en
    bord n'aurait donc jamais pu être citée — et c'est la plus utile de
    toutes.
    """
    graph = _add_graph(db_session, label="CAO-BORD")
    db_session.add(
        _citation_de_plan(
            graph,
            x0=Decimal("0"),
            y0=Decimal("0"),
            x1=Decimal("1"),
            y1=Decimal("1"),
        )
    )
    db_session.commit()

    # Hors du cadre, et le cadre inversé : les deux restent refusés.
    _commit_is_refused(
        db_session,
        _citation_de_plan(
            graph,
            x0=Decimal("0"),
            y0=Decimal("0"),
            x1=Decimal("1.5"),
            y1=Decimal("1"),
        ),
    )
    _commit_is_refused(
        db_session,
        _citation_de_plan(
            graph,
            x0=Decimal("0.8"),
            y0=Decimal("0"),
            x1=Decimal("0.2"),
            y1=Decimal("1"),
        ),
    )


# ---------------------------------------------------------------------------
# Les quatre étapes de plan
# ---------------------------------------------------------------------------


def test_the_four_plan_pipeline_steps_are_accepted_and_an_invented_one_is_not(
    db_session: Session,
) -> None:
    """Aucune des onze étapes de texte ne convenait à un plan.

    Une liste `IN` ne se complète pas : il faut la refaire, et c'est le seul
    endroit du dépôt où une contrainte CHECK existante est modifiée dans un
    `upgrade`. Ce test vérifie que les quatre nouvelles passent réellement en
    SQL — et que la liste reste une liste : « dxf », nom plausible et absent,
    est refusé.
    """
    graph = _add_graph(db_session, label="ETAPES-PLAN")
    for etape in ("page_render", "vector_geometry", "cad_read", "measurement"):
        db_session.add(_step(graph, step=etape))
        db_session.commit()

    posees = set(
        db_session.scalars(
            select(DocumentStepRun.step).where(DocumentStepRun.revision_id == graph.revision.id)
        ).all()
    )
    assert {"page_render", "vector_geometry", "cad_read", "measurement"} <= posees

    _commit_is_refused(db_session, _step(graph, step="dxf"))


def _etapes_citees(texte: str) -> list[str]:
    """Les noms d'étape d'une clause `step IN (...)`, dans leur ordre d'écriture."""
    return re.findall(r"'([a-z_]+)'", texte)


def _contrainte_des_etapes_du_modele() -> str:
    from sqlalchemy import CheckConstraint

    for element in DocumentStepRun.__table_args__:
        if isinstance(element, CheckConstraint) and element.name == "ck_document_step_run_step":
            return str(element.sqltext)
    raise AssertionError("ck_document_step_run_step a disparu de DocumentStepRun")


def _etapes_de_la_migration() -> tuple[str, ...]:
    """Les étapes telles que la migration les écrit, lues DANS la migration.

    Le module expose un tuple `ETAPES` nommé : il est chargé et lu, plutôt
    qu'extrait du texte source à l'expression régulière. Une migration
    s'importe sans contexte Alembic — elle ne fait que définir des fonctions —
    et c'est ce qui rend cette lecture propre.
    """
    # La DÉCLARATION `revision = "..."`, en début de ligne, et non n'importe
    # quelle occurrence de l'identifiant : toute migration enfant le cite dans
    # son `down_revision`, et chercher la chaîne nue trouverait donc autant de
    # fichiers que la révision a d'enfants.
    declaration = re.compile(
        rf'^revision(\s*:\s*str)?\s*=\s*"{re.escape(REVISION_DES_CITATIONS_CAO)}"\s*$',
        re.MULTILINE,
    )
    candidats = [
        chemin
        for chemin in sorted(VERSIONS.glob("*.py"))
        if declaration.search(chemin.read_text(encoding="utf-8"))
    ]
    assert len(candidats) == 1, (
        f"la révision {REVISION_DES_CITATIONS_CAO} n'est pas déclarée dans "
        f"exactement un fichier de migration : {[c.name for c in candidats]}"
    )
    specification = importlib.util.spec_from_file_location(
        f"migration_{REVISION_DES_CITATIONS_CAO}", candidats[0]
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    etapes: tuple[str, ...] = module.ETAPES
    return etapes


def test_the_service_step_list_and_the_database_check_name_the_same_steps() -> None:
    """**La liste des étapes vit à TROIS endroits, et ce test les confronte.**

    Le service (`DOCUMENT_PIPELINE_STEPS`), la contrainte du modèle
    (`ck_document_step_run_step`) et celle de la migration `d8e9fa010203`.
    Elles ne sont pas redondantes : `claim_step_run` refuse AVANT la base, et
    oublier le service rendrait les quatre étapes de plan inatteignables par
    l'API tout en étant parfaitement acceptées en SQL — une divergence
    silencieuse, visible seulement à l'usage.

    Les trois sont comparées comme des ENSEMBLES de noms, et la migration est
    lue dans la migration : recopier les quinze noms ici créerait une
    quatrième liste à maintenir, c'est-à-dire exactement le défaut que ce test
    prétend fermer.
    """
    du_modele = _etapes_citees(_contrainte_des_etapes_du_modele())
    de_la_migration = list(_etapes_de_la_migration())

    assert len(du_modele) == 15, du_modele
    assert set(du_modele) == DOCUMENT_PIPELINE_STEPS
    assert set(de_la_migration) == DOCUMENT_PIPELINE_STEPS
    # Doublon dans la clause `IN` : il ne changerait rien au comportement,
    # mais il signalerait une édition faite à la main et mal relue.
    assert len(set(du_modele)) == len(du_modele)
    assert len(set(de_la_migration)) == len(de_la_migration)
    assert {"page_render", "vector_geometry", "cad_read", "measurement"} <= DOCUMENT_PIPELINE_STEPS
