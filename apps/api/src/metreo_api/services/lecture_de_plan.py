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
from . import lecture_dxf, lecture_pdf, mesures_de_plan, rendu_de_plan, rendu_pdf
from .document_storage import StockageLocal
from .lecture_dxf import LecturePlan
from .lecture_pdf import LecturePdf

logger = logging.getLogger("metreo.api")

#: Les types qu'une lecture de plan sait traiter, et le format qu'ils désignent.
#:
#: Les deux formats ne font PAS la même chose, et l'écran doit le dire :
#:
#: - un DXF est mesuré. Son unité de dessin est déclarée dans le fichier, et
#:   ses cotations portent une valeur ;
#: - un PDF est lu et affiché, mais **aucune mesure n'en sort sans qu'un
#:   humain ait confirmé une échelle**. Ses coordonnées sont des points
#:   PostScript, qui ne disent rien de l'ouvrage.
#:
#: Accepter le PDF ici ne revient donc pas à promettre un métré : cela revient
#: à promettre un aperçu et des textes situés. La différence est portée par
#: `format`, et par `mesurable`, qui reste faux pour un PDF.
TYPES_LISIBLES: dict[str, str] = {
    "image/vnd.dxf": "dxf",
    "application/pdf": "pdf",
}

#: Conservé : `scripts/lire_un_plan.py` et l'épreuve d'image le nomment, et un
#: renommage silencieux en ferait deux vérités.
TYPE_LISIBLE = "image/vnd.dxf"

#: L'extracteur, tel qu'il s'inscrit dans chaque citation. La version de la
#: bibliothèque en fait partie : une mesure doit pouvoir dire QUI l'a lue.
#: Borné à 120 caractères par la base.
EXTRACTEUR = "lecture_dxf@ezdxf"

#: Le pendant pour le PDF. Deux extracteurs distincts, parce qu'une citation
#: doit dire par quelle bibliothèque elle a été produite — et parce qu'une
#: relecture ne reconnaît ses propres citations qu'à ce nom.
EXTRACTEUR_PDF = "lecture_pdf@pypdfium2"

ETAPE_LECTURE = "cad_read"
ETAPE_RENDU = "page_render"

#: L'étape d'extraction du texte d'un PDF. Le nom vient du pipeline
#: documentaire, où il désigne exactement cela : le texte que le document
#: porte déjà, par opposition à `ocr`, qui le devine depuis une image. Les
#: deux étaient déjà déclarés dans la contrainte de `document_step_runs` —
#: aucune migration n'est donc nécessaire ici, et c'est vérifié par un test.
ETAPE_TEXTE = "native_text"

#: Combien de pages reçoivent un aperçu.
#:
#: Mesuré : une page A0 rendue à 2 000 pixels de grand côté prend environ
#: 0,3 s. Dix pages tiennent donc dans les trois secondes, ce qui reste dans
#: l'enveloppe d'une analyse immédiate ; cinquante n'y tiendraient pas.
#:
#: Au-delà, la lecture ne se tait pas : une anomalie nomme les pages restées
#: sans aperçu. Un document tronqué en silence serait présenté comme complet.
PLAFOND_APERCUS = 10


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


def cle_des_fragments(organization_id: str, revision_id: str) -> str:
    """La clé de l'artefact portant les textes d'un PDF.

    Séparé du constat, et pas par goût du rangement : mesuré sur un plan réel,
    une page porte 4 351 fragments. Les mettre dans le constat obligerait
    chaque ouverture d'écran à charger des mégaoctets de texte pour afficher
    une ligne d'état.
    """
    return f"{rendu_de_plan.DOSSIER_RENDUS}/{organization_id}/{revision_id}-textes.json"


def format_de(revision: DocumentRevision) -> str:
    """`"dxf"` ou `"pdf"`. Lève `PlanNonLisible` pour tout le reste."""
    verifier_que_cest_un_plan(revision)
    return TYPES_LISIBLES[revision.media_type]


def verifier_que_cest_un_plan(revision: DocumentRevision) -> None:
    """Lève `PlanNonLisible` si la révision n'est ni un DXF ni un PDF."""
    if revision.media_type not in TYPES_LISIBLES:
        raise PlanNonLisible(
            "type_non_lisible",
            "Seuls un plan DXF et un PDF sont lus aujourd'hui. Le fichier "
            "reste déposé et téléchargeable.",
        )


def cles_derivees(
    stockage: StockageLocal,
    *,
    organization_id: str,
    revision_id: str,
) -> list[tuple[str, str]]:
    """Tous les artefacts dérivés d'une révision, avec leur intitulé.

    Cette fonction existe pour une raison et une seule : la purge. Les
    dérivés n'ont **aucune ligne en base** — leur clé est calculée depuis la
    révision — donc le jour où la révision disparaît, plus rien ne sait quels
    fichiers lui appartenaient. Leur liste doit donc vivre à un seul endroit,
    et c'est ici ; `conservation.py` l'appelle au lieu de la redire.

    Le nombre d'aperçus d'un PDF n'est pas devinable : il est LU dans le
    constat, qui l'a enregistré. À défaut de constat, on ne cherche pas :
    sans analyse, il n'y a pas d'aperçu.
    """
    cles: list[tuple[str, str]] = [
        (rendu_de_plan.cle_du_rendu(organization_id, revision_id), "image du plan"),
        (cle_du_constat(organization_id, revision_id), "constat du plan"),
        (cle_des_fragments(organization_id, revision_id), "textes du plan"),
    ]
    constat = lire_le_constat(stockage, organization_id=organization_id, revision_id=revision_id)
    pages = constat.contenu.get("apercus") if constat is not None else None
    if isinstance(pages, list):
        cles += [
            (
                rendu_pdf.cle_de_l_apercu(organization_id, revision_id, int(page)),
                f"aperçu de la page {int(page)}",
            )
            for page in pages
            if isinstance(page, int)
        ]
    return cles


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


# --------------------------------------------------------------------------
# Le PDF : lire les textes, puis rendre les aperçus
# --------------------------------------------------------------------------


def extracteur_pdf_avec_version() -> str:
    """`lecture_pdf@pypdfium2-5.14.0`, ou le nom seul si la version manque."""
    try:
        from importlib.metadata import version

        return f"{EXTRACTEUR_PDF}-{version('pypdfium2')}"[:120]
    except Exception:  # pragma: no cover - l'extra `pdf` est optionnel
        return EXTRACTEUR_PDF


def _constat_pdf_en_dictionnaire(
    constat: LecturePdf, *, apercus: list[int], pages_sans_apercu: int
) -> dict[str, Any]:
    """La même forme que le constat DXF, et c'est délibéré.

    L'écran lit un seul objet, quel que soit le format : les champs d'un
    format absent valent `None` ou une liste vide, jamais une valeur
    inventée. Deux formes auraient demandé deux écrans.

    `mesurable` vaut **faux sans condition**, et ce n'est pas un défaut à
    corriger plus tard : un PDF ne porte aucune unité de dessin. Une mesure
    n'en sortira qu'après qu'un humain aura confirmé une échelle, et elle
    naîtra alors d'une autre étape que celle-ci.
    """
    motif = constat.motif_du_refus
    return {
        "format": "pdf",
        "refuse": constat.refuse,
        "motif_du_refus": ({"code": motif.code, "message": motif.message} if motif else None),
        "mesurable": False,
        "unite_source": None,
        "insunits": None,
        "version_dxf": None,
        "feuilles": [],
        "calques": {},
        "entites": {},
        "etendue": None,
        "anomalies": [{"code": a.code, "message": a.message} for a in constat.anomalies],
        "cotations_lues": 0,
        "extracteur": extracteur_pdf_avec_version(),
        # Ce qui n'existe que pour un PDF.
        "pages": constat.pages,
        "dimensions_des_pages": [[largeur, hauteur] for largeur, hauteur in constat.dimensions],
        "porte_du_texte": constat.porte_du_texte,
        "fragments_lus": len(constat.fragments),
        "apercus": apercus,
        "pages_sans_apercu": pages_sans_apercu,
    }


def _fragments_en_dictionnaire(constat: LecturePdf) -> dict[str, Any]:
    """Les textes, sous une forme que l'écran peut poser sur l'aperçu.

    Les coordonnées sont celles de `lecture_pdf` : normalisées dans [0,1],
    origine en haut à gauche — donc **celles du PNG**, directement. Les
    reconvertir ici réintroduirait l'inversion qu'un seul endroit doit porter.
    """
    return {
        "extracteur": extracteur_pdf_avec_version(),
        "fragments": [
            {
                "texte": fragment.texte,
                "page": fragment.page,
                # `null` quand la position est inconnue — un fragment hors de
                # la page affichée. Le texte reste, l'emplacement manque, et
                # `position` dit lequel des deux cas on a.
                "cadre": (
                    [
                        fragment.cadre.x0,
                        fragment.cadre.y0,
                        fragment.cadre.x1,
                        fragment.cadre.y1,
                    ]
                    if fragment.cadre is not None
                    else None
                ),
                "position": fragment.position,
            }
            for fragment in constat.fragments
        ],
    }


def travail_d_extraction(stockage: StockageLocal) -> Travail:
    """Le travail de l'étape `native_text` : lire le PDF et ses textes.

    Rend `None` quand la lecture aboutit, **même si le document ne porte
    aucun texte** : un plan scanné reste consultable, et c'est le constat qui
    porte l'anomalie. Faire échouer l'étape interdirait l'aperçu, qui est
    précisément ce qui reste utile dans ce cas.

    Aucune mesure n'est écrite, et aucune citation : un fragment de texte
    n'est pas une mesure. Les citations naîtront d'une confirmation d'échelle,
    qui est une décision humaine et une autre étape.
    """

    def _travail(session: Session, revision: DocumentRevision, chemin: Path) -> str | None:
        constat = lecture_pdf.lire(chemin)

        if not constat.refuse:
            _poser_un_artefact(
                session,
                stockage=stockage,
                organization_id=revision.organization_id,
                revision_id=revision.id,
                extension="-textes.json",
                contenu=json.dumps(_fragments_en_dictionnaire(constat), ensure_ascii=False).encode(
                    "utf-8"
                ),
                media_type="application/json",
            )

        # Le constat est écrit dans tous les cas, refus compris : l'utilisateur
        # doit pouvoir lire POURQUOI son fichier a été refusé, et un échec
        # d'étape ne porte qu'un code parmi six.
        _poser_un_artefact(
            session,
            stockage=stockage,
            organization_id=revision.organization_id,
            revision_id=revision.id,
            extension=".json",
            contenu=json.dumps(
                _constat_pdf_en_dictionnaire(constat, apercus=[], pages_sans_apercu=0),
                ensure_ascii=False,
                indent=1,
            ).encode("utf-8"),
            media_type="application/json",
        )

        if constat.refuse:
            return "unsupported_media_type"

        logger.info(
            "pdf_lu",
            extra={
                "organization_id": revision.organization_id,
                "revision_id": revision.id,
                # Des COMPTEURS, jamais un fragment de texte : le journal ne
                # porte pas de contenu de document.
                "pages": constat.pages,
                "fragments_lus": len(constat.fragments),
                "porte_du_texte": constat.porte_du_texte,
            },
        )
        return None

    return _travail


def travail_d_apercu(stockage: StockageLocal) -> Travail:
    """Le travail de l'étape `page_render` pour un PDF : un PNG par page.

    L'étape réussit si **la première page** a pu être rendue. Une page
    ultérieure qui échoue est consignée au journal et laisse l'aperçu partiel
    disponible : refuser l'ensemble pour la page sept priverait l'utilisateur
    des six premières, qui sont bonnes.
    """

    def _travail(session: Session, revision: DocumentRevision, chemin: Path) -> str | None:
        constat = lire_le_constat(
            stockage,
            organization_id=revision.organization_id,
            revision_id=revision.id,
        )
        if constat is None:
            # L'aperçu vient APRÈS l'extraction, qui écrit le constat. Sans
            # lui on ne sait pas combien de pages existent, et sonder au
            # hasard produirait des refus pour des pages inexistantes.
            return "processing_failed"

        total = int(constat.contenu.get("pages") or 0)
        if total <= 0:
            return "processing_failed"

        a_rendre = min(total, PLAFOND_APERCUS)
        rendues: list[int] = []
        for page in range(1, a_rendre + 1):
            try:
                apercu = rendu_pdf.rendre(chemin, page=page)
            except rendu_pdf.RenduRefuse as refus:
                logger.info(
                    "apercu_de_pdf_refuse",
                    extra={
                        "organization_id": revision.organization_id,
                        "revision_id": revision.id,
                        "page": page,
                        "code": refus.code,
                    },
                )
                if page == 1:
                    if refus.code in ("fichier_illisible", "fichier_invalide"):
                        return "unsupported_media_type"
                    if refus.code == "rendu_non_servable":
                        return "invalid_output"
                    return "processing_failed"
                break

            _poser_un_artefact(
                session,
                stockage=stockage,
                organization_id=revision.organization_id,
                revision_id=revision.id,
                extension=f"-p{page}.png",
                contenu=apercu.png,
                media_type="image/png",
            )
            rendues.append(page)

        # Le constat est réécrit pour dire QUELLES pages ont un aperçu. Sans
        # cela, l'écran devrait sonder le volume page par page, et la purge
        # ne saurait pas quels fichiers retirer.
        contenu = dict(constat.contenu)
        contenu["apercus"] = rendues
        contenu["pages_sans_apercu"] = total - len(rendues)
        if total > len(rendues):
            anomalies = list(contenu.get("anomalies") or [])
            anomalies.append(
                {
                    "code": "apercus_incomplets",
                    "message": (
                        f"Les {len(rendues)} premières pages sur {total} ont un "
                        "aperçu. Les suivantes sont déposées et téléchargeables, "
                        "mais elles ne s'affichent pas dans Metreo."
                    ),
                }
            )
            contenu["anomalies"] = anomalies
        _poser_un_artefact(
            session,
            stockage=stockage,
            organization_id=revision.organization_id,
            revision_id=revision.id,
            extension=".json",
            contenu=json.dumps(contenu, ensure_ascii=False, indent=1).encode("utf-8"),
            media_type="application/json",
        )

        logger.info(
            "pdf_apercu",
            extra={
                "organization_id": revision.organization_id,
                "revision_id": revision.id,
                "pages": total,
                "apercus_rendus": len(rendues),
            },
        )
        return None

    return _travail


def etapes_pour(revision: DocumentRevision, stockage: StockageLocal) -> list[tuple[str, Travail]]:
    """Les étapes à jouer pour cette révision, dans l'ordre, selon son format.

    L'ordre n'est pas indifférent dans les deux cas, et pour deux raisons
    différentes :

    - en DXF, `cad_read` passe avant `page_render` pour que des mesures justes
      ne disparaissent pas si le dessin ne se trace pas ;
    - en PDF, `native_text` passe avant `page_render` parce que l'aperçu a
      besoin de savoir combien de pages existent, ce que seule la lecture dit.
    """
    if format_de(revision) == "pdf":
        return [
            (ETAPE_TEXTE, travail_d_extraction(stockage)),
            (ETAPE_RENDU, travail_d_apercu(stockage)),
        ]
    return [
        (ETAPE_LECTURE, travail_de_lecture(stockage)),
        (ETAPE_RENDU, travail_de_rendu(stockage)),
    ]


def lire_les_fragments(
    stockage: StockageLocal,
    *,
    organization_id: str,
    revision_id: str,
) -> list[dict[str, Any]] | None:
    """Les textes posés par l'extraction, ou `None` s'il n'y en a pas.

    `None` veut dire « jamais extrait », et c'est un état, pas une erreur —
    la même convention que `lire_le_constat`.
    """
    cle = cle_des_fragments(organization_id, revision_id)
    if stockage.taille(cle) is None:
        return None
    octets = b"".join(stockage.lire(cle))
    try:
        contenu = json.loads(octets.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(
            "artefact_de_textes_illisible",
            extra={"organization_id": organization_id, "revision_id": revision_id},
        )
        return None
    if not isinstance(contenu, dict):
        return None
    fragments = contenu.get("fragments")
    if not isinstance(fragments, list):
        return None
    return [fragment for fragment in fragments if isinstance(fragment, dict)]
