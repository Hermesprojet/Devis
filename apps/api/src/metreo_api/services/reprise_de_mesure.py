"""Faire d'une mesure tranchée une ligne de bordereau.

**Le trou que ce module ferme.** `calibration_de_plan.reprenables()` disait
depuis plusieurs livraisons quelles mesures un bordereau a le droit de
reprendre : ni une mesure rejetée, ni une mesure sur laquelle personne n'a
tranché. Rien ne les reprenait. La règle n'avait aucun point d'application, et
une quantité mesurée sur un plan ne pouvait arriver dans un devis que recopiée
à la main — donc sans provenance, et sans que rien ne dise d'où venait le
nombre.

**Trois décisions, et chacune est contestable, donc écrite ici.**

1. **La conversion d'unité a lieu MAINTENANT, et pas avant.** C'est ce que
   `ExtractionProposal` annonce dans sa propre docstring : « La conversion aura
   lieu plus tard, quand un humain rattachera la mesure à une ligne de
   bordereau : après la `ValidationDecision`, jamais avant. » L'unité cible est
   celle de la ligne, et c'est une personne qui la choisit. À défaut, c'est
   celle de la mesure : ne rien convertir est le seul défaut qui ne devine rien.

2. **La quantité reprise est la valeur RETENUE, jamais la mesure brute.** Une
   proposition machine n'est pas une quantité. Si la personne a corrigé 6 000
   en 6 020, c'est 6 020 qui part au bordereau, et l'empreinte garde les deux.

3. **L'incertitude ne devient pas une quantité.** Elle est conservée dans
   l'empreinte, en clair, et elle n'entre dans aucun calcul de prix. Majorer
   une quantité de son incertitude serait une décision commerciale — une marge
   déguisée — et le propriétaire n'en a fixé aucune.

Ce module est pur à une exception près : il lit la session pour retrouver la
mesure. Il n'écrit rien ; c'est la route qui crée la ligne.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from metreo_domain.errors import (
    AmbiguousConversionError,
    IncompatibleUnitsError,
    UnknownUnitError,
)
from metreo_domain.units import Quantity, convert, get_unit

from ..db import AMOUNT_SCALE
from ..models import ExtractionProposal
from . import calibration_de_plan, lisible
from .tenant import find_owned


class RepriseRefusee(Exception):
    """Un refus que l'appelant peut relayer tel quel, code et phrase comprises."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Reprise:
    """Ce qu'une ligne de bordereau doit porter pour reprendre une mesure."""

    #: La quantité, dans `unite`, convertie exactement depuis la valeur retenue.
    quantite: Decimal
    #: Le code d'unité canonique de la quantité ci-dessus.
    unite: str
    #: L'empreinte figée : d'où vient ce nombre, et ce qu'il valait.
    source_mesure: dict[str, object]
    #: Une phrase lisible, pour un journal ou un libellé par défaut.
    provenance_lisible: str
    #: La quantité ci-dessus, écrite pour être LUE — « 4,18068 m ».
    #:
    #: Rendue par le serveur, et jamais recomposée par l'écran : l'écran doit
    #: pouvoir montrer le nombre AVANT de l'écrire, et deux arrondis — un en
    #: Python, un en TypeScript — finiraient par diverger d'un chiffre. C'est
    #: la même règle que `valeur_lisible` sur une mesure.
    quantite_lisible: str


def preparer(
    session: Session,
    *,
    organization_id: str,
    proposal_id: str,
    unite_cible: str | None = None,
) -> Reprise | None:
    """La quantité, l'unité et l'empreinte — ou `None` si la mesure n'existe pas.

    `None` et non une exception pour l'inexistence : c'est à la route de rendre
    404, et un identifiant d'un autre tenant doit produire exactement le même
    404 qu'un identifiant inventé.
    """
    proposition = find_owned(session, ExtractionProposal, organization_id, proposal_id)
    if proposition is None:
        return None

    if proposition.schema_name != calibration_de_plan.SCHEMA:
        raise RepriseRefusee(
            "mesure_non_reprise",
            "Seules les mesures prises sur un plan PDF se reprennent aujourd'hui "
            "dans un bordereau. Celle-ci vient d'une autre source.",
        )

    # Relue par `lister`, et non reconstruite ici : c'est là que vit la règle
    # qui dit ce qu'une décision retient. La réécrire rendrait possible qu'une
    # mesure rejetée passe par cette porte-ci et pas par l'autre.
    mesures = calibration_de_plan.lister(
        session, organization_id=organization_id, revision_id=proposition.revision_id
    )
    mesure = next((une for une in mesures if une.proposal_id == proposal_id), None)
    if mesure is None:
        return None

    if not mesure.reprenable:
        raise RepriseRefusee(
            "mesure_sans_decision_retenue",
            _pourquoi_elle_ne_se_reprend_pas(mesure.decision),
        )
    if mesure.valeur_retenue is None:  # pragma: no cover - `reprenable` l'implique
        raise RepriseRefusee(
            "mesure_sans_decision_retenue",
            _pourquoi_elle_ne_se_reprend_pas(mesure.decision),
        )

    unite_source = mesure.unite_retenue or mesure.unite
    quantite, unite = _convertir(Decimal(mesure.valeur_retenue), unite_source, unite_cible)

    # **L'incertitude suit la même conversion**, et elle part dans l'empreinte.
    # Elle ne décide plus l'écriture de la quantité : cet aperçu annonce ce que
    # la LIGNE DE BORDEREAU portera, et une ligne de bordereau s'écrit comme le
    # devis l'écrira — deux décimales au moins, celles de la valeur au-delà.
    # Annoncer « 6,0200 m » puis écrire « 6,02 m » serait annoncer autre chose
    # que ce qu'on fait ; c'est le défaut que les captures ont montré.
    #
    # La précision de la MESURE n'est pas perdue pour autant : elle reste à
    # l'écran sur la ligne de mesure, avec son « ± », et dans
    # `provenance_lisible` juste au-dessous.
    incertitude = _en_decimal(mesure.incertitude)
    if incertitude is not None:
        incertitude, _ = _convertir(incertitude, unite_source, unite_cible)

    return Reprise(
        quantite=quantite,
        unite=unite,
        quantite_lisible=lisible.quantite_de_document_lisible(quantite, unite),
        source_mesure=_empreinte(
            mesure,
            unite_source=unite_source,
            unite_reprise=unite,
            incertitude_reprise=incertitude,
        ),
        provenance_lisible=(
            f"Mesure de plan, page {mesure.page}, "
            f"{_DECISION_LISIBLE.get(mesure.decision or '', 'tranchée')} : "
            f"{mesure.valeur_retenue_lisible or mesure.valeur_lisible}"
        ),
    )


#: La précision que la colonne `boq_items.quantity` conserve réellement.
#:
#: `Amount` est un `NUMERIC(28, 10)`, et quantise à l'écriture. La reprise
#: quantise AVANT, pour que l'aperçu annonce le nombre qui sera écrit et non
#: celui qui lui ressemble à la treizième décimale.
_PRECISION_DE_COLONNE = Decimal(1).scaleb(-AMOUNT_SCALE)


#: Pourquoi une mesure ne se reprend pas, selon ce qui lui est arrivé.
_DECISION_LISIBLE: dict[str, str] = {
    "accepted": "confirmée",
    "corrected": "corrigée",
    "rejected": "rejetée",
}


def _en_decimal(valeur: str | None) -> Decimal | None:
    """Un nombre, ou rien — jamais une exception sur une valeur de JSON.

    Même raison que l'homonyme de `calibration_de_plan` : `value` est du JSON,
    et rien en base ne garantit qu'une ligne écrite par une version future
    reste lisible. Une incertitude illisible fait perdre le choix des
    décimales, pas la reprise.
    """
    if valeur in (None, ""):
        return None
    try:
        return Decimal(str(valeur))
    except (InvalidOperation, ValueError):
        return None


def _pourquoi_elle_ne_se_reprend_pas(decision: str | None) -> str:
    """Le motif exact du refus, et non « mesure invalide ».

    Les deux cas n'appellent pas la même action : l'une attend une décision,
    l'autre a déjà été écartée et ne reviendra pas.
    """
    if decision == "rejected":
        return (
            "Cette mesure a été rejetée : elle n'alimentera aucun bordereau. "
            "Reprenez la mesure sur le plan si la quantité est nécessaire."
        )
    return (
        "Personne n'a encore tranché sur cette mesure. Une proposition de "
        "calcul n'est pas une quantité : confirmez-la ou corrigez-la d'abord."
    )


def _convertir(valeur: Decimal, unite_source: str, unite_cible: str | None) -> tuple[Decimal, str]:
    """La valeur retenue, dans l'unité de la ligne — exactement.

    Sans `unite_cible`, rien n'est converti : c'est le seul défaut qui ne
    devine pas ce que la personne voulait. Une unité d'une AUTRE dimension est
    refusée et nommée : convertir une surface en mètres linéaires n'a pas de
    sens, et un facteur silencieux produirait un métré plausible et faux.
    """
    try:
        source = get_unit(unite_source)
    except UnknownUnitError as exc:  # pragma: no cover - l'unité vient du serveur
        raise RepriseRefusee("unite_de_mesure_inconnue", exc.message) from exc

    if unite_cible is None:
        return valeur, source.code

    try:
        cible = get_unit(unite_cible)
    except UnknownUnitError as exc:
        raise RepriseRefusee("unite_cible_inconnue", exc.message) from exc

    if cible.code == source.code:
        return valeur, source.code

    try:
        converti = convert(Quantity(valeur, source), cible.code)
    except (IncompatibleUnitsError, AmbiguousConversionError) as exc:
        # Les deux refus ont la même conséquence pour l'appelant — la reprise
        # n'a pas lieu — et deux codes distincts n'apprendraient rien ici : ni
        # un passage longueur → surface, ni un passage volume → masse sans
        # masse volumique sourcée ne peut être tranché par le serveur.
        raise RepriseRefusee(
            "unite_cible_incompatible",
            f"Cette mesure est en {lisible.unite_affichee(source.code)} ; "
            f"elle ne peut pas être reprise en {lisible.unite_affichee(cible.code)}.",
        ) from exc
    # **Quantisée à la précision de la COLONNE, et non rendue brute.**
    #
    # `convert` travaille à 28 chiffres significatifs : 4 180,682 281 084 4 mm
    # devient 4,180 682 281 084 4 m, soit treize décimales. La colonne `Amount`
    # en garde dix, et quantise à l'écriture. Sans cette ligne, l'aperçu
    # annonçait donc un nombre que la ligne n'allait pas porter — exactement ce
    # que cet aperçu existe pour empêcher. L'écart est de l'ordre de 10⁻¹¹ m ;
    # ce n'est pas sa taille qui compte, c'est qu'il existe.
    return converti.quantity.value.quantize(_PRECISION_DE_COLONNE), cible.code


def _empreinte(
    mesure: calibration_de_plan.MesureALire,
    *,
    unite_source: str,
    unite_reprise: str,
    incertitude_reprise: Decimal | None = None,
) -> dict[str, object]:
    """Ce que valait la mesure à l'instant de la reprise, figé.

    **Pourquoi figer, alors que le lien existe.** Parce que le lien peut être
    dénoué : la clé étrangère est en `SET NULL`, pour qu'une suppression de
    proposition ne détruise jamais une ligne de devis. Rien ne détruit
    aujourd'hui une proposition reprise — la seule purge livrée emporte toute
    l'organisation, et une décision humaine est en ajout seul — mais le jour où
    une purge par document existera, l'empreinte sera tout ce qui reste pour
    dire d'où vient le montant.

    Toutes les valeurs sont des chaînes : ce champ est du JSON, et un flottant
    y perdrait des décimales que `Amount` conserve.
    """
    return {
        "origine": "mesure_pdf",
        "proposal_id": mesure.proposal_id,
        "citation_id": mesure.citation_id,
        "page": mesure.page,
        "type": mesure.type,
        "libelle": mesure.libelle,
        "valeur_mesuree": mesure.valeur,
        "unite_mesuree": mesure.unite,
        "incertitude": mesure.incertitude,
        #: La même incertitude, dans l'unité RETENUE pour la ligne.
        #:
        #: `incertitude` est dans l'unité de la mesure ; quand la reprise
        #: convertit, les deux ne sont plus comparables, et relire la provenance
        #: six mois plus tard obligerait à refaire la conversion de tête — avec
        #: le facteur d'une table qui aura peut-être bougé. Absente des lignes
        #: écrites avant cette version, et c'est sans conséquence : la ligne
        #: porte déjà `incertitude` et `unite_mesuree`.
        "incertitude_reprise": (None if incertitude_reprise is None else str(incertitude_reprise)),
        "incertitude_relative": mesure.incertitude_relative,
        "fiabilite": mesure.fiabilite,
        "reserves": list(mesure.reserves),
        "decision": mesure.decision,
        "motif_de_la_decision": mesure.motif_de_la_decision,
        "valeur_retenue": mesure.valeur_retenue,
        "unite_retenue": unite_source,
        "unite_reprise": unite_reprise,
        "calibration_id": mesure.calibration.get("id"),
        "motif_de_la_calibration": mesure.calibration.get("motif"),
        "reprise_le": datetime.now(UTC).isoformat(),
    }
