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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from metreo_domain.money import canonical_text

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

    groupes: list[str] = []
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


def nombre_francais_tel_quel(texte: str) -> str:
    """Un nombre **déjà décidé** par le moteur, réécrit à la belge sans y toucher.

    **La différence d'avec `nombre_francais` tient à qui a arrondi.** Dans le
    monde du DOCUMENT — bordereau, étude, devis, PDF, aperçu imprimable — le
    nombre de décimales n'est pas une question d'affichage : c'est la
    `RoundingPolicy` de l'entreprise, et elle a déjà tranché en amont. Cette
    fonction ne quantise donc rien, n'arrondit rien et n'ajoute aucun zéro :
    elle remplace le point par une virgule et groupe les milliers.

    C'est l'invariant qui rend ce travail sûr : **la valeur ne change pas**,
    seule son écriture change.

    >>> nombre_francais_tel_quel("66053.78")
    '66 053,78'
    >>> nombre_francais_tel_quel("6.02")
    '6,02'
    >>> nombre_francais_tel_quel("-1250.5")
    '-1 250,5'
    >>> nombre_francais_tel_quel("")
    ''

    Une chaîne qui n'est pas un nombre revient telle quelle. Ce cas existe :
    le moteur rend « » pour un poste sans prix, et un libellé de section passe
    par les mêmes colonnes qu'un montant.
    """
    if not texte:
        return texte
    signe = ""
    reste = texte
    if reste[0] in "+-":
        signe = "-" if reste[0] == "-" else ""
        reste = reste[1:]
    entiere, point, fraction = reste.partition(".")
    if not entiere.isdigit() or (point and not fraction.isdigit()):
        return texte

    groupes: list[str] = []
    while len(entiere) > 3:
        groupes.insert(0, entiere[-3:])
        entiere = entiere[:-3]
    groupes.insert(0, entiere)
    entiere = " ".join(groupes)

    return f"{signe}{entiere},{fraction}" if fraction else f"{signe}{entiere}"


def quantite_de_document_lisible(valeur: Decimal, unite: str) -> str:
    """La quantité d'une ligne de bordereau, écrite comme le devis l'écrira.

    **Pourquoi ce n'est pas `quantite_lisible`.** Celle-ci sert le monde de la
    MESURE : son nombre de décimales vient de l'incertitude, et « 6,0200 m »
    dit jusqu'où la cote est connue. Une ligne de bordereau vit dans l'autre
    monde, celui du DOCUMENT : son nombre sera multiplié par un prix unitaire
    et imprimé sur un devis.

    **La règle : le nombre canonique du moteur, transcrit, sans rien y ajouter
    ni rien en retirer.** C'est exactement ce que l'étude de prix affiche et
    ce que le PDF imprime, parce que les deux reçoivent ce même texte du
    moteur (`canonical_text`). Une quantité s'écrit donc de la même façon sur
    les quatre surfaces où elle passe : l'aperçu d'une reprise, le bordereau,
    l'étude et le devis.

    **Le défaut que cette règle ferme a été trouvé sur un plan réel.** Cette
    fonction plafonnait à six décimales et complétait à deux. Une surface
    mesurée sur un balcon, reprise au bordereau, s'écrivait « 6,378795 m² »
    dans l'aperçu et au bordereau, et « 6,3787950927 » dans l'étude et sur le
    PDF remis au client : la même valeur, deux écritures, et un aperçu qui
    annonçait un nombre que la ligne ne portait pas. Arrondir cette quantité
    est une décision de chiffrage, ouverte dans `docs/ARRONDI_DES_DOCUMENTS.md` ;
    l'écrire de deux façons n'en était pas une.

    >>> quantite_de_document_lisible(Decimal("6.0200000000"), "m")
    '6,02 m'
    >>> quantite_de_document_lisible(Decimal("1250.5"), "m3")
    '1\u202f250,5 m³'
    >>> quantite_de_document_lisible(Decimal("6.3787950927"), "m2")
    '6,3787950927 m²'
    >>> quantite_de_document_lisible(Decimal("120.0000000000"), "t")
    '120 t'
    """
    return f"{nombre_francais_tel_quel(canonical_text(valeur))} {unite_affichee(unite)}"


#: Les deux unités d'une cotation angulaire. La cote stockée par un logiciel
#: de dessin est en RADIANS — 1,431 rad, c'est 82° — ; le recalcul de la
#: bibliothèque DXF rend des DEGRÉS. Voir `mesures_de_plan.unite_de_la_cote`.
UNITE_D_ANGLE = "rad"
UNITE_DE_DEGRE = "deg"

#: Les décimales d'une cotation DXF abrégée. Trois, sans zéro inutile : une
#: valeur recalculée depuis la géométrie n'est jamais ronde (« 629,9999999999999
#: mm »), et un métreur ne lit pas seize décimales. La valeur exacte reste
#: rendue à côté — l'abrégé ne la remplace jamais.
DECIMALES_D_UNE_COTE = 3


def _decimal_ou_rien(texte: str) -> Decimal | None:
    try:
        valeur = Decimal(texte)
    except (InvalidOperation, ValueError):
        return None
    return valeur if valeur.is_finite() else None


def cote_lisible(valeur: str, unite: str | None) -> str:
    """Une cotation lue dans un DXF, abrégée pour être lue.

    **Pourquoi le serveur l'écrit, et plus l'écran.** L'écran l'abrégeait avec
    un flottant (`toFixed(3)`) et l'écrivait avec un point : « 1.431 cm ». Sur
    un plan réel, cette ligne était un ANGLE de 82° — en radians, avec l'unité
    de longueur du document. Le serveur sait la famille et l'unité ; c'est lui
    qui écrit, comme pour une mesure PDF.

    >>> cote_lisible("80.0086861176836", "cm")
    '80,009 cm'
    >>> cote_lisible("629.9999999999999", "mm")
    '630 mm'
    >>> cote_lisible("1.431163279536011", "rad")
    '82°'
    >>> cote_lisible("81.99961571151513", "deg")
    '82°'
    >>> cote_lisible("5000", None)
    '5\u202f000'
    """
    nombre = _decimal_ou_rien(valeur)
    if nombre is None:
        return valeur
    if unite == UNITE_D_ANGLE:
        degres = nombre * Decimal(180) / Decimal(str(math.pi))
        return f"{nombre_francais_court(degres, DECIMALES_D_UNE_COTE)}°"
    if unite == UNITE_DE_DEGRE:
        return f"{nombre_francais_court(nombre, DECIMALES_D_UNE_COTE)}°"
    texte = nombre_francais_court(nombre, DECIMALES_D_UNE_COTE)
    return f"{texte} {unite_affichee(unite)}" if unite else texte


def cote_exacte(valeur: str, unite: str | None) -> str:
    """La même cotation, ENTIÈRE : c'est elle qu'on recoupe avec le fichier.

    Une transcription — virgule, milliers groupés —, sans rien arrondir.

    >>> cote_exacte("80.0086861176836", "cm")
    '80,0086861176836 cm'
    >>> cote_exacte("1.431163279536011", "rad")
    '1,431163279536011 rad'
    >>> cote_exacte("81.99961571151513", "deg")
    '81,99961571151513°'
    """
    texte = nombre_francais_tel_quel(valeur)
    if unite == UNITE_DE_DEGRE:
        return f"{texte}°"
    return f"{texte} {unite_affichee(unite)}" if unite else texte
