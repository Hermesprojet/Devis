"""Scénarios bas / probable / haut : ce que chaque hypothèse veut dire.

`sensitivity()` existe dans le domaine, mais son facteur n'a **volontairement
aucun sens imposé** : l'appelant décide ce qu'il fait varier. L'exposer tel
quel demanderait à l'utilisateur de deviner ce que « 1,1 » multiplie. Ce module
est le contrat manquant.

**La règle qui gouverne tout le reste : un facteur agit sur les ENTRÉES du
moteur, jamais sur le total.** Multiplier le total par 1,1 donnerait un nombre
qui ressemble à un résultat sans en être un — la TVA ne suit pas la même
proportion que le déboursé, une rotation arrondie ne se met pas à l'échelle, et
un forfait ne bouge pas du tout. Chaque hypothèse modifie donc les valeurs
d'entrée qu'elle concerne, puis **le moteur ordinaire recalcule tout** :
rotations et arrondis, frais de chantier, frais généraux, aléas, marge, taxes.
Aucune de ces règles n'est réécrite ici.

Corollaire : le scénario neutre reproduit la référence **par construction**, et
non parce qu'on aurait pris soin de le faire coïncider. Sans hypothèse, aucune
entrée n'est modifiée, et le calcul est littéralement le même.

**Les trois axes, et le sens de chacun.**

* `prix` — les coûts unitaires d'entrée. `+0,10` renchérit de 10 % le prix
  unitaire d'une ressource, le taux horaire d'une équipe, le coût par rotation
  et le coût kilométrique d'un transport. Filtrable par catégorie de ressource.
* `productivite` — le rendement des composants qui en ont un. `+0,10` veut dire
  « on produit 10 % de plus par heure », donc **moins d'heures**, donc un coût
  qui **baisse**. C'est le seul axe dont le signe s'inverse entre l'hypothèse et
  son effet, et c'est pour cela qu'il est écrit ici plutôt que supposé.
* `distance` — le trajet des transports. `+0,10` allonge de 10 % la distance,
  ce qui augmente le coût de CHAQUE rotation. Le nombre de rotations, lui, ne
  dépend pas de la distance mais du tonnage : l'effet passe donc par un nombre
  entier de rotations, et n'est pas proportionnel.

**Ce que les hypothèses ne touchent pas, et pourquoi.**

* Les **forfaits** (`lump_sum`). Un forfait n'a pas de prix unitaire : c'est un
  montant convenu. Lui appliquer une variation de prix unitaire reviendrait à
  renégocier un accord sous couvert de simulation.
* Les **taux commerciaux** — frais généraux, aléas, marge — et les **taux de
  taxe**. Ils sont recalculés par le moteur à partir des réglages de
  l'organisation, jamais modifiés ici. Faire varier une marge est une décision
  commerciale, pas une hypothèse de chiffrage.
* Les **quantités du bordereau**. Le métré est ce qu'il est ; en faire varier
  le volume est une autre question que le coût des ressources.

**Une hypothèse fabrique une entrée neuve, donc elle est bornée.** Les valeurs
saisies passent par les bornes du domaine ; celles qu'un scénario CRÉE n'étaient
contrôlées par personne. Le moteur, lui, ne vérifie aucune borne au moment de
calculer : une distance de 19 000 km — légale, elle entre en base — majorée de
10 % devenait un trajet de 20 900 km, au-delà de la demi-circonférence terrestre
que `DISTANCE_KM` déclare comme maximum, et le total qui en sortait avait
l'apparence d'un résultat. Chaque valeur mise à l'échelle est donc vérifiée
contre la borne de son rôle — `UNIT_PRICE` pour les prix, `OUTPUT_RATE` pour les
rendements, `DISTANCE_KM` pour les trajets — au moment où elle naît. Le scénario
concerné est refusé, en nommant le champ et l'hypothèse ; ses voisins, eux,
restent calculés.

**« Bas », « probable » et « haut » sont des libellés, pas une garantie.**
Rien n'oblige un scénario nommé « bas » à coûter moins cher : il suffit d'y
mettre une hausse de prix. Ce module ne réordonne rien et ne corrige rien ; il
SIGNALE que l'ordre obtenu ne suit pas les libellés, et laisse l'utilisateur
décider si c'est une erreur de saisie ou une hypothèse volontaire.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any

from metreo_domain import bounds
from metreo_domain.errors import DomainError
from metreo_domain.estimate import EstimateResult
from metreo_domain.money import WORKING_PRECISION, canonical_text, to_decimal
from metreo_domain.pricing import ResourceKind

#: Les trois axes, et le nom sous lequel l'API les reçoit.
AXES: tuple[str, ...] = ("prix", "productivite", "distance")

#: Les catégories de ressource qu'une variation de prix peut viser.
#:
#: DÉRIVÉES de `ResourceKind`, et non recopiées. La version précédente était une
#: liste écrite à la main dont le commentaire affirmait pourtant qu'elle venait
#: du domaine : ajouter une nature de ressource l'aurait laissée en arrière,
#: sans rien pour le signaler, et la catégorie neuve aurait été refusée comme
#: « inconnue » alors qu'elle existe.
CATEGORIES: tuple[str, ...] = tuple(kind.value for kind in ResourceKind)

#: Le libellé français de chaque catégorie, pour que l'écran n'ait pas à s'en
#: tenir une seconde table. Il vient lui aussi du domaine.
LIBELLES_DE_CATEGORIE: dict[str, str] = {kind.value: kind.label_fr for kind in ResourceKind}

#: Les libellés attendus, dans l'ordre où l'écran les présente.
SCENARIOS: tuple[str, ...] = ("bas", "probable", "haut")


class HypotheseRefusee(DomainError):
    """Une hypothèse qu'on ne sait pas appliquer, dite à qui l'a saisie."""

    def __init__(self, code: str, message: str, **contexte: object) -> None:
        super().__init__(message, **contexte)
        self.code = code


@dataclass(frozen=True)
class Hypotheses:
    """Les écarts relatifs d'un scénario. Tous nuls = le calcul de référence."""

    prix: Decimal = Decimal("0")
    #: Vide = toutes les catégories. Sinon, seules celles-ci sont touchées.
    prix_categories: tuple[str, ...] = ()
    productivite: Decimal = Decimal("0")
    distance: Decimal = Decimal("0")

    @property
    def neutre(self) -> bool:
        return self.prix == 0 and self.productivite == 0 and self.distance == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prix": canonical_text(self.prix),
            "prix_categories": list(self.prix_categories),
            "productivite": canonical_text(self.productivite),
            "distance": canonical_text(self.distance),
        }


def hypotheses_depuis(donnees: dict[str, Any] | None) -> Hypotheses:
    """Lit des hypothèses reçues, en refusant proprement ce qui n'en est pas.

    Chaque écart passe par la borne du domaine : un `-1` exact met un rendement
    à zéro, c'est-à-dire un diviseur nul, et ce refus doit porter le nom de
    l'hypothèse fautive plutôt que remonter en division par zéro trois couches
    plus bas.
    """
    donnees = donnees or {}
    valeurs: dict[str, Decimal] = {}
    for axe in AXES:
        brut = donnees.get(axe, 0)
        if brut is None or brut == "":
            valeurs[axe] = Decimal("0")
            continue
        try:
            valeur = to_decimal(brut)
        except (DomainError, ArithmeticError, TypeError, ValueError) as refus:
            # `to_decimal` ne lève PAS que des `DomainError` : une chaîne qui
            # n'est pas un nombre remonte en `decimal.InvalidOperation`, qui
            # descend d'`ArithmeticError`. Ne rattraper que `DomainError`
            # laissait « abc » traverser jusqu'au client en erreur serveur.
            raise HypotheseRefusee(
                "hypothese_illisible",
                f"L'hypothèse « {axe} » n'est pas un nombre : {brut!r}.",
                axe=axe,
            ) from refus
        if not valeur.is_finite():
            raise HypotheseRefusee(
                "hypothese_illisible",
                f"L'hypothèse « {axe} » doit être un nombre fini.",
                axe=axe,
            )
        bounds.SCENARIO_VARIATION.check(valeur, label=axe)
        valeurs[axe] = valeur

    categories = tuple(donnees.get("prix_categories") or ())
    inconnues = [c for c in categories if c not in CATEGORIES]
    if inconnues:
        raise HypotheseRefusee(
            "categorie_inconnue",
            f"Catégorie de ressource inconnue : {', '.join(inconnues)}.",
            categories=list(CATEGORIES),
        )
    return Hypotheses(
        prix=valeurs["prix"],
        prix_categories=categories,
        productivite=valeurs["productivite"],
        distance=valeurs["distance"],
    )


def _echelle(
    valeur: str | None,
    variation: Decimal,
    *,
    borne: bounds.Bound,
    sujet: str,
) -> str | None:
    """Applique un écart relatif à une valeur d'entrée sérialisée, et la borne.

    Rend une CHAÎNE, comme les specs en portent : elles voyagent en JSON et
    sont relues par `to_decimal`. Passer par un flottant ici perdrait
    exactement la précision que tout le moteur s'attache à conserver.

    **La valeur produite est vérifiée contre la borne de son rôle métier**, la
    même que celle qui garde la saisie et l'import. Une hypothèse fabrique une
    entrée neuve : rien ne l'a jamais contrôlée. Sans ce passage, une distance
    de 19 000 km majorée de 10 % devenait un trajet de 20 900 km — au-delà de
    la demi-circonférence terrestre, qui est précisément le maximum que
    `DISTANCE_KM` déclare — et le moteur, qui ne vérifie aucune borne au
    moment de calculer, rendait un total d'apparence sérieuse. Le scénario
    concerné est désormais refusé, avec le nom du champ et la valeur obtenue ;
    les scénarios voisins, eux, restent calculés.
    """
    if valeur is None:
        return None
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        echelle = to_decimal(valeur) * (Decimal(1) + variation)
    borne.check(echelle, label=sujet)
    return canonical_text(echelle)


def _composant_modifie(composant: dict[str, Any], hyp: Hypotheses) -> dict[str, Any]:
    """Un composant de sous-détail, avec les seules entrées que l'hypothèse vise.

    La correspondance entre un axe et les champs qu'il touche est le contrat de
    ce module ; elle est écrite ici une fois, et nulle part ailleurs.
    """
    modifie = dict(composant)
    type_ = composant.get("component_type")
    categorie = composant.get("resource_kind")
    nom = composant.get("label") or "composant"
    vise_le_prix = hyp.prix != 0 and (not hyp.prix_categories or categorie in hyp.prix_categories)

    if type_ == "consumption":
        if vise_le_prix:
            modifie["unit_price"] = _echelle(
                composant.get("unit_price"),
                hyp.prix,
                borne=bounds.UNIT_PRICE,
                sujet=f"prix unitaire de « {nom} » après variation de prix",
            )
        # Ni la consommation ni le taux de perte : ce sont des quantités de
        # matière, pas des prix, et aucune hypothèse de ce bloc ne les vise.

    elif type_ == "output_rate":
        if vise_le_prix:
            modifie["hourly_rate"] = _echelle(
                composant.get("hourly_rate"),
                hyp.prix,
                borne=bounds.UNIT_PRICE,
                sujet=f"taux horaire de « {nom} » après variation de prix",
            )
        if hyp.productivite != 0:
            # Le SEUL endroit où le signe s'inverse. Produire 10 % de plus par
            # heure, c'est un rendement multiplié par 1,10 — et comme les
            # heures valent quantité ÷ rendement, c'est un coût qui BAISSE.
            modifie["output_rate"] = _echelle(
                composant.get("output_rate"),
                hyp.productivite,
                borne=bounds.OUTPUT_RATE,
                sujet=f"rendement de « {nom} » après variation de productivité",
            )
        # `crew_size` est un effectif, pas un rendement : le faire varier
        # changerait la composition de l'équipe, ce qui est une autre décision.

    elif type_ == "rotation":
        if vise_le_prix:
            modifie["cost_per_rotation"] = _echelle(
                composant.get("cost_per_rotation"),
                hyp.prix,
                borne=bounds.UNIT_PRICE,
                sujet=f"coût par rotation de « {nom} » après variation de prix",
            )
            modifie["rate_per_km"] = _echelle(
                composant.get("rate_per_km"),
                hyp.prix,
                borne=bounds.UNIT_PRICE,
                sujet=f"coût kilométrique de « {nom} » après variation de prix",
            )
        if hyp.distance != 0:
            # Modifiée ICI, sur l'entrée : le moteur en déduira ensuite le coût
            # par rotation, puis le multipliera par un nombre ENTIER de
            # rotations. Appliquer l'écart au montant final court-circuiterait
            # cet arrondi et rendrait un chiffre qui n'existe pas.
            modifie["distance_km"] = _echelle(
                composant.get("distance_km"),
                hyp.distance,
                borne=bounds.DISTANCE_KM,
                sujet=f"distance de « {nom} » après variation de distance",
            )
        # Ni la charge utile ni la masse volumique : ce sont des propriétés du
        # camion et du matériau, pas des hypothèses de conjoncture.

    # `lump_sum` n'apparaît pas : un forfait est un montant convenu, sans prix
    # unitaire. Lui appliquer une variation de prix reviendrait à renégocier un
    # accord sous couvert de simulation.
    return modifie


def _ligne_modifiee(spec: dict[str, Any], hyp: Hypotheses) -> dict[str, Any]:
    """Une ligne de bordereau, avec ses entrées de prix ajustées."""
    tarif = spec.get("pricing")
    if not tarif:
        # Une section, ou un poste SANS PRIX. Il le reste : le valoriser à zéro
        # parce qu'on simule serait exactement la faute que le moteur refuse.
        return spec

    modifiee = dict(spec)
    if tarif["mode"] == "composite":
        modifiee["pricing"] = {
            **tarif,
            "components": [_composant_modifie(c, hyp) for c in tarif["components"]],
        }
    # Un prix de bibliothèque posé sur une ligne ne porte PAS sa catégorie de
    # ressource : celle-ci vit sur la fiche du prix, pas sur l'entrée du
    # moteur. Une variation ciblée par catégorie ne peut donc pas l'atteindre,
    # et ne prétend pas le faire ; une variation générale, si.
    elif tarif["mode"] == "library_price" and hyp.prix != 0 and not hyp.prix_categories:
        modifiee["pricing"] = {
            **tarif,
            "unit_price": _echelle(
                tarif.get("unit_price"),
                hyp.prix,
                borne=bounds.UNIT_PRICE,
                sujet=(
                    f"prix de bibliothèque de « {spec.get('designation') or spec.get('code')} »"
                    " après variation de prix"
                ),
            ),
        }
    return modifiee


def appliquer(specs: list[dict[str, Any]], hyp: Hypotheses) -> list[dict[str, Any]]:
    """Les mêmes lignes, avec les entrées que les hypothèses modifient.

    Rend une structure NEUVE : les specs reçues viennent d'un instantané gelé
    ou d'une lecture en base, et les modifier sur place ferait d'une simulation
    une écriture.
    """
    if hyp.neutre:
        # Aucune copie, aucune arithmétique : le scénario neutre est le calcul
        # de référence, littéralement, et pas une reproduction fidèle.
        return specs
    return [_ligne_modifiee(spec, hyp) for spec in specs]


@dataclass(frozen=True)
class Chiffrage:
    """Ce qu'un scénario a produit, ou la raison pour laquelle il a échoué.

    Les deux cas vivent dans la même structure, et c'est délibéré : un scénario
    refusé ne doit pas faire disparaître les deux autres, et l'écran a besoin
    de savoir POUR QUEL scénario il n'a rien à montrer.
    """

    nom: str
    hypotheses: Hypotheses
    resultat: EstimateResult | None = None
    refus: dict[str, Any] | None = None

    @property
    def calcule(self) -> bool:
        return self.resultat is not None


def evaluer(
    specs: list[dict[str, Any]],
    hypotheses_par_scenario: dict[str, Hypotheses],
    *,
    currency: str,
    markup: Any,
    taxes: Any,
    missing_price_policy: Any,
) -> list[Chiffrage]:
    """Recalcule chaque scénario avec le moteur ORDINAIRE.

    Aucun raccourci : les mêmes `inputs_from_specs` et `compute_estimate` que
    le chiffrage de référence, avec les mêmes taux et la même politique de prix
    manquant. Ce module ne sait pas calculer un devis — il sait seulement quelles
    entrées modifier avant de laisser le moteur faire son travail.

    Un scénario qui échoue est capturé et rendu comme refus. Laisser l'exception
    remonter emporterait les scénarios voisins, qui n'ont rien à se reprocher.
    """
    from metreo_domain.estimate import compute_estimate

    from .estimating import PricingInputError, inputs_from_specs

    chiffrages: list[Chiffrage] = []
    for nom, hyp in hypotheses_par_scenario.items():
        try:
            inputs = inputs_from_specs(appliquer(specs, hyp), currency)
            resultat = compute_estimate(
                inputs,
                currency=currency,
                markup=markup,
                taxes=taxes,
                missing_price_policy=missing_price_policy,
            )
        except PricingInputError as refus:
            chiffrages.append(
                Chiffrage(
                    nom=nom,
                    hypotheses=hyp,
                    refus={
                        "code": "lignes_non_chiffrables",
                        "message": "Certaines lignes ne peuvent pas être chiffrées "
                        "sous cette hypothèse.",
                        "problems": refus.problems,
                    },
                )
            )
        except DomainError as refus:
            # Une borne franchie APRÈS application : un prix multiplié qui sort
            # de sa plage, un rendement devenu inexploitable. Le refus nomme
            # l'hypothèse, sans quoi l'utilisateur chercherait dans son
            # bordereau une faute qui vient de sa saisie.
            chiffrages.append(
                Chiffrage(
                    nom=nom,
                    hypotheses=hyp,
                    refus={**refus.to_dict(), "scenario": nom},
                )
            )
        else:
            chiffrages.append(Chiffrage(nom=nom, hypotheses=hyp, resultat=resultat))
    return chiffrages


def ordre_incoherent(chiffrages: list[Chiffrage]) -> bool:
    """Les totaux ne suivent-ils pas l'ordre que les libellés suggèrent ?

    On le SIGNALE, on ne le corrige pas. Réordonner les colonnes ferait
    disparaître l'information la plus utile : que les hypothèses saisies ne
    disent pas ce que leur nom laisse croire. Il peut s'agir d'une faute de
    saisie comme d'une intention — c'est à l'utilisateur de trancher.
    """
    par_nom = {c.nom: c for c in chiffrages}
    totaux: list[Decimal] = []
    for nom in SCENARIOS:
        chiffrage = par_nom.get(nom)
        if chiffrage is None or chiffrage.resultat is None:
            return False  # un scénario manque : rien à conclure sur l'ordre
        totaux.append(chiffrage.resultat.total_selling_price_ht.amount)
    return not (totaux[0] <= totaux[1] <= totaux[2])


def _ecart(montant: Decimal, reference: Decimal, arrondi: Any) -> dict[str, str | None]:
    """L'écart d'un scénario au scénario probable, en valeur et en pourcentage.

    **Deux formes de la valeur absolue, comme partout ailleurs dans ce dépôt** :
    le décimal EXACT pour qui recalcule, et la forme arrondie selon la politique
    de l'organisation pour qui lit. Un écart de productivité ne tombe pas juste
    — 750,00 ÷ 1,1 est périodique — et l'écran affichait alors
    « -68.18181818181818181818181818 EUR ». Arrondir dans le navigateur ferait
    diverger ce chiffre du devis au premier centime ; l'arrondi vient donc d'ici,
    avec la même politique que les totaux.

    Le pourcentage vaut `None` quand la référence est nulle : une division par
    zéro n'a pas de résultat, et rendre « 0 % » ou « ∞ » ferait passer une
    absence d'information pour une information.
    """
    absolu = montant - reference
    rendu: dict[str, str | None] = {
        "absolu": canonical_text(absolu),
        "absolu_display": str(arrondi.quantize(absolu)),
    }
    if reference == 0:
        rendu["pourcentage"] = None
        return rendu
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        pourcentage = absolu / reference * Decimal(100)
    rendu["pourcentage"] = canonical_text(pourcentage.quantize(Decimal("0.01")))
    return rendu


def simuler(
    session: Any,
    *,
    estimate: Any,
    version: Any,
    hypotheses_par_scenario: dict[str, Hypotheses],
    inclure_couts: bool,
    inclure_marge: bool,
) -> dict[str, Any]:
    """Chiffre les scénarios d'une version, SANS rien écrire.

    Les entrées viennent de `compute_version`, qui tranche déjà la seule
    question qui compte ici : une version **gelée** rend son instantané, une
    version **brouillon** relit les données courantes. Refaire ce choix ici
    ouvrirait la porte à ce qu'il soit fait différemment des deux côtés, et
    c'est exactement ainsi qu'un devis gelé finit par bouger.
    """
    from metreo_domain.estimate import MissingPricePolicy

    from .estimating import (
        markup_from_dict,
        rounding_from_dict,
        taxes_from_list,
        totals_for_display,
    )

    # L'instantané porte les entrées ET les taux : pour une version gelée, ce
    # sont ceux du jour du gel, jamais les réglages d'aujourd'hui.
    _, instantane = _calculer(session, estimate=estimate, version=version)

    specs = instantane["lines"]
    currency = instantane["currency"]
    markup = markup_from_dict(instantane["markup"])
    taxes = taxes_from_list(instantane["taxes"])
    rounding = rounding_from_dict(instantane["rounding"])
    politique = MissingPricePolicy(instantane["missing_price_policy"])

    chiffrages = evaluer(
        specs,
        hypotheses_par_scenario,
        currency=currency,
        markup=markup,
        taxes=taxes,
        missing_price_policy=politique,
    )

    par_nom = {c.nom: c for c in chiffrages}
    probable = par_nom.get("probable")
    reference = (
        probable.resultat.total_selling_price_ht.amount
        if probable is not None and probable.resultat is not None
        else None
    )

    rendus: list[dict[str, Any]] = []
    for chiffrage in chiffrages:
        entree: dict[str, Any] = {
            "nom": chiffrage.nom,
            "hypotheses": chiffrage.hypotheses.to_dict(),
        }
        resultat = chiffrage.resultat
        if resultat is None:
            entree["status"] = "refused"
            entree["refus"] = chiffrage.refus
            rendus.append(entree)
            continue
        entree["status"] = "success"
        entree["totaux"] = totals_for_display(
            resultat, rounding, include_costs=inclure_couts, include_margin=inclure_marge
        )
        # Les lignes sans prix restent NOMMÉES, jamais comptées pour zéro : la
        # simulation ne doit pas faire disparaître ce qui bloque le gel.
        entree["lignes_sans_prix"] = list(resultat.missing_price_line_ids)
        entree["bloquant"] = resultat.blocking
        if reference is not None:
            entree["ecart"] = _ecart(resultat.total_selling_price_ht.amount, reference, rounding)
        rendus.append(entree)

    return {
        "from_snapshot": version.status == "frozen",
        "includes_internal_costs": inclure_couts,
        "includes_margin_steps": inclure_marge,
        "currency": currency,
        "scenarios": rendus,
        "ordre_incoherent": ordre_incoherent(chiffrages),
        "categories": LIBELLES_DE_CATEGORIE,
    }


def _calculer(session: Any, *, estimate: Any, version: Any) -> tuple[Any, dict[str, Any]]:
    from .estimating import compute_version

    return compute_version(session, estimate=estimate, version=version)
