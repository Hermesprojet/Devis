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

4. **La quantité écrite est une quantité RETENUE, et c'est une personne qui la
   retient.** Une mesure confirmée vaut 6,3787950927 m² ± 0,041 m² : écrire les
   dix décimales sur un devis affirme une précision que la mesure n'a pas, et
   c'est ce qu'un premier essai sur un plan réel a imprimé. Le serveur PROPOSE
   donc la quantité à la finesse de la mesure — 6,379 m², la règle que l'écran
   applique déjà au ± —, et la personne peut en retenir une autre, **dans le ±
   de la mesure** : 6,38 m² est une façon d'écrire la même mesure, 6,5 m² est
   un autre nombre. Au-delà du ±, la route refuse et renvoie vers la correction
   de la mesure, qui exige un motif. Une mesure corrigée n'a pas de ± : la
   personne a déjà choisi le nombre, et c'est lui qui s'écrit. La quantité
   brute, la proposée, la retenue et qui l'a retenue partent dans l'empreinte
   et au journal : la décision est tracée.

Ce module est pur à une exception près : il lit la session pour retrouver la
mesure. Il n'écrit rien ; c'est la route qui crée la ligne.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

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

    #: La quantité qui sera ÉCRITE, dans `unite` : celle que la personne a
    #: retenue, ou à défaut celle que le serveur propose.
    quantite: Decimal
    #: La mesure brute dans `unite` : la valeur retenue par la décision,
    #: convertie exactement, sans aucun arrondi. Elle reste dans l'empreinte.
    quantite_brute: Decimal
    #: Le ± de la mesure, dans `unite` — `None` pour une mesure corrigée, dont
    #: la valeur est un choix humain et non un calcul.
    incertitude: Decimal | None
    #: La quantité que le serveur propose d'écrire : la brute, à la finesse
    #: que son ± autorise. Égale à la brute quand il n'y a pas de ±.
    quantite_proposee: Decimal
    #: Qui a retenu `quantite` : la proposition du serveur, ou une personne.
    quantite_retenue_par: Literal["proposition", "personne"]
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
    quantite_brute_lisible: str
    incertitude_lisible: str | None
    quantite_proposee_lisible: str


def preparer(
    session: Session,
    *,
    organization_id: str,
    proposal_id: str,
    unite_cible: str | None = None,
    quantite_retenue: Decimal | None = None,
) -> Reprise | None:
    """La quantité, l'unité et l'empreinte — ou `None` si la mesure n'existe pas.

    `None` et non une exception pour l'inexistence : c'est à la route de rendre
    404, et un identifiant d'un autre tenant doit produire exactement le même
    404 qu'un identifiant inventé.

    `quantite_retenue` est ce que la personne veut écrire, dans `unite_cible`.
    Absente, c'est la proposition du serveur qui s'écrit. Présente, elle est
    refusée si elle sort du ± de la mesure (voir l'en-tête, décision 4).
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
    brute, unite = _convertir(Decimal(mesure.valeur_retenue), unite_source, unite_cible)

    # **L'incertitude suit la même conversion**, et elle part dans l'empreinte.
    # Une mesure CORRIGÉE n'en a pas : la valeur est un choix humain, et lui
    # appliquer le ± calculé par la machine prêterait au nombre d'une personne
    # la finesse d'un pixel. C'est le ± qui borne ce qu'une personne peut
    # retenir, et qui donne à la proposition ses décimales.
    incertitude: Decimal | None = None
    if mesure.decision == "accepted":
        incertitude = _en_decimal(mesure.incertitude)
        if incertitude is not None:
            incertitude, _ = _convertir(incertitude, unite_source, unite_cible)

    proposee = _proposer(brute, incertitude)
    quantite, retenue_par = _retenir(
        brute=brute,
        incertitude=incertitude,
        proposee=proposee,
        demandee=quantite_retenue,
        unite=unite,
    )

    provenance = (
        f"Mesure de plan, page {mesure.page}, "
        f"{_DECISION_LISIBLE.get(mesure.decision or '', 'tranchée')} : "
        f"{mesure.valeur_retenue_lisible or mesure.valeur_lisible}"
    )
    if retenue_par == "personne":
        provenance += (
            f" — quantité retenue {lisible.quantite_de_document_lisible(quantite, unite)}, "
            f"dans le ± de la mesure"
        )

    return Reprise(
        quantite=quantite,
        quantite_brute=brute,
        incertitude=incertitude,
        quantite_proposee=proposee,
        quantite_retenue_par=retenue_par,
        unite=unite,
        quantite_lisible=lisible.quantite_de_document_lisible(quantite, unite),
        quantite_brute_lisible=lisible.quantite_de_document_lisible(brute, unite),
        incertitude_lisible=(
            None if incertitude is None else lisible.incertitude_lisible(incertitude, unite)
        ),
        quantite_proposee_lisible=lisible.quantite_de_document_lisible(proposee, unite),
        source_mesure=_empreinte(
            mesure,
            unite_source=unite_source,
            unite_reprise=unite,
            incertitude_reprise=incertitude,
            quantite_brute=brute,
            quantite_proposee=proposee,
            quantite_retenue=quantite,
            quantite_retenue_par=retenue_par,
        ),
        provenance_lisible=provenance,
    )


def _proposer(brute: Decimal, incertitude: Decimal | None) -> Decimal:
    """La quantité à la finesse de la mesure — et rien de plus fin.

    La règle est celle que `lisible.decimales_utiles` applique déjà au ± :
    l'incertitude garde deux chiffres significatifs, et la valeur s'arrête à la
    même décimale. 6,3787950927 m² ± 0,041 m² se propose 6,379 m². Sans ± —
    mesure corrigée, ou ± absent —, rien n'est arrondi : on ne sait pas jusqu'où
    la valeur est connue, et c'est la brute qui s'écrit.
    """
    if incertitude is None or incertitude <= 0:
        return brute
    decimales = lisible.decimales_utiles(incertitude)
    proposee = brute.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    # `decimales_utiles` plafonne les décimales : un ± plus fin que ce plafond
    # ferait sortir la proposition du ± qu'elle est censée respecter. La brute
    # est alors la seule écriture sûre.
    if abs(proposee - brute) > incertitude:
        return brute
    return proposee


def _retenir(
    *,
    brute: Decimal,
    incertitude: Decimal | None,
    proposee: Decimal,
    demandee: Decimal | None,
    unite: str,
) -> tuple[Decimal, Literal["proposition", "personne"]]:
    """Ce qui sera écrit : la proposition, ou le choix d'une personne s'il tient.

    **Le ± est la frontière.** En deçà, retenir 6,38 là où la mesure dit 6,3788
    ± 0,041 n'est qu'une façon d'écrire la même mesure ; la ligne porte un
    nombre que le plan justifie. Au-delà, c'est un AUTRE nombre, et le bon
    geste est de corriger la mesure, avec un motif, pour que la provenance le
    dise. Sans ± — mesure corrigée —, seule la valeur exacte s'écrit : la
    personne a déjà choisi, et un second choix sans motif serait invisible.
    """
    if demandee is None:
        return proposee, "proposition"
    retenue = demandee.quantize(_PRECISION_DE_COLONNE)
    if retenue <= 0:
        raise RepriseRefusee(
            "quantite_retenue_invalide",
            "La quantité retenue doit être strictement positive.",
        )
    # Renvoyer la proposition du serveur, c'est l'accepter : elle n'a pas à
    # repasser le contrôle de tolérance qui l'a produite.
    if retenue == proposee:
        return proposee, "proposition"
    tolerance = incertitude if incertitude is not None and incertitude > 0 else Decimal(0)
    if abs(retenue - brute) > tolerance:
        brute_lisible = lisible.quantite_de_document_lisible(brute, unite)
        marge = f" {lisible.incertitude_lisible(tolerance, unite)}" if tolerance > 0 else ""
        raise RepriseRefusee(
            "quantite_retenue_hors_tolerance",
            f"{lisible.quantite_de_document_lisible(retenue, unite)} n'est pas une écriture "
            f"de cette mesure, qui vaut {brute_lisible}{marge}. Pour retenir un autre "
            "nombre, corrigez la mesure sur le plan, avec un motif : la provenance le dira.",
        )
    return retenue, "personne"


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
    quantite_brute: Decimal,
    quantite_proposee: Decimal,
    quantite_retenue: Decimal,
    quantite_retenue_par: str,
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
        #: Les trois quantités, dans `unite_reprise`, et qui a retenu la
        #: dernière. C'est ce qui permet de relire, six mois plus tard, que
        #: « 6,38 » est une écriture de « 6,3787950927 ± 0,041 » et non un
        #: autre nombre.
        "quantite_brute": str(quantite_brute),
        "quantite_proposee": str(quantite_proposee),
        "quantite_retenue": str(quantite_retenue),
        "quantite_retenue_par": quantite_retenue_par,
        "reprise_le": datetime.now(UTC).isoformat(),
    }
