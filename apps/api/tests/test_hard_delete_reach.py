"""Ce qu'une suppression dure d'organisation emporte — mesuré, pas déduit.

Aucune route ne supprime durement : un projet part en `deleted_at`, et rien
n'expose la suppression d'une organisation. Mais `seed --reset` le fait, un
script d'exploitation le fera, et l'effacement RGPD d'un client le demandera.
La question « qu'est-ce que cela efface exactement ? » se pose alors, et la
réponse ne se lit pas dans le graphe des clés : elle dépend des actions
référentielles posées par trois migrations successives.

Deux faits que la mesure a sortis, et qui méritent d'être nommés :

* **le journal d'audit de l'organisation part avec elle.** C'est cohérent avec
  un effacement RGPD et contraire à la conservation d'une preuve. Les deux
  obligations tirent en sens inverse, et l'arbitrage est un choix
  d'exploitation, pas un défaut d'implémentation ;
* **les utilisateurs survivent, leurs appartenances non.** Un utilisateur qui
  n'appartenait qu'à cette organisation reste un compte capable de
  s'authentifier et rattaché à rien.

Ce fichier n'exprime pas d'avis sur ces deux points. Il les rend visibles et
fait tomber la suite si la **portée observable** change.

**Ce qu'il ne prouve pas, et la mesure qui le montre.** Le graphe est
sur-déterminé : plusieurs chemins mènent à la même suppression. Vérifié en
retirant `ondelete='CASCADE'` de `bills_of_quantities.organization_id` dans la
migration initiale — le DDL posé ne le porte plus, et pourtant la ligne
disparaît quand même, emportée par `projects` qui garde sa cascade vers
`organizations`, puis par `bills_of_quantities.project_id` qui garde la sienne
vers `projects`.

Ce test épingle donc **le résultat**, pas les arêtes qui le produisent. Il verra
une table changer de catégorie ; il ne dira pas quelle action référentielle a
bougé, et il ne tombera pas si l'une d'elles disparaît tant qu'une autre couvre
le même effet. Pour surveiller les arêtes elles-mêmes, c'est
`test_referential_action_drift.py` qui les confronte une par une au catalogue.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

#: Les tables entièrement vidées : tout leur contenu appartenait à l'organisation.
VIDEES = frozenset(
    {
        "bills_of_quantities",
        "boq_items",
        "estimate_versions",
        "estimates",
        "projects",
    }
)

#: Les tables partagées entre organisations, dont seule la part de la
#: supprimée disparaît.
AMPUTEES = frozenset(
    {
        "audit_events",
        "composite_components",
        "composite_prices",
        "memberships",
        "organization_settings",
        "organizations",
        "price_book_versions",
        "price_books",
        "price_items",
        "tax_rates",
    }
)

#: Les tables que la suppression ne touche pas du tout.
INTACTES = frozenset({"region_profiles", "users"})


def _comptes(session) -> dict[str, int]:
    noms = [
        table
        for table in session.bind.dialect.get_table_names(session.connection())
        if table != "alembic_version"
    ]
    return {nom: session.scalar(text(f'SELECT count(*) FROM "{nom}"')) or 0 for nom in noms}


@pytest.fixture()
def portee(seeded: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Compte chaque table avant et après la suppression dure d'une organisation.

    **La suppression passe par du SQL brut, et c'est essentiel.**
    `session.delete(organisation)` déclenche la cascade de l'ORM : SQLAlchemy
    supprime les enfants en Python, un par un, avant le parent. La base
    n'intervient jamais, et ses actions référentielles ne sont pas exercées.

    Première version de ce fichier : elle passait par l'ORM. Vérifié en
    débranchant `ondelete='CASCADE'` dans la migration initiale, d'abord sur
    `bills_of_quantities.project_id` puis sur `bills_of_quantities.organization_id` :
    le test restait vert dans les deux cas. Il aurait donc documenté la portée
    d'une cascade ORM en la présentant comme celle de la base — deux choses
    différentes, et c'est la seconde qui s'applique à un script d'exploitation,
    à `psql`, ou à un effacement RGPD exécuté hors de l'application.
    """
    from metreo_api.db import get_session_factory

    session = get_session_factory()()
    try:
        if session.bind.dialect.name == "sqlite":
            session.execute(text("PRAGMA foreign_keys=ON"))
        avant = _comptes(session)
        session.execute(
            text("DELETE FROM organizations WHERE id = :identifiant"),
            {"identifiant": seeded["organization_a"]},
        )
        session.commit()
        apres = _comptes(session)
    finally:
        session.close()
    return {nom: (avant[nom], apres.get(nom, 0)) for nom in avant if avant[nom]}


class TestTheReachIsExactlyWhatWasMeasured:
    def test_the_three_categories_cover_every_populated_table(
        self, portee: dict[str, tuple[int, int]]
    ) -> None:
        """Une table peuplée et non classée doit faire tomber, pas passer."""
        non_classees = set(portee) - VIDEES - AMPUTEES - INTACTES
        assert non_classees == set(), (
            f"tables peuplées et non classées : {sorted(non_classees)}. "
            "Rangez-les : vidée, amputée, ou intacte."
        )

    def test_the_emptied_tables_are_empty(self, portee: dict[str, tuple[int, int]]) -> None:
        restantes = {
            nom: apres for nom, (_avant, apres) in portee.items() if nom in VIDEES and apres
        }
        assert restantes == {}, f"ces tables devaient être vidées : {restantes}"

    def test_the_shared_tables_lost_something_but_not_everything(
        self, portee: dict[str, tuple[int, int]]
    ) -> None:
        fautives = {
            nom: (avant, apres)
            for nom, (avant, apres) in portee.items()
            if nom in AMPUTEES and not 0 < apres < avant
        }
        assert fautives == {}, (
            f"ces tables devaient perdre une part et pas tout : {fautives}. "
            "Tout perdre voudrait dire que la seconde organisation a été emportée."
        )

    def test_the_untouched_tables_are_untouched(self, portee: dict[str, tuple[int, int]]) -> None:
        bougees = {
            nom: (avant, apres)
            for nom, (avant, apres) in portee.items()
            if nom in INTACTES and avant != apres
        }
        assert bougees == {}, f"ces tables ne devaient pas bouger : {bougees}"


class TestTheTwoFactsWorthNaming:
    """Ils ne sont pas des défauts. Ils sont des propriétés, et elles engagent."""

    def test_the_audit_trail_goes_with_the_organisation(
        self, portee: dict[str, tuple[int, int]]
    ) -> None:
        """Cohérent avec un effacement RGPD, contraire à la conservation d'une preuve.

        Si cela devait changer — journal conservé au-delà de l'organisation —
        ce serait une décision d'exploitation, et ce test la rendrait visible.
        """
        avant, apres = portee["audit_events"]
        assert apres < avant, "le journal de l'organisation a survécu à sa suppression"

    def test_the_users_survive_without_their_memberships(
        self, portee: dict[str, tuple[int, int]]
    ) -> None:
        """Un compte peut rester capable de s'authentifier et rattaché à rien."""
        assert portee["users"][0] == portee["users"][1], "des utilisateurs ont été supprimés"
        avant, apres = portee["memberships"]
        assert apres < avant, "les appartenances ont survécu à l'organisation"
