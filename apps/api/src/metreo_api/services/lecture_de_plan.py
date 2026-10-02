"""Le parcours complet d'un plan : le lire, le rendre, en proposer les mesures.

Ce module est la couture entre quatre pièces qui, séparément, ne savent rien
de la base : `lecture_dxf` (le constat), `rendu_de_plan` (l'image),
`mesures_de_plan` (les propositions) et `travail_documentaire` (l'état
d'exécution). Il n'ajoute aucune règle de mesure ; il décide seulement **ce
qui est consigné où**.

**Deux étapes, et pas une.** `cad_read` lit le fichier et propose les mesures ;
`page_render` produit l'image. Les séparer coûte une seconde lecture du
fichier — mesuré, 3,5 s sur un plan réel — et le prix est accepté pour trois
raisons : une image qui échoue ne doit pas emporter des mesures déjà justes ;
chaque étape se relance séparément ; et l'état d'exécution dit alors laquelle
des deux a échoué, ce qu'une étape unique ne saurait pas dire.

**Ce qui est consigné en base, et ce qui ne l'est pas.** Les mesures vont en
base, parce qu'elles attendent une décision humaine qui doit survivre à tout.
Le constat (unité, calques, comptes d'entités, anomalies du fichier) et
l'image sont des **artefacts dérivés**, posés sur le volume : ils se
reconstruisent à partir de l'original, qui est immuable. Leur donner une table
serait leur donner une durée de vie qu'ils n'ont pas.

**Le cadre de l'image et celui des mesures sont le même.** Les deux étapes
appellent `bbox.extents(modelspace)` sur le même fichier immuable : le repère
est donc identique, et une position normalisée dans [0,1] se pose directement
sur le SVG. Un test le vérifie, parce que rien dans le code ne l'impose.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..models import DocumentRevision
from ..transactions import compenser
from . import lecture_dxf, mesures_de_plan, rendu_de_plan
from .document_storage import StockageLocal
from .lecture_dxf import LecturePlan

logger = logging.getLogger("metreo.api")

#: Le seul type qu'une lecture de plan sait traiter aujourd'hui.
#:
#: Le PDF est accepté au DÉPÔT et reste téléchargeable, mais sa lecture
#: vectorielle n'est pas livrée. Dire « plan » en acceptant un PDF ici
#: laisserait croire qu'il va être mesuré.
TYPE_LISIBLE = "image/vnd.dxf"

#: L'extracteur, tel qu'il s'inscrit dans chaque citation. La version de la
#: bibliothèque en fait partie : une mesure doit pouvoir dire QUI l'a lue.
#: Borné à 120 caractères par la base.
EXTRACTEUR = "lecture_dxf@ezdxf"

ETAPE_LECTURE = "cad_read"
ETAPE_RENDU = "page_render"


class PlanNonLisible(Exception):
    """La révision n'est pas un plan que ce module sait lire."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Constat:
    """Le constat tel qu'il est relu depuis l'artefact, sans refaire la lecture."""

    contenu: dict[str, Any]

    @property
    def mesurable(self) -> bool:
        return bool(self.contenu.get("mesurable"))


def extracteur_avec_version() -> str:
    """`lecture_dxf@ezdxf-1.4.4`, ou le nom seul si la version est illisible."""
    try:
        import ezdxf

        return f"{EXTRACTEUR}-{ezdxf.__version__}"[:120]
    except Exception:  # pragma: no cover - ezdxf est un extra optionnel
        return EXTRACTEUR


def cle_du_constat(organization_id: str, revision_id: str) -> str:
    """La clé de l'artefact de constat. Déterministe, comme celle du rendu.

    Même raison qu'en face : l'artefact est produit par Metreo, son extension
    est toujours `.json`, et son identifiant est celui d'une révision
    immuable. Ce n'est PAS le cas de l'original, dont l'extension dépend du
    type détecté dans les octets — la sienne est lue en base, jamais
    recalculée.
    """
    return f"{rendu_de_plan.DOSSIER_RENDUS}/{organization_id}/{revision_id}.json"


def verifier_que_cest_un_plan(revision: DocumentRevision) -> None:
    """Lève `PlanNonLisible` si la révision n'est pas un DXF."""
    if revision.media_type != TYPE_LISIBLE:
        raise PlanNonLisible(
            "type_non_lisible",
            "Seul un plan DXF est lu aujourd'hui. Le fichier reste déposé et "
            "téléchargeable ; la lecture du PDF n'est pas livrée.",
        )


def _constat_en_dictionnaire(constat: LecturePlan) -> dict[str, Any]:
    motif = constat.motif_du_refus
    return {
        "refuse": constat.refuse,
        "motif_du_refus": ({"code": motif.code, "message": motif.message} if motif else None),
        "mesurable": constat.mesurable,
        "unite_source": constat.unite_source,
        "insunits": constat.insunits,
        "version_dxf": constat.version_dxf,
        "feuilles": list(constat.feuilles),
        "calques": dict(constat.calques),
        "entites": dict(constat.entites),
        "etendue": list(constat.etendue) if constat.etendue else None,
        "anomalies": [{"code": a.code, "message": a.message} for a in constat.anomalies],
        "cotations_lues": len(constat.cotations),
        "extracteur": extracteur_avec_version(),
    }


def _poser_un_artefact(
    session: Session,
    *,
    stockage: StockageLocal,
    organization_id: str,
    revision_id: str,
    extension: str,
    contenu: bytes,
    media_type: str,
) -> str:
    """Pose un dérivé sur le volume, et prévoit son retrait si tout s'annule.

    Le volume et la base ne partagent aucune transaction : un fichier écrit
    avant un `commit` qui échoue resterait là sans que rien ne le nomme. La
    compensation est donc enregistrée juste après l'écriture, et elle est
    idempotente — elle peut n'être jamais jouée, l'être une fois, ou l'être
    après que le fichier a déjà disparu.
    """
    pose = stockage.ecrire_octets(
        organization_id=organization_id,
        dossier=rendu_de_plan.DOSSIER_RENDUS,
        identifiant=revision_id,
        extension=extension,
        contenu=contenu,
        media_type=media_type,
    )
    compenser(
        session,
        lambda: stockage.supprimer(pose.storage_key),
        f"artefact de plan {extension}",
    )
    return pose.storage_key


def lire_le_constat(
    stockage: StockageLocal,
    *,
    organization_id: str,
    revision_id: str,
) -> Constat | None:
    """Relit le constat posé par l'étape de lecture, ou `None` s'il n'y en a pas.

    `None` veut dire « jamais analysé », et c'est ce que l'API doit rendre en
    404 : pas une erreur, un état.
    """
    cle = cle_du_constat(organization_id, revision_id)
    if stockage.taille(cle) is None:
        return None
    octets = b"".join(stockage.lire(cle))
    try:
        contenu = json.loads(octets.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Un artefact illisible n'est pas une donnée perdue : l'original est
        # immuable, une relecture le refait. On le traite comme absent.
        logger.warning(
            "artefact_de_constat_illisible",
            extra={"organization_id": organization_id, "revision_id": revision_id},
        )
        return None
    if not isinstance(contenu, dict):
        return None
    return Constat(contenu=contenu)


def image_disponible(
    stockage: StockageLocal,
    *,
    organization_id: str,
    revision_id: str,
) -> bool:
    return stockage.taille(rendu_de_plan.cle_du_rendu(organization_id, revision_id)) is not None


#: La signature qu'attendent `executer_etape` et `executer_etape_dans`.
Travail = Callable[[Session, DocumentRevision, Path], str | None]


def travail_de_lecture(stockage: StockageLocal) -> Travail:
    """Le travail de l'étape `cad_read`, prêt pour `executer_etape*`.

    Rend `None` quand la lecture aboutit — **y compris quand le plan n'est pas
    mesurable**. L'unité absente n'est pas un échec d'étape : le constat est
    écrit, le plan reste consultable, et c'est le constat qui porte la
    nouvelle. Faire échouer l'étape effacerait cette nuance.
    """

    def _travail(session: Session, revision: DocumentRevision, chemin: Path) -> str | None:
        constat = lecture_dxf.lire(chemin, situer=True)

        _poser_un_artefact(
            session,
            stockage=stockage,
            organization_id=revision.organization_id,
            revision_id=revision.id,
            extension=".json",
            contenu=json.dumps(
                _constat_en_dictionnaire(constat), ensure_ascii=False, indent=1
            ).encode("utf-8"),
            media_type="application/json",
        )

        if constat.refuse:
            # Le constat est posé AVANT le refus : l'utilisateur doit pouvoir
            # lire POURQUOI son fichier a été refusé, et un échec d'étape ne
            # porte qu'un code de six valeurs possibles.
            return "unsupported_media_type"

        if constat.unite_source is None:
            logger.info(
                "plan_lu_sans_unite",
                extra={
                    "organization_id": revision.organization_id,
                    "revision_id": revision.id,
                    "cotations_lues": len(constat.cotations),
                },
            )
            return None

        mesures_de_plan.enregistrer(
            session,
            organization_id=revision.organization_id,
            revision_id=revision.id,
            constat=constat,
            extracteur=extracteur_avec_version(),
        )
        return None

    return _travail


def travail_de_rendu(stockage: StockageLocal) -> Travail:
    """Le travail de l'étape `page_render` : produire l'image affichable."""

    def _travail(session: Session, revision: DocumentRevision, chemin: Path) -> str | None:
        try:
            rendu = rendu_de_plan.rendre(chemin)
        except rendu_de_plan.RenduRefuse as refus:
            logger.info(
                "rendu_de_plan_refuse",
                extra={
                    "organization_id": revision.organization_id,
                    "revision_id": revision.id,
                    "code": refus.code,
                },
            )
            # Les six codes autorisés en base ne distinguent pas ces cas ; le
            # code précis vit dans le journal, et l'écran dit simplement que
            # l'image n'a pas pu être produite.
            if refus.code in ("fichier_illisible", "fichier_invalide", "cycle_de_blocs"):
                return "unsupported_media_type"
            if refus.code == "rendu_non_servable":
                return "invalid_output"
            return "processing_failed"

        _poser_un_artefact(
            session,
            stockage=stockage,
            organization_id=revision.organization_id,
            revision_id=revision.id,
            extension=".svg",
            contenu=rendu.svg.encode("utf-8"),
            media_type="image/svg+xml",
        )
        logger.info(
            "plan_rendu",
            extra={
                "organization_id": revision.organization_id,
                "revision_id": revision.id,
                "entites_rendues": rendu.entites_rendues,
                "octets_svg": len(rendu.svg),
            },
        )
        return None

    return _travail
