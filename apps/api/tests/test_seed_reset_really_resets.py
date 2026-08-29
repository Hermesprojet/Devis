"""`--reset` efface tout ce que le seed a semé, et rien d'autre.

**Le défaut mesuré.** Le nom de chaque organisation de démonstration était écrit
deux fois : dans l'appel qui la crée, et dans une liste séparée que `--reset`
consultait. Les deux avaient divergé. La liste nommait « Voiries & Égouttage
Martin SPRL (démo) », que rien ne crée, et ignorait « Wegenbouw Janssens NV
(demo) », que le seed crée bel et bien.

Reproduit avant correction, sur une base montée par les migrations : après un
`seed` puis un `seed --reset`, la base portait **deux** « Wegenbouw Janssens NV
(demo) ». Un reset de plus en aurait ajouté une troisième. La garantie affichée
— « n'efface que ce qu'il a semé » — était fausse dans l'autre sens : il
n'effaçait pas tout ce qu'il avait semé.

Le nom est maintenant écrit une fois et sert aux deux usages. Ce fichier tient
les deux moitiés de la garantie : le reset ramène à l'état d'un seul seed, et
aucune organisation ne peut être créée sous un nom que le reset ne connaît pas.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import func, select

API_ROOT = Path(__file__).resolve().parents[1]


def _organisations(session) -> list[str]:
    from metreo_api.models import Organization

    return sorted(session.scalars(select(Organization.name)).all())


class TestResetReturnsToASingleSeed:
    def test_a_reset_leaves_exactly_what_one_seed_creates(self, migrated: None) -> None:
        from metreo_api.db import get_session_factory
        from metreo_api.seed import SEEDED_ORGANIZATIONS, seed

        session = get_session_factory()()
        try:
            assert seed(session)["status"] == "seeded"
            apres_un_seed = _organisations(session)
            assert apres_un_seed == sorted(SEEDED_ORGANIZATIONS), apres_un_seed

            assert seed(session, reset=True)["status"] == "seeded"
            apres_reset = _organisations(session)
        finally:
            session.close()

        assert apres_reset == apres_un_seed, (
            f"le reset n'a pas ramené à l'état d'un seul seed : {apres_reset}"
        )

    def test_two_resets_do_not_accumulate(self, migrated: None) -> None:
        """Le défaut ajoutait une copie à chaque passage : deux resets le montrent."""
        from metreo_api.db import get_session_factory
        from metreo_api.models import Organization
        from metreo_api.seed import seed

        session = get_session_factory()()
        try:
            seed(session)
            seed(session, reset=True)
            seed(session, reset=True)
            doublons = session.execute(
                select(Organization.name, func.count())
                .group_by(Organization.name)
                .having(func.count() > 1)
            ).all()
        finally:
            session.close()
        assert doublons == [], f"organisations en double après deux resets : {doublons}"


class TestNoOrganisationEscapesTheResetList:
    """La divergence ne peut plus revenir, parce que le nom n'est écrit qu'une fois."""

    def test_every_created_name_comes_from_the_list(self) -> None:
        """Lu dans l'AST : un littéral en dur rouvrirait exactement le défaut.

        Le contrôle porte sur la SOURCE et non sur l'exécution : une deuxième
        organisation ajoutée demain avec un nom écrit à la main passerait le
        test d'exécution — elle serait créée, puis oubliée du reset — et c'est
        cette écriture-là qu'on refuse.
        """
        arbre = ast.parse((API_ROOT / "src" / "metreo_api" / "seed.py").read_text(encoding="utf-8"))
        litteraux: list[str] = []
        for noeud in ast.walk(arbre):
            if not (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)):
                continue
            if noeud.func.id != "_create_organization":
                continue
            for mot in noeud.keywords:
                if mot.arg == "name" and isinstance(mot.value, ast.Constant):
                    litteraux.append(str(mot.value.value))
        assert litteraux == [], (
            f"ces organisations sont créées avec un nom écrit en dur : {litteraux}. "
            "Passez par une constante, sinon `--reset` ne les connaîtra pas."
        )

    def test_the_list_names_nothing_imaginary(self, migrated: None) -> None:
        """Et aucun nom de la liste ne désigne une organisation que rien ne sème.

        Un nom fantôme est un ordre de suppression qui attend : si une vraie
        organisation le portait un jour, `--reset` l'emporterait.
        """
        from metreo_api.db import get_session_factory
        from metreo_api.seed import SEEDED_ORGANIZATIONS, seed

        session = get_session_factory()()
        try:
            seed(session)
            semees = set(_organisations(session))
        finally:
            session.close()
        fantomes = sorted(set(SEEDED_ORGANIZATIONS) - semees)
        assert fantomes == [], (
            f"la liste d'effacement nomme des organisations que rien ne sème : {fantomes}"
        )


@pytest.mark.parametrize("environnement", ["staging", "production"])
def test_the_reset_stays_refused_outside_development(
    environnement: str, monkeypatch: pytest.MonkeyPatch, migrated: None
) -> None:
    """La correction ne doit pas avoir élargi le champ d'action de la commande."""
    from metreo_api.config import get_settings
    from metreo_api.db import get_session_factory
    from metreo_api.seed import SeedRefused, seed

    monkeypatch.setenv("METREO_ENVIRONMENT", environnement)
    get_settings.cache_clear()
    session = get_session_factory()()
    try:
        with pytest.raises(SeedRefused):
            seed(session, reset=True)
    finally:
        session.close()
        get_settings.cache_clear()
