"""Le plafond d'octets de chaque route qui reçoit un fichier, et sa garde.

**Le défaut que ce module ferme.** Les routes de téléversement contrôlaient
`Content-Length` dans leur propre corps. À ce point du traitement, FastAPI a
déjà lu et analysé la totalité du corps multipart pour résoudre le paramètre
`UploadFile` : le contrôle s'exécutait donc APRÈS la lecture qu'il prétendait
éviter, et même après l'authentification. Leurs docstrings le disaient déjà —
« un raccourci, PAS une protection ». Le raccourci reste utile ; il lui
manquait la protection.

**Deux niveaux, qui ne se remplacent pas.** La garde de ce module borne les
octets qui entrent par le RÉSEAU. Les lecteurs spécialisés — `services/images`
pour un PNG, `services/classeur` pour un XLSX — bornent ce que ces octets
peuvent DÉVELOPPER une fois décompressés. Une archive de deux cents kilooctets
passe la première sans difficulté et se fait refuser par la seconde ; un
fichier de cinq cents mégaoctets n'atteint jamais la seconde. Supprimer l'une
laisserait l'autre découverte.

**Pourquoi un refus avant authentification.** La garde répond `413` sans
regarder qui appelle. C'est délibéré : lire cinq cents mégaoctets pour
découvrir ensuite que le jeton est invalide, c'est offrir à un anonyme le
travail que l'authentification devait protéger. La contrepartie est qu'un tel
refus ne peut PAS être inscrit au journal d'audit d'une organisation — il n'y
a pas encore d'identité à qui l'attribuer, et en inventer une serait pire que
de n'en inscrire aucune.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .services import images

#: Ce que `Content-Length` mesure EN PLUS du fichier : les bornes multipart,
#: les en-têtes de chaque partie, le nom du fichier, les champs de formulaire
#: qui l'accompagnent.
#:
#: Sans cette marge, un fichier pesant exactement le plafond serait refusé,
#: parce que son enveloppe le pousse au-delà. Mesuré : `documents.py` portait
#: déjà cette correction pour la même raison, et la valeur est reprise de là
#: plutôt que réinventée.
MARGE_ENVELOPPE_MULTIPART = 64 * 1024


@dataclass(frozen=True)
class CorpsBorne:
    """Une route qui reçoit un fichier, et le plafond qui la gouverne."""

    methode: str
    chemin: str
    #: Le nom du réglage — ou de la constante — qui donne le plafond. Nommé
    #: plutôt que recopié : un plafond en double diverge de son original.
    reglage: str
    #: Ce que la route attend réellement. `multipart/form-data` pour toutes
    #: celles d'aujourd'hui ; le champ existe pour que l'ajout d'un dépôt en
    #: corps brut soit une décision, et non un oubli.
    type_attendu: str

    def plafond(self, settings: Settings) -> int:
        if self.reglage == "images.OCTETS_MAXIMUM":
            return images.OCTETS_MAXIMUM
        valeur = getattr(settings, self.reglage, None)
        if not isinstance(valeur, int):
            raise RuntimeError(
                f"Le plafond « {self.reglage} » de {self.methode} {self.chemin} "
                "n'est pas un entier de configuration."
            )
        return valeur


#: Toutes les routes qui acceptent un corps de fichier. Fermé : le démarrage
#: refuse une route multipart qui n'y figure pas.
REGISTRE_DES_CORPS: tuple[CorpsBorne, ...] = (
    # Le logo a son propre plafond, bien plus bas que celui des documents : un
    # logo est un aplat, et deux mégaoctets sont déjà généreux. Lui appliquer
    # les 25 Mio des pièces jointes ouvrirait une porte sans usage.
    CorpsBorne(
        "PUT",
        "/api/v1/organization/logo",
        "images.OCTETS_MAXIMUM",
        "multipart/form-data",
    ),
    CorpsBorne(
        "POST",
        "/api/v1/documents/{document_id}/revisions",
        "max_upload_bytes",
        "multipart/form-data",
    ),
    # Le même plafond couvre le CSV et le classeur : c'est la même route, et
    # c'est son contenu qui décide du lecteur.
    CorpsBorne(
        "POST",
        "/api/v1/price-books/versions/{version_id}/imports/preview",
        "max_upload_bytes",
        "multipart/form-data",
    ),
)

_PAR_ROUTE: dict[tuple[str, str], CorpsBorne] = {
    (entree.methode, entree.chemin): entree for entree in REGISTRE_DES_CORPS
}


def borne_de(methode: str, chemin: str) -> CorpsBorne | None:
    return _PAR_ROUTE.get((methode, chemin))


def _recoit_un_fichier(route: Any) -> bool:
    """Vrai si la route déclare un `UploadFile` — donc si elle lit un corps.

    Le contrôle porte sur la SIGNATURE réelle et non sur une convention de nom :
    une route qui recevrait un fichier sans le dire dans son chemin resterait
    trouvée, et c'est précisément le cas qu'un registre tenu à la main manque.
    """
    from fastapi import UploadFile

    for dependance in getattr(getattr(route, "dependant", None), "body_params", []) or []:
        # Selon la version de FastAPI, l'annotation vit sur le champ lui-même
        # ou sur son `field_info`. On lit les deux plutôt que d'en supposer
        # une : se tromper ici ne casserait rien de visible — le registre
        # cesserait simplement de voir les routes qu'il doit garder.
        candidates = [
            getattr(dependance, "type_", None),
            getattr(getattr(dependance, "field_info", None), "annotation", None),
        ]
        for annotation in candidates:
            if annotation is UploadFile:
                return True
            # `list[UploadFile]`, ou une annotation enveloppée.
            for argument in getattr(annotation, "__args__", ()) or ():
                if argument is UploadFile:
                    return True
    return False


def verifier_le_registre(app: Any) -> list[str]:
    """Refuse de démarrer si une route reçoit un fichier sans plafond déclaré.

    Au DÉMARRAGE, comme le registre transactionnel voisin, et pour la même
    raison : une route de dépôt qu'on ajouterait sans plafond ne se
    remarquerait qu'en charge, le jour où quelqu'un y déverse un gigaoctet.
    Mieux vaut que l'application refuse de se lever en la nommant.

    Rend la liste des routes vérifiées, pour que le test de contrat puisse la
    comparer au registre plutôt que de la recopier.
    """
    from .transactions import parcourir

    exposees: list[str] = []
    oubliees: list[str] = []
    for methode, chemin, route in parcourir(app.routes):
        if not _recoit_un_fichier(route):
            continue
        exposees.append(f"{methode} {chemin}")
        if borne_de(methode, chemin) is None:
            oubliees.append(f"{methode} {chemin}")

    if oubliees:
        raise RuntimeError(
            "Routes recevant un fichier sans plafond déclaré dans "
            "`metreo_api/corps_bornes.py` — leur corps serait lu en entier "
            "avant tout refus :\n  " + "\n  ".join(sorted(oubliees))
        )

    # L'inverse compte aussi : une entrée qui ne correspond à aucune route
    # exposée est un plafond qui ne protège rien, et qui ferait croire le
    # contraire à qui lit le registre.
    inexistantes = sorted(
        f"{entree.methode} {entree.chemin}"
        for entree in REGISTRE_DES_CORPS
        if f"{entree.methode} {entree.chemin}" not in exposees
    )
    if inexistantes:
        raise RuntimeError(
            "Plafonds déclarés pour des routes qui n'existent pas :\n  " + "\n  ".join(inexistantes)
        )
    return sorted(exposees)


def routes_bornees(settings: Settings) -> Iterator[tuple[str, str, int]]:
    """(méthode, chemin, plafond effectif), pour la garde et pour les tests."""
    for entree in REGISTRE_DES_CORPS:
        yield entree.methode, entree.chemin, entree.plafond(settings)
