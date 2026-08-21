"""Le vérificateur de skills doit pouvoir échouer.

Un skill est lu par un agent *avant* qu'il touche au code : une valeur périmée
y est suivie, pas seulement affichée. `scripts/check_skills.py` existe pour
refuser ces valeurs — encore faut-il qu'il les voie.

Il ne les voyait pas. Deux angles morts se recouvraient :

* les expressions reconnaissaient « N tests », « N routes » et « N tables »,
  mais ni « N passed », ni « N files », ni « no issues found in N source
  files » — c'est-à-dire aucune des sorties d'outil réellement recopiées ;
* toute ligne commençant par `#` était sautée comme un titre Markdown, y
  compris à l'intérieur d'un bloc de code, là où `#` introduit un commentaire
  de shell — exactement l'endroit où les compteurs périmés se trouvaient.

Le résultat était un vert de complaisance : `8 skills conformes` sur un skill
qui annonçait 61 tests là où il y en a plus de deux cents.

Chaque règle ajoutée ici est falsifiable : la neutraliser fait tomber au moins
un de ces tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load("check_skills")

FRONTMATTER = """---
name: {name}
description: Un skill de test, écrit pour faire échouer le vérificateur.
---

# Titre

{body}

## Signaux d'alerte

- rien.
"""


def write_skill(root: Path, name: str, body: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(FRONTMATTER.format(name=name, body=body), encoding="utf-8")
    return directory


class TestVolatileCounters:
    """Les sorties d'outil recopiées dans un skill sont refusées."""

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("Le domaine rend 61 passed.", id="passed"),
            pytest.param("Il reste 3 skipped.", id="skipped"),
            pytest.param("On observe 2 failed.", id="failed"),
            pytest.param("45 files already formatted", id="files"),
            pytest.param("no issues found in 33 source files", id="source-files"),
            pytest.param("La suite compte 288 tests.", id="tests"),
            pytest.param("Il y a 51 routes montées.", id="routes"),
            pytest.param("Le schéma porte 19 tables.", id="tables"),
            pytest.param("Le workflow comporte 5 jobs.", id="jobs"),
            pytest.param("La tête Alembic est e2be18fcac1b.", id="révision"),
        ],
    )
    def test_a_frozen_figure_in_prose_is_refused(self, tmp_path: Path, line: str) -> None:
        directory = write_skill(tmp_path, "faux-skill", line)
        problems = checker.lint_skill(directory)
        assert problems, f"non détecté : {line}"

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("python -m pytest -q    # attendu : 61 passed", id="passed"),
            pytest.param("ruff format --check .  # 45 files already formatted", id="files"),
            pytest.param("mypy src  # no issues found in 33 source files", id="source-files"),
        ],
    )
    def test_a_frozen_figure_inside_a_code_block_is_refused(
        self, tmp_path: Path, line: str
    ) -> None:
        """L'angle mort d'origine : `#` ouvre un commentaire, pas un titre."""
        body = f"```bash\n{line}\n```"
        directory = write_skill(tmp_path, "faux-skill", body)
        problems = checker.lint_skill(directory)
        assert problems, f"non détecté dans un bloc de code : {line}"

    def test_a_comment_line_starting_with_a_hash_inside_a_block_is_read(
        self, tmp_path: Path
    ) -> None:
        """Même en début de ligne, dans un bloc, `#` est un commentaire."""
        body = "```bash\n# attendu : 61 passed\npytest -q\n```"
        directory = write_skill(tmp_path, "faux-skill", body)
        assert checker.lint_skill(directory)

    def test_a_markdown_heading_is_not_a_shell_comment(self, tmp_path: Path) -> None:
        """Un titre reste un titre : il ne doit pas être pris pour du shell."""
        body = "## Étape 4 — lancer la suite\n\nLancer `pytest -q` et lire la sortie."
        directory = write_skill(tmp_path, "faux-skill", body)
        assert checker.lint_skill(directory) == []

    def test_giving_the_command_instead_of_the_figure_passes(self, tmp_path: Path) -> None:
        """La forme attendue : la commande, jamais sa valeur du jour."""
        body = (
            "Compter les tests du domaine :\n\n"
            "```bash\npython -m pytest packages/domain/tests -q\n```\n\n"
            "La sortie fait foi ; ne pas la recopier ici."
        )
        directory = write_skill(tmp_path, "faux-skill", body)
        assert checker.lint_skill(directory) == []

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("Phase 1 de la feuille de route.", id="numéro-de-phase"),
            pytest.param("Voir la section 4 ci-dessus.", id="renvoi"),
            pytest.param("Le budget est de 1024 caractères.", id="budget"),
            pytest.param("Arrondi à 2 décimales.", id="décimales"),
        ],
    )
    def test_a_number_that_is_not_a_counter_is_left_alone(self, tmp_path: Path, line: str) -> None:
        """Le vérificateur ne doit pas devenir un refus de tout chiffre."""
        directory = write_skill(tmp_path, "faux-skill", line)
        assert checker.lint_skill(directory) == [], line


class TestTheRepositorySkills:
    def test_every_skill_of_this_repository_passes(self) -> None:
        problems: list[str] = []
        for directory in sorted(p for p in checker.SKILLS_ROOT.iterdir() if p.is_dir()):
            problems += checker.lint_skill(directory)
        assert problems == []

    def test_the_definition_of_done_skill_carries_no_tool_output(self) -> None:
        """Le skill qui définit la porte de sortie ne fige aucun compteur."""
        text = (checker.SKILLS_ROOT / "definition-of-done" / "SKILL.md").read_text(encoding="utf-8")
        for forbidden in ("61 passed", "86 passed", "45 files", "33 source files"):
            assert forbidden not in text, forbidden

    def test_the_definition_of_done_skill_lists_every_ci_job(self) -> None:
        """Sa porte de sortie doit couvrir les dix jobs réels, pas cinq."""
        import yaml

        workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
        jobs = set(workflow["jobs"])
        text = (checker.SKILLS_ROOT / "definition-of-done" / "SKILL.md").read_text(encoding="utf-8")
        missing = sorted(job for job in jobs if f"`{job}`" not in text)
        assert missing == [], f"jobs de CI absents du skill : {missing}"

    def test_the_definition_of_done_skill_covers_the_scripts_directory(self) -> None:
        text = (checker.SKILLS_ROOT / "definition-of-done" / "SKILL.md").read_text(encoding="utf-8")
        # Les lignes de commande seulement : la description du frontmatter cite
        # ces outils en prose, sans être une commande à lancer.
        commands = [line for line in text.splitlines() if line.startswith(("ruff ", "mypy "))]
        assert commands, "aucune commande de lint ou de typage dans le skill"
        assert all("scripts" in line for line in commands), commands
