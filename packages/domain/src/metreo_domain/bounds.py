"""Bornes métier des valeurs numériques, et leur vérification.

Deux raisons d'exister, dans cet ordre.

**La première est métier.** Un métré de 10^15 m³ ou un rendement de 10^-9 ne
sont pas des valeurs rares : ce sont des fautes de saisie, une virgule
déplacée, une colonne mal associée à l'import. Les laisser entrer produit un
devis dont le total est absurde, et le rattraper en aval coûte plus cher que
de refuser la valeur au moment où elle est écrite.

**La seconde est technique.** Les colonnes décimales sont des
``NUMERIC(28, 10)`` : dix décimales, donc dix-huit chiffres avant la virgule,
soit un maximum absolu de 10^18. Dépasser cette capacité provoque une erreur
SQL illisible sur PostgreSQL — et, sur SQLite, où la valeur est stockée en
texte, aucune erreur du tout, ce qui est pire : les deux moteurs divergeraient
en silence. Les bornes ci-dessous sont choisies pour rester **très en deçà**
de cette capacité, de sorte qu'aucune combinaison acceptée ne puisse la
saturer ; :func:`headroom_report` le démontre chiffres en main.

Les bornes sont larges à dessein. Elles ne cherchent pas à deviner la taille
d'un chantier : elles écartent l'absurde, pas l'inhabituel.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from .errors import OutOfBoundsError

#: Capacité brute du stockage, dérivée de ``NUMERIC(28, 10)``.
SQL_PRECISION: Final[int] = 28
SQL_SCALE: Final[int] = 10

#: Borne **exclusive** : ``NUMERIC(28, 10)`` accepte jusqu'à
#: 999 999 999 999 999 999,999 999 999 9, soit tout ce qui est strictement
#: inférieur à 10^18. La valeur 10^18 elle-même ne tient pas.
SQL_MAX_ABS: Final[Decimal] = Decimal(10) ** (SQL_PRECISION - SQL_SCALE)


@dataclass(frozen=True, slots=True)
class Bound:
    """Plage acceptée pour un rôle métier donné."""

    name: str
    minimum: Decimal
    maximum: Decimal
    #: Nombre de décimales réellement utiles. Au-delà, la précision est du
    #: bruit : une masse volumique au dix-milliardième de kg/m³ n'existe pas.
    useful_decimals: int
    #: La borne basse est-elle atteignable, ou strictement exclue ? Un
    #: rendement de zéro est un diviseur nul, pas une petite valeur.
    minimum_inclusive: bool = True
    unit: str = ""

    def check(self, value: Decimal, *, label: str | None = None) -> Decimal:
        """Renvoie la valeur, ou lève :class:`OutOfBoundsError`."""
        subject = label or self.name
        too_small = value < self.minimum if self.minimum_inclusive else value <= self.minimum
        if too_small:
            raise OutOfBoundsError(
                f"{subject} : {value} est en dessous du minimum accepté "
                f"({'≥' if self.minimum_inclusive else '>'} {self.minimum}"
                f"{' ' + self.unit if self.unit else ''}).",
                bound=self.name,
                value=str(value),
                minimum=str(self.minimum),
                maximum=str(self.maximum),
            )
        if value > self.maximum:
            raise OutOfBoundsError(
                f"{subject} : {value} dépasse le maximum accepté "
                f"({self.maximum}{' ' + self.unit if self.unit else ''}). "
                "Vérifier l'unité et la position de la virgule.",
                bound=self.name,
                value=str(value),
                minimum=str(self.minimum),
                maximum=str(self.maximum),
            )
        return value


def _d(value: str) -> Decimal:
    return Decimal(value)


#: Quantité d'un poste. Le plus gros terrassement routier belge se compte en
#: millions de m³ ; un milliard laisse trois ordres de grandeur de marge.
QUANTITY: Final = Bound("quantity", _d("0"), _d("1e9"), useful_decimals=6)

#: Prix unitaire. Couvre aussi bien un joint à 0,01 € qu'un ouvrage d'art
#: chiffré au forfait.
UNIT_PRICE: Final = Bound("unit_price", _d("0"), _d("1e9"), useful_decimals=4, unit="EUR")

#: Total d'une ligne ou d'une estimation. Les plus gros marchés publics
#: européens sont de l'ordre de 10^10 EUR.
TOTAL: Final = Bound("total", _d("-1e12"), _d("1e12"), useful_decimals=2, unit="EUR")

#: Taux : frais de chantier, frais généraux, aléas, marge. 10 vaut 1000 %,
#: ce qui est déjà au-delà de toute pratique ; au-delà, c'est une faute de
#: saisie (un taux exprimé en pourcentage entier plutôt qu'en fraction).
RATE: Final = Bound("rate", _d("0"), _d("10"), useful_decimals=6)

#: Rendement : quantité produite par heure. Diviseur, donc strictement positif.
OUTPUT_RATE: Final = Bound(
    "output_rate", _d("0"), _d("1e6"), useful_decimals=6, minimum_inclusive=False
)

#: Masse volumique. L'osmium, le plus dense des éléments, est à 22 590 kg/m³ ;
#: aucun matériau de construction n'en approche. Diviseur ou multiplicateur
#: selon le sens, donc strictement positif.
DENSITY: Final = Bound(
    "density",
    _d("0"),
    _d("30000"),
    useful_decimals=4,
    minimum_inclusive=False,
    unit="kg/m³",
)

#: Coefficient de perte, consommation unitaire, taille d'équipe.
COEFFICIENT: Final = Bound("coefficient", _d("0"), _d("1e6"), useful_decimals=6)

#: Distance d'un trajet d'évacuation, en kilomètres. La demi-circonférence
#: terrestre est de 20 000 km.
DISTANCE_KM: Final = Bound("distance_km", _d("0"), _d("20000"), useful_decimals=3, unit="km")

#: Nombre de composants d'un sous-détail. Au-delà, ce n'est plus un
#: sous-détail mais un bordereau : il faut le découper.
MAX_COMPONENTS_PER_LINE: Final[int] = 200

#: Nombre de lignes d'une estimation.
MAX_LINES_PER_ESTIMATE: Final[int] = 20_000

ALL_BOUNDS: Final[tuple[Bound, ...]] = (
    QUANTITY,
    UNIT_PRICE,
    TOTAL,
    RATE,
    OUTPUT_RATE,
    DENSITY,
    COEFFICIENT,
    DISTANCE_KM,
)


def headroom_report() -> list[dict[str, object]]:
    """Marge entre chaque borne et la capacité de ``NUMERIC(28, 10)``.

    Sert la démonstration, et le test qui la vérifie : aucune borne acceptée ne
    doit pouvoir saturer le stockage, même combinée aux autres.
    """
    report: list[dict[str, object]] = []
    for bound in ALL_BOUNDS:
        widest = max(abs(bound.minimum), abs(bound.maximum))
        report.append(
            {
                "bound": bound.name,
                "maximum": bound.maximum,
                "sql_capacity": SQL_MAX_ABS,
                "orders_of_magnitude_spare": (SQL_MAX_ABS / widest if widest else SQL_MAX_ABS),
                "useful_decimals": bound.useful_decimals,
                "decimals_stored": SQL_SCALE,
            }
        )
    return report


def worst_case_stored_value() -> Decimal:
    """La plus grande valeur qu'une écriture acceptée puisse porter.

    Attention au raisonnement, il a failli être faux. On serait tenté de
    multiplier la quantité maximale par le prix unitaire maximal, puis par les
    majorations maximales : cela donne 1,5 × 10^22, très au-dessus de la
    capacité de stockage. Mais ce produit n'est jamais **écrit** — il est
    d'abord soumis à :data:`TOTAL`, qui le refuse.

    C'est donc la borne du total, et non celle des entrées, qui protège le
    stockage. Les bornes d'entrée écartent l'absurde saisie par saisie ; la
    borne du total écarte l'absurde né de leur combinaison. Un poste d'un
    milliard de m³ à un milliard d'euros l'unité est refusé, mais par le
    message « total de ligne dépasse le maximum accepté », pas par un message
    sur la quantité — qui, prise seule, reste dans sa plage.

    La plus grande valeur réellement écrite est donc le maximum des bornes
    portées par une colonne décimale.
    """
    return max(
        QUANTITY.maximum,
        UNIT_PRICE.maximum,
        abs(TOTAL.minimum),
        TOTAL.maximum,
        COEFFICIENT.maximum,
        DENSITY.maximum,
        OUTPUT_RATE.maximum,
        DISTANCE_KM.maximum,
        RATE.maximum,
    )


def check_total(value: Decimal, *, label: str = "total") -> Decimal:
    """Vérifie un montant calculé avant qu'il n'atteigne une colonne.

    À appeler sur tout résultat du moteur destiné à être persisté ou exporté.
    C'est le point de passage qui garantit l'invariant vérifié par les tests :
    aucune valeur écrite ne peut saturer ``NUMERIC(28, 10)``.
    """
    return TOTAL.check(value, label=label)
