"""Ce que le serveur sait lire, et ce que l'écran propose de lire.

Un seul sujet, et il ne tient dans aucun des autres fichiers de plans : la
CONCORDANCE entre deux listes qui vivent dans deux langages. Les tests de
`test_lecture_de_plan_api.py` éprouvent les routes, ceux de
`test_depot_de_plans.py` le dépôt ; aucun ne regarde le source du front.
"""

from __future__ import annotations

from metreo_api.services import lecture_de_plan


def test_the_screen_offers_to_read_exactly_the_types_the_server_can_read() -> None:
    """Les deux listes de types lisibles — serveur et écran — restent d'accord.

    **Le défaut que ce test ferme, dans les deux sens.** Le lien « Lire le
    plan » ne s'affichait que pour `image/vnd.dxf`, en dur, avec le
    commentaire « proposer « Lire le plan » sur un PDF promettrait un
    affichage que cette tranche ne sait pas produire ». La tranche suivante a
    appris à afficher un PDF, et la liste du serveur l'a dit — mais pas celle
    de l'écran. Résultat : un PDF déposé était lu par le serveur et **sans
    aucun passage vers l'écran de lecture**. Personne ne pouvait voir son
    plan.

    L'autre sens est aussi fermé : un lien proposé pour un type que le serveur
    refuse mène à un refus au lieu d'un plan, ce qui est pire qu'un lien
    absent — l'utilisateur a cliqué pour rien.

    La liste de l'écran est relue dans son SOURCE, et non recopiée ici : une
    troisième copie serait une troisième chose à tenir d'accord.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "web"
        / "src"
        / "components"
        / "DocumentsDuProjet.tsx"
    )
    texte = source.read_text(encoding="utf-8")
    declaration = re.search(r"const TYPES_DE_PLAN_LISIBLES = \[([^\]]*)\]", texte)
    assert declaration is not None, (
        "TYPES_DE_PLAN_LISIBLES a disparu de DocumentsDuProjet.tsx : le lien « Lire le plan » "
        "ne se décide plus par une liste, et ce test ne protège plus rien"
    )
    de_l_ecran = set(re.findall(r"'([^']+)'", declaration.group(1)))
    assert de_l_ecran == set(lecture_de_plan.TYPES_LISIBLES), (
        f"l'écran propose de lire {sorted(de_l_ecran)} et le serveur sait lire "
        f"{sorted(lecture_de_plan.TYPES_LISIBLES)}"
    )
