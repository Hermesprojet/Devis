"""Un nombre écrit pour être lu : la précision affichée dit ce qu'on sait.

Ce fichier éprouve une règle d'AFFICHAGE, et c'est pour cela qu'elle vit côté
serveur : une règle d'affichage qui n'est éprouvée par rien dérive au premier
coup de main, et personne ne s'en aperçoit avant de la voir sur un document
remis à un client.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metreo_api.services import lisible

# ---------------------------------------------------------------------------
# Combien de décimales
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("incertitude", "attendu"),
    [
        ("26.3682553403", 0),  # ± 26 mm : le millimètre est déjà du bruit
        ("1.4500000000", 1),  # ± 1,5 mm
        ("0.1094736856", 2),  # ± 0,11 m²
        ("0.0123456789", 3),
        ("0.0001234567", 5),
        ("150.0", 0),  # une incertitude énorme n'autorise pas des décimales
    ],
)
def test_la_precision_affichee_suit_l_incertitude(incertitude: str, attendu: int) -> None:
    """La règle de métrologie : deux chiffres significatifs sur l'incertitude.

    Et la valeur s'arrête à la même décimale. Afficher un chiffre de plus
    revient à affirmer une précision qu'on vient de déclarer absente.
    """
    assert lisible.decimales_utiles(Decimal(incertitude)) == attendu


def test_une_incertitude_absente_n_autorise_pas_tous_les_chiffres() -> None:
    """Ne pas l'avoir calculée n'est pas la même chose que de la savoir nulle."""
    assert lisible.decimales_utiles(None) == lisible.DECIMALES_SANS_INCERTITUDE
    assert lisible.decimales_utiles(Decimal("0")) == lisible.DECIMALES_SANS_INCERTITUDE


def test_une_incertitude_minuscule_reste_sous_le_plafond_d_affichage() -> None:
    """Six décimales suffisent : au-delà, on affiche du bruit de calcul."""
    assert lisible.decimales_utiles(Decimal("0.00000000001")) == lisible.DECIMALES_MAXIMALES


# ---------------------------------------------------------------------------
# L'écriture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valeur", "decimales", "attendu"),
    [
        ("4180.6822810844", 0, "4 181"),
        ("4180.6822810844", 1, "4 180,7"),
        ("16.2046806767", 2, "16,20"),
        ("0.5", 0, "1"),  # arrondi commercial, comme partout dans le dépôt
        ("1234567.89", 2, "1 234 567,89"),
        ("-12.5", 1, "-12,5"),
        ("999.95", 1, "1 000,0"),  # l'arrondi peut créer un groupe
    ],
)
def test_un_nombre_s_ecrit_en_francais(valeur: str, decimales: int, attendu: str) -> None:
    """Virgule décimale, et espace insécable étroit entre les milliers.

    L'espace est U+202F et non un espace ordinaire : un nombre coupé en deux
    en fin de ligne se relit mal, et sur un devis il se relit de travers.
    """
    assert lisible.nombre_francais(Decimal(valeur), decimales) == attendu


def test_le_separateur_de_milliers_est_insecable() -> None:
    """Dit explicitement, parce que c'est invisible dans un diff."""
    rendu = lisible.nombre_francais(Decimal("12345"), 0)
    assert " " not in rendu, "un espace ordinaire laisserait le nombre se couper"
    assert " " in rendu


# ---------------------------------------------------------------------------
# Les unités
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "affichee"),
    [("m2", "m²"), ("cm2", "cm²"), ("m3", "m³"), ("mm", "mm"), ("m", "m")],
)
def test_une_unite_s_affiche_avec_son_exposant(code: str, affichee: str) -> None:
    """« m2 » n'est pas faux, il est négligé — et la négligence se voit."""
    assert lisible.unite_affichee(code) == affichee


def test_une_unite_inconnue_est_rendue_telle_quelle() -> None:
    """Jamais de valeur inventée : un code non répertorié s'affiche brut."""
    assert lisible.unite_affichee("kg") == "kg"


# ---------------------------------------------------------------------------
# L'assemblage, sur les deux nombres de la capture
# ---------------------------------------------------------------------------


def test_la_longueur_de_la_capture_devient_lisible() -> None:
    """Le cas exact relevé à l'écran, avant et après."""
    assert (
        lisible.quantite_lisible(
            Decimal("4180.6822810844"), "mm", incertitude=Decimal("26.3682553403")
        )
        == "4 181 mm"
    )
    assert lisible.incertitude_lisible(Decimal("26.3682553403"), "mm") == "± 26 mm"


def test_la_surface_de_la_capture_devient_lisible() -> None:
    assert (
        lisible.quantite_lisible(
            Decimal("16.2046806767"), "m2", incertitude=Decimal("0.1094736856")
        )
        == "16,20 m²"
    )
    assert lisible.incertitude_lisible(Decimal("0.1094736856"), "m2") == "± 0,11 m²"


def test_une_valeur_saisie_par_une_personne_garde_deux_decimales() -> None:
    """Une valeur retenue n'a pas d'incertitude calculée : elle vient d'un relevé."""
    assert lisible.quantite_lisible(Decimal("3800.5"), "mm") == "3 800,50 mm"


# ---------------------------------------------------------------------------
# Le facteur d'échelle : un rapport, pas une mesure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valeur", "decimales", "attendu"),
    [
        ("50.0000000000", 2, "50"),
        ("25.0221108491", 2, "25,02"),
        ("3.3300000000", 3, "3,33"),
        ("0.0004321000", 6, "0,000432"),
    ],
)
def test_un_rapport_se_lit_sans_ses_zeros_de_fin(valeur: str, decimales: int, attendu: str) -> None:
    """La différence tient au lecteur, pas à l'arithmétique.

    Une valeur MESURÉE garde ses zéros : « 24,00 m² » dit jusqu'où on la
    connaît, « 24 m² » ne le dit plus. Un rapport d'échelle n'a pas
    d'incertitude propre à annoncer — ses zéros ne disent rien, et « 50,00 mm
    par point » se lit moins bien que « 50 mm par point ».
    """
    assert lisible.nombre_francais_court(Decimal(valeur), decimales) == attendu


def test_une_valeur_mesuree_garde_ses_zeros() -> None:
    """Le contre-exemple, pour que la distinction ne se perde pas."""
    assert (
        lisible.quantite_lisible(Decimal("24"), "m2", incertitude=Decimal("0.016")) == "24,000 m²"
    )
