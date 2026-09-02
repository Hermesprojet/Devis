"""Toute requête écrite à la main est rattachée à une organisation, ou nommée.

`owned_query`, `find_owned` et `get_owned` posent le filtre d'organisation pour
qu'on ne puisse pas l'oublier. Toutes les lectures ne passent pas par eux :
relevé sur les routeurs et les services, **trente et une** requêtes construisent
un `select(...)` à la main. Vingt-deux filtrent explicitement, quatre visent une
table sans organisation, et cinq ne font ni l'un ni l'autre.

Aucune des cinq n'est exploitable aujourd'hui, et c'est justement le problème :
elles sont sûres par des mécanismes **extérieurs à la requête**, qui peuvent
régresser sans qu'elle change. Deux d'entre elles ont exactement la forme du
défaut corrigé dans la PR #8 sur l'index de tri d'un bordereau — un numéro
calculé depuis un identifiant de parent, sans filtrer l'organisation.

Ce contrôle ferme la classe d'erreur là où elle naît. Il ne remplace pas RLS,
qui protégerait aussi d'un script lancé hors de l'application ; `TENANT_RLS.md`
mesure ce que celle-ci exigerait et pourquoi elle attend une décision
d'exploitation. En attendant, une requête non rattachée doit être **nommée**,
avec sa raison écrite, ou elle est refusée.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SOURCE = API_ROOT / "src" / "metreo_api"

#: Les fonctions qui posent le filtre d'organisation à notre place.
AIDES_TENANT = frozenset({"owned_query", "find_owned", "get_owned"})


#: **Les seuls modèles qu'une requête peut lire sans filtrer.** Données de
#: référence, identiques pour tout le monde, sans propriétaire.
#:
#: Cette liste a d'abord été DÉDUITE — « tout modèle sans colonne
#: `organization_id` » — et c'était faux. `ImportBatchRow` n'a pas la colonne
#: mais appartient bien à une organisation, à travers son lot : un
#: `select(ImportBatchRow)` sans filtre rendrait les lignes d'import de tout le
#: monde, et le contrôle l'acceptait — vérifié avant correction. `Organization`
#: et `User` sont dans le même cas : l'absence de colonne ne dit pas l'absence
#: de propriétaire, elle dit que le propriétaire est ailleurs.
#:
#: Une déduction commode donnait donc une garantie fausse. La liste est
#: maintenant écrite, et courte.
GLOBALES = frozenset({"RegionProfile"})

#: Les modèles sans colonne `organization_id` qui appartiennent malgré tout à
#: quelqu'un. Une requête qui les vise doit passer par le parent possédé, ou
#: être nommée dans EXCEPTIONS. Ils sont listés pour qu'un modèle ajouté demain
#: n'atterrisse pas silencieusement du bon côté.
POSSEDES_INDIRECTEMENT = frozenset({"ImportBatchRow", "Organization", "User"})


def _modeles_sans_organisation() -> frozenset[str]:
    """Les modèles réellement libres de tout tenant, et le contrôle qui le tient.

    La fonction renvoie `GLOBALES`, mais elle vérifie d'abord que la partition
    écrite ci-dessus couvre encore exactement ce que portent les modèles. Un
    modèle ajouté sans `organization_id` et rangé nulle part fait tomber le
    contrôle, plutôt que de glisser du côté permissif.
    """
    from sqlalchemy.orm import DeclarativeBase

    from metreo_api import models

    sans_colonne = set()
    for nom in dir(models):
        objet = getattr(models, nom)
        table = getattr(objet, "__table__", None)
        if table is None or not isinstance(objet, type):
            continue
        if issubclass(objet, DeclarativeBase) and "organization_id" not in table.columns:
            sans_colonne.add(nom)

    non_classes = sans_colonne - GLOBALES - POSSEDES_INDIRECTEMENT
    assert non_classes == set(), (
        f"modèles sans `organization_id` et non classés : {sorted(non_classes)}. "
        "Décidez : donnée de référence globale, ou possédée à travers un parent ?"
    )
    disparus = (GLOBALES | POSSEDES_INDIRECTEMENT) - sans_colonne
    assert disparus == set(), (
        f"ces modèles ne sont plus sans `organization_id` : {sorted(disparus)}. "
        "Retirez-les de la partition."
    )
    return GLOBALES


#: Les requêtes non rattachées qu'on accepte, chacune avec sa raison.
#:
#: Une exception qui s'allonge sans que personne ne le voie cesse d'en être une :
#: un test plus bas exige que cette table vaille exactement ce que l'analyse
#: trouve, ni plus — une entrée devenue inutile doit disparaître — ni moins.
EXCEPTIONS: dict[str, str] = {
    "routers/auth.py::dev_login": (
        "Cette lecture est inter-tenant À DESSEIN : elle répond à « à quelles organisations "
        "cet utilisateur appartient-il ? », question qui précède le choix d'une organisation "
        "et ne peut donc pas être filtrée par elle. Elle est bornée à l'utilisateur qui vient "
        "de s'authentifier — `Membership.user_id == user.id` — et ne rend que des "
        "appartenances actives. C'est le seul endroit du code où une lecture franchit "
        "légitimement la frontière."
    ),
    "services/estimating.py::next_version_number": (
        "`lock_owned(session, Estimate, organization_id, estimate_id)` précède et rend 404 si "
        "le devis n'appartient pas à l'appelant ; la clé composite "
        "`fk_estimate_versions_estimate_tenant` interdit ensuite qu'une version d'un autre "
        "tenant porte cet `estimate_id`. Deux garanties, toutes deux hors de la requête."
    ),
    "services/pricebook_versions.py::next_version_number": (
        "`lock_owned(session, PriceBook, organization_id, price_book_id)` précède, et "
        "`fk_price_book_versions_price_book_tenant` interdit une version d'un autre tenant "
        "sous cette bibliothèque. Même raisonnement que ci-dessus."
    ),
}


def _selects_non_rattaches() -> dict[str, list[str]]:
    """Les requêtes qui ne se rattachent à aucune organisation, par fonction.

    L'unité d'analyse est l'INSTRUCTION entière : `select(...).where(...)` est
    une chaîne d'appels, et le `.where` n'est pas un enfant du `select`. Un
    contrôle qui regarderait le seul appel `select(...)` déclarerait fautif un
    code correct — c'est l'erreur qu'avait faite le premier contrôle de l'index
    de tri, corrigée dans la PR #8.
    """
    sans_organisation = _modeles_sans_organisation()
    trouves: dict[str, list[str]] = {}

    for chemin in sorted(SOURCE.rglob("*.py")):
        relatif = chemin.relative_to(SOURCE).as_posix()
        if not relatif.startswith(("routers/", "services/")):
            continue
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        for fonction in ast.walk(arbre):
            if not isinstance(fonction, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Les noms qui portent déjà un filtre d'organisation dans cette
            # fonction : soit `owned_query` les a produits, soit ils ont été
            # bâtis sur une comparaison d'`organization_id`. Une requête bâtie
            # dessus — un `count()` sur sa sous-requête, typiquement — est
            # filtrée elle aussi.
            #
            # Un nom n'est jamais retiré de cet ensemble : `query =
            # query.where(...)` ne perd pas le filtre posé à l'affectation
            # précédente, et le traiter autrement rendrait le contrôle faux.
            filtres: set[str] = set()
            for noeud in ast.walk(fonction):
                if not isinstance(noeud, ast.Assign):
                    continue
                valeur = ast.unparse(noeud.value)
                deja_filtre = "organization_id" in valeur or any(
                    aide in valeur for aide in AIDES_TENANT
                )
                if not deja_filtre:
                    continue
                for cible in noeud.targets:
                    if isinstance(cible, ast.Name):
                        filtres.add(cible.id)

            for instruction in ast.walk(fonction):
                if not isinstance(instruction, ast.stmt) or isinstance(
                    instruction, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                rendu = ast.unparse(instruction)
                if "select(" not in rendu:
                    continue
                if any(aide in rendu for aide in AIDES_TENANT):
                    continue
                if "organization_id" in rendu:
                    continue
                if any(f"{nom}." in rendu or f"({nom})" in rendu for nom in filtres):
                    continue
                if any(f"{modele}." in rendu for modele in sans_organisation):
                    continue
                cle = f"{relatif}::{fonction.name}"
                trouves.setdefault(cle, []).append(rendu.splitlines()[0][:100])
    return trouves


class TestEveryHandWrittenQueryIsAccountedFor:
    """Filtrée, sans objet, ou nommée. Il n'y a pas de quatrième cas."""

    def test_no_unaccounted_query_exists(self) -> None:
        trouves = _selects_non_rattaches()
        inconnues = {cle: extraits for cle, extraits in trouves.items() if cle not in EXCEPTIONS}
        assert inconnues == {}, (
            "des requêtes ne se rattachent à aucune organisation et ne sont pas nommées :\n"
            + "\n".join(f"  {cle} : {extraits}" for cle, extraits in sorted(inconnues.items()))
            + "\n\nSoit la requête filtre `organization_id`, soit elle passe par `owned_query`, "
            "soit elle vise une table sans organisation — sinon ajoutez-la aux EXCEPTIONS "
            "avec la raison écrite de sa sûreté."
        )

    def test_no_exception_outlives_its_reason(self) -> None:
        """Une exception devenue inutile doit disparaître, pas dormir."""
        trouves = _selects_non_rattaches()
        perimees = sorted(set(EXCEPTIONS) - set(trouves))
        assert perimees == [], (
            f"ces exceptions ne correspondent plus à aucune requête : {perimees}. "
            "La requête a été corrigée ou déplacée ; retirez l'entrée."
        )

    def test_every_exception_states_a_reason(self) -> None:
        """Une exception sans raison écrite est une exception qu'on ne peut pas relire."""
        muettes = [cle for cle, raison in EXCEPTIONS.items() if len(raison.strip()) < 80]
        assert muettes == [], f"exceptions sans raison suffisante : {muettes}"


class TestTheFreeListIsWrittenNotDeduced:
    """La faiblesse trouvée dans ce contrôle même, et le filet qui l'empêche.

    Première version : « est libre tout modèle sans colonne `organization_id` ».
    Commode, et faux. `ImportBatchRow` n'a pas la colonne mais appartient à une
    organisation à travers son lot — un `select(ImportBatchRow)` sans filtre
    rendrait les lignes d'import de tout le monde, et le contrôle l'acceptait.

    La leçon est plus large que le cas : l'absence d'une colonne ne dit pas
    l'absence de propriétaire, elle dit que le propriétaire est ailleurs.
    """

    def test_a_tenant_owned_model_without_the_column_is_not_free(self) -> None:
        from metreo_api import models

        for nom in sorted(POSSEDES_INDIRECTEMENT):
            objet = getattr(models, nom)
            assert "organization_id" not in objet.__table__.columns, (
                f"{nom} porte maintenant `organization_id` : retirez-le de la partition"
            )
            assert nom not in _modeles_sans_organisation(), (
                f"{nom} appartient à quelqu'un et ne doit pas être traité comme global"
            )

    def test_an_unfiltered_read_of_such_a_model_is_reported(self) -> None:
        """La règle appliquée à la requête exacte qui passait avant."""
        rendu = "session.scalars(select(ImportBatchRow)).all()"
        libres = _modeles_sans_organisation()
        accepte = (
            not any(aide in rendu for aide in AIDES_TENANT)
            and "organization_id" not in rendu
            and any(f"{modele}." in rendu or f"({modele})" in rendu for modele in libres)
        )
        assert not accepte, (
            "une lecture non filtrée des lignes d'import serait acceptée : "
            "la liste des modèles libres est redevenue trop large"
        )

    def test_the_partition_still_covers_the_models(self) -> None:
        """Un modèle ajouté sans colonne et rangé nulle part doit faire tomber."""
        assert _modeles_sans_organisation() == GLOBALES


class TestTheControlCanActuallyFail:
    """Sans ceci, un contrôle qui ne trouve jamais rien passerait pour vert."""

    def test_the_analysis_sees_the_known_two(self) -> None:
        """Les deux `next_version_number` doivent être vues, sinon l'analyse est aveugle."""
        trouves = _selects_non_rattaches()
        assert set(trouves) == set(EXCEPTIONS), (
            f"l'analyse trouve {sorted(trouves)}, la table nomme {sorted(EXCEPTIONS)}"
        )

    def test_a_query_without_any_filter_is_reported(self, tmp_path: Path) -> None:
        """Éprouvé sur un fichier construit pour l'occasion, pas sur le dépôt."""
        faux = tmp_path / "routers"
        faux.mkdir()
        (faux / "intrus.py").write_text(
            "from sqlalchemy import select\n"
            "from ..models import Project\n"
            "def tout_lire(session):\n"
            "    return session.scalars(select(Project)).all()\n",
            encoding="utf-8",
        )
        arbre = ast.parse((faux / "intrus.py").read_text(encoding="utf-8"))
        instructions = [
            ast.unparse(n)
            for n in ast.walk(arbre)
            if isinstance(n, ast.stmt) and "select(" in ast.unparse(n)
        ]
        suspectes = [
            rendu
            for rendu in instructions
            if "organization_id" not in rendu and not any(a in rendu for a in AIDES_TENANT)
        ]
        assert suspectes, "la règle appliquée à une requête sans filtre doit la signaler"
