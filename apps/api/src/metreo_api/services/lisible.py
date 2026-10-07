"""Écrire un nombre pour un lecteur belge, à la précision qu'il mérite.

**Le défaut que ce module corrige**, relevé sur une capture du parcours :

    4180.6822810844 mm      ± 26.3682553403 mm
    16.2046806767 m2        ± 0.1094736856 m2

Dix décimales affichées pour une valeur dont l'incertitude vaut vingt-six
millimètres. Le dixième de picomètre est écrit avec la même autorité que le
mètre, et le séparateur est un point anglo-saxon tandis que la valeur corrigée
juste en dessous porte la virgule que l'utilisateur a tapée.

**Pourquoi ce calcul vit ici et non dans le navigateur.** La même raison que
`calibration_de_plan.facteur_lisible`, et c'est une règle du dépôt : un nombre
destiné à être LU est rendu par le serveur, et l'écran ne le recalcule pas.
Deux arrondis — un en Python, un en TypeScript — finiraient par diverger d'un
chiffre, et c'est le genre d'écart qu'on ne voit jamais venir. En prime, la
règle devient éprouvable par la suite de tests du dépôt plutôt que par un
harnais de navigateur.

**La précision de CALCUL ne change pas.** `Amount` quantise toujours à dix
décimales, la base les conserve, et l'API rend toujours la valeur complète à
côté de sa version lisible. Ce module ne touche qu'à ce qui s'affiche.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

#: Les codes d'unité dont l'écriture usuelle n'est pas le code.
#:
#: `units.py` connaît `m2` parce qu'un code d'unité doit rester tapable au
#: clavier et stable en base. « m2 » affiché à un métreur n'est pas faux, il
#: est négligé — et sur un document qui sortira chez un client, la négligence
#: se voit.
EXPOSANTS: dict[str, str] = {
    "m2": "m²",
    "cm2": "cm²",
    "mm2": "mm²",
    "m3": "m³",
    "cm3": "cm³",
    "mm3": "mm³",
}

#: Combien de décimales afficher quand on ne connaît aucune incertitude.
#:
#: Deux : assez pour un montant ou une dimension en mètres, assez peu pour
#: qu'on ne prenne pas le chiffre pour une mesure de laboratoire. Le cas se
#: présente pour une valeur SAISIE par une personne, qui ne porte pas
#: d'incertitude calculée.
DECIMALES_SANS_INCERTITUDE = 2

#: Le plafond. Au-delà, l'affichage cesse d'être lisible quoi qu'en dise
#: l'incertitude — et une incertitude de 10⁻⁷ sur un plan n'existe pas.
DECIMALES_MAXIMALES = 6


def decimales_utiles(incertitude: Decimal | None) -> int:
    """À quelle décimale s'arrêter, compte tenu de ce qu'on ignore.

    La règle est celle de la métrologie : **l'incertitude garde deux chiffres
    significatifs, et la valeur s'arrête à la même décimale.** Écrire un
    chiffre de plus revient à affirmer une précision qu'on vient soi-même de
    déclarer absente.

    >>> decimales_utiles(Decimal("26.368"))   # ± 26 mm
    0
    >>> decimales_utiles(Decimal("1.45"))     # ± 1,5 mm
    1
    >>> decimales_utiles(Decimal("0.109"))    # ± 0,11 m²
    2

    Une incertitude nulle ou absente n'autorise pas tous les chiffres : elle
    signifie qu'on ne l'a pas calculée, pas qu'elle est nulle.
    """
    if incertitude is None or incertitude <= 0:
        return DECIMALES_SANS_INCERTITUDE
    # Le rang du premier chiffre significatif, puis un de plus pour en garder
    # deux. `log10` d'un Decimal passe par float : sans conséquence ici, on ne
    # cherche qu'un ordre de grandeur.
    rang = math.floor(math.log10(float(incertitude)))
    return max(0, min(DECIMALES_MAXIMALES, 1 - rang))


def nombre_francais(valeur: Decimal, decimales: int) -> str:
    """« 4 181 » et non « 4181.0000000000 ».

    Virgule décimale et espace insécable étroit comme séparateur de milliers,
    qui sont les conventions belges. L'espace est le caractère U+202F : un
    espace ordinaire laisserait un nombre se couper en deux en fin de ligne.

    Écrit à la main plutôt que par `locale` : la locale d'un conteneur dépend
    de l'image, et un nombre qui change d'écriture selon la machine qui l'a
    rendu est exactement ce qu'on ne veut pas.
    """
    arrondie = valeur.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    signe = "-" if arrondie < 0 else ""
    chiffres = f"{abs(arrondie):.{decimales}f}"
    entiere, _, fraction = chiffres.partition(".")

    groupes = []
    while len(entiere) > 3:
        groupes.insert(0, entiere[-3:])
        entiere = entiere[:-3]
    groupes.insert(0, entiere)
    entiere = " ".join(groupes)

    return f"{signe}{entiere},{fraction}" if fraction else f"{signe}{entiere}"


def unite_affichee(unite: str) -> str:
    """« m² » là où le code dit « m2 »."""
    return EXPOSANTS.get(unite, unite)


def quantite_lisible(valeur: Decimal, unite: str, *, incertitude: Decimal | None = None) -> str:
    """Une valeur et son unité, écrites pour être lues. Jamais pour être relues.

    Le résultat est destiné à l'affichage et au journal, pas au calcul : il
    porte des espaces insécables et un arrondi. La valeur exacte reste rendue
    à côté par l'API, et c'est elle qu'on reprend pour calculer.
    """
    return f"{nombre_francais(valeur, decimales_utiles(incertitude))} {unite_affichee(unite)}"


def incertitude_lisible(incertitude: Decimal, unite: str) -> str:
    """« ± 26 mm ». Arrondie à ses deux chiffres significatifs, comme la valeur."""
    decimales = decimales_utiles(incertitude)
    return f"± {nombre_francais(incertitude, decimales)} {unite_affichee(unite)}"


def nombre_francais_court(valeur: Decimal, decimales: int) -> str:
    """Comme `nombre_francais`, mais sans les zéros de fin.

    « 50 » plutôt que « 50,00 », et « 25,02 » reste « 25,02 ». La différence
    d'avec `nombre_francais` tient au LECTEUR : une valeur mesurée garde ses
    zéros parce qu'ils disent jusqu'où on la connaît — « 24,00 m² » n'est pas
    « 24 m² ». Un rapport d'échelle, lui, n'est pas une mesure : il n'a pas
    d'incertitude propre à annoncer, et ses zéros ne disent rien.
    """
    rendu = nombre_francais(valeur, decimales)
    if "," not in rendu:
        return rendu
    return rendu.rstrip("0").rstrip(",")
