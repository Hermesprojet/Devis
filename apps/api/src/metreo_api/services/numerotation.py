"""Le motif de numérotation des devis : le valider, l'afficher, l'appliquer.

Un motif est saisi par un humain dans les réglages. La première version
l'appliquait dans un `try`, et retombait en silence sur `DEV-{year}-{sequence:04d}`
quand il ne rendait rien d'utilisable. C'était un mauvais compromis : une
entreprise qui a saisi `FACT-{sequenc}` — une lettre de trop — croit émettre
sous son propre format et reçoit des numéros qui ne lui ressemblent pas, sans
qu'aucun écran ne le dise. Le devis part chez le client avec ce numéro-là.

Trois moments, une seule règle :

* à la CONFIGURATION, un motif inutilisable est refusé, et l'écran en montre
  un aperçu avant d'enregistrer ;
* à l'ÉMISSION, un motif historique devenu illisible fait REFUSER l'émission,
  avec le motif fautif nommé — jamais un numéro de secours ;
* quand rien n'est configuré, le motif par défaut s'applique, et c'est le seul
  repli admis.
"""

from __future__ import annotations

from string import Formatter

from metreo_domain.errors import DomainError

#: Ce que rend une organisation qui n'a rien choisi. Le seul repli admis.
MOTIF_PAR_DEFAUT = "DEV-{year}-{sequence:04d}"

#: Les deux seuls champs qu'un motif peut nommer.
CHAMPS = ("year", "sequence")

#: La colonne fait 60 caractères ; un numéro rendu plus long ne s'écrirait pas.
LONGUEUR_MAXIMALE = 60

#: Le rang et l'année de l'aperçu. Un aperçu doit montrer un numéro PLAUSIBLE,
#: pas le prochain réellement alloué : le calculer supposerait un verrou de
#: séquence pour un simple affichage.
ANNEE_APERCU = 2026
RANG_APERCU = 7


class MotifInvalide(DomainError):
    """Un motif que l'on refuse, en disant lequel et pourquoi."""

    def __init__(self, motif: str, raison: str) -> None:
        super().__init__(
            f"Le motif de numérotation « {motif} » est inutilisable : {raison}",
            pattern=motif,
            reason=raison,
        )
        self.code = "quote_number_pattern_invalid"


def verifier(motif: str | None) -> str:
    """Rend le motif à retenir, ou lève `MotifInvalide` en le nommant.

    Un motif vide vaut « rien de choisi » : c'est le défaut qui s'applique, et
    non un refus. Effacer le champ doit revenir au comportement d'origine.
    """
    candidat = (motif or "").strip()
    if not candidat:
        return MOTIF_PAR_DEFAUT

    nommes = ", ".join(f"{{{champ}}}" for champ in CHAMPS)
    try:
        # `parse` lève DÉJÀ sur une accolade non fermée : l'appeler hors du
        # `try` faisait remonter un `ValueError` nu jusqu'à l'appelant, au lieu
        # du refus nommé que cette fonction promet. Mesuré sur « DEV-{year ».
        champs = [nom for _t, nom, _f, _c in Formatter().parse(candidat) if nom is not None]
    except ValueError as erreur:
        raise MotifInvalide(candidat, f"il est mal formé ({erreur})") from erreur

    if any(nom == "" or nom.isdigit() for nom in champs):
        raise MotifInvalide(
            candidat,
            f"il utilise un champ sans nom ; écrivez {nommes}",
        )
    inconnus = sorted({nom for nom in champs if nom not in CHAMPS})
    if inconnus:
        cites = ", ".join(f"{{{nom}}}" for nom in inconnus)
        raise MotifInvalide(candidat, f"il nomme {cites}, alors que seuls {nommes} existent")

    try:
        rendu = candidat.format(year=ANNEE_APERCU, sequence=RANG_APERCU)
    except (IndexError, KeyError, ValueError) as erreur:
        # Un format impossible — « {sequence:!!} » — passe la lecture des
        # champs et n'échoue qu'à l'application.
        raise MotifInvalide(candidat, f"il ne s'applique pas ({erreur})") from erreur

    if not rendu.strip():
        raise MotifInvalide(candidat, "il ne produit aucun texte")
    if len(rendu) > LONGUEUR_MAXIMALE:
        raise MotifInvalide(
            candidat,
            f"il produit un numéro de {len(rendu)} caractères, "
            f"au-delà des {LONGUEUR_MAXIMALE} que la fiche accepte",
        )
    if "sequence" not in champs:
        # Sans rang, deux devis de la même année porteraient le MÊME numéro, et
        # la seconde émission serait refusée par `uq_issued_quote_number` — un
        # blocage inexplicable, découvert le jour où l'on émet le deuxième.
        raise MotifInvalide(
            candidat,
            "il ne contient pas {sequence} : tous les devis d'une même année "
            "porteraient le même numéro",
        )
    return candidat


def apercu(motif: str | None) -> str:
    """Le numéro que ce motif produirait, ou une phrase disant qu'il est refusé."""
    try:
        return verifier(motif).format(year=ANNEE_APERCU, sequence=RANG_APERCU)
    except MotifInvalide as refus:
        return f"— {refus.message}"


def rendre(motif: str | None, *, annee: int, rang: int) -> str:
    """Le numéro, ou `MotifInvalide` — jamais un numéro de secours silencieux."""
    return verifier(motif).format(year=annee, sequence=rang)
