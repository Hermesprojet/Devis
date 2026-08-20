#!/usr/bin/env python3
"""Lint the repository skills in ``.claude/skills/``.

A skill is read by an agent before it touches the code, so a stale line in one
is worse than a stale line in a document nobody opens: it is followed. This
script checks the things that can be checked mechanically.

  * the frontmatter parses, carries only ``name`` and ``description``, and the
    name matches the directory;
  * the description stays within the budget the runtime allows;
  * every repository path quoted in the body still exists;
  * no volatile figure is written down — a test count, a route count or an
    Alembic revision is true on the day it is typed and wrong a week later, so
    the skill must give the command that produces it instead;
  * every skill ends with its ``## Signaux d'alerte`` section.

Run: ``python scripts/check_skills.py``. Exit status is non-zero on the first
category that fails, with every offending line listed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"

#: Combined budget for ``description`` (plus ``when_to_use`` when a skill
#: grows one). Beyond it the runtime may truncate, and what gets cut is the
#: tail — usually the clause separating a skill from its neighbours.
DESCRIPTION_BUDGET = 1024

#: Upper bound on a skill body. Long references belong in ``references/``.
MAX_LINES = 500

#: Figures that are true the day they are written and wrong soon after.
_VOLATILE: tuple[tuple[str, str], ...] = (
    (r"\b\d+\s+(?:tests?|routes?|tables?)\b", "compteur figé (tests / routes / tables)"),
    (r"\b[0-9a-f]{12}\b", "identifiant de révision (Alembic ou commit)"),
)

#: Paths are quoted in backticks; only those that look like repository paths
#: are checked, so prose such as `Decimal` or `BOQ_READ/WRITE` is left alone.
_PATH_IN_BACKTICKS = re.compile(r"`([A-Za-z0-9_./-]+)`")
_LOOKS_LIKE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*-]+)+$")

#: A candidate is only treated as a file path when it carries one of these
#: suffixes. Without that filter, enum listings (``proposed/verified/...``),
#: permission shorthands (``BOQ_READ/WRITE``), schema identifiers
#: (``metreo.estimate.snapshot/1``) and branch names all look like paths.
_CODE_SUFFIXES = (
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".json",
    ".ts",
    ".tsx",
    ".csv",
    ".ini",
    ".toml",
    ".cfg",
    ".sh",
    ".sql",
    ".txt",
)


def _frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            # A continuation line of a folded value.
            continue
        fields[key.strip()] = value.strip()
    return fields, text[match.end() :]


def _resolves(candidate: str) -> bool:
    """Does ``candidate`` name something in the repository?

    Skills quote abbreviated paths on purpose — each one declares near the top
    that ``services/estimating.py`` means
    ``apps/api/src/metreo_api/services/estimating.py`` — so an exact miss from
    the root is followed by a suffix search before anything is reported.
    """
    cleaned = candidate.rstrip("/")
    if (REPO_ROOT / cleaned).exists():
        return True
    suffix = "/" + cleaned
    for path in REPO_ROOT.rglob("*" + Path(cleaned).name):
        if ".git/" in str(path) or "node_modules" in str(path):
            continue
        if str(path).endswith(suffix):
            return True
    return False


def _check_paths(body: str, skill: str, problems: list[str]) -> None:
    for candidate in sorted(set(_PATH_IN_BACKTICKS.findall(body))):
        if not _LOOKS_LIKE_PATH.match(candidate):
            continue
        if candidate.startswith(("http", "$", "~")):
            continue
        cleaned = candidate.rstrip("/")
        is_directory_like = cleaned.endswith("/") or candidate.endswith("/")
        if not (cleaned.endswith(_CODE_SUFFIXES) or "*" in cleaned or is_directory_like):
            continue
        if "*" in cleaned:
            parent = Path(cleaned).parent
            if not _resolves(str(parent)):
                problems.append(f"{skill}: motif vers un dossier inexistant — {candidate}")
            continue
        if not _resolves(cleaned):
            problems.append(f"{skill}: chemin inexistant — {candidate}")


def main() -> int:
    if not SKILLS_ROOT.is_dir():
        print(f"Aucun dossier de skills à {SKILLS_ROOT}", file=sys.stderr)
        return 1

    problems: list[str] = []
    directories = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    if not directories:
        print("Aucun skill trouvé.", file=sys.stderr)
        return 1

    for directory in directories:
        skill_file = directory / "SKILL.md"
        name = directory.name
        if not skill_file.is_file():
            problems.append(f"{name}: SKILL.md manquant")
            continue

        text = skill_file.read_text(encoding="utf-8")
        parsed = _frontmatter(text)
        if parsed is None:
            problems.append(f"{name}: frontmatter YAML absent ou malformé")
            continue
        fields, body = parsed

        unknown = set(fields) - {"name", "description", "when_to_use"}
        if unknown:
            problems.append(f"{name}: clés de frontmatter inattendues — {sorted(unknown)}")
        if fields.get("name") != name:
            problems.append(f"{name}: name={fields.get('name')!r} ne correspond pas au dossier")

        budget_used = len(fields.get("description", "")) + len(fields.get("when_to_use", ""))
        if budget_used == 0:
            problems.append(f"{name}: description vide")
        elif budget_used > DESCRIPTION_BUDGET:
            problems.append(
                f"{name}: description + when_to_use = {budget_used} caractères, "
                f"au-delà du budget de {DESCRIPTION_BUDGET} — la fin sera tronquée"
            )

        line_count = len(text.splitlines())
        if line_count > MAX_LINES:
            problems.append(
                f"{name}: {line_count} lignes, au-delà de {MAX_LINES} — "
                "déplacer le détail dans references/"
            )

        if "## Signaux d'alerte" not in body:
            problems.append(f"{name}: section « ## Signaux d'alerte » absente")

        for number, line in enumerate(body.splitlines(), start=1):
            if line.lstrip().startswith(("#", ">")):
                continue
            for pattern, label in _VOLATILE:
                if re.search(pattern, line):
                    problems.append(
                        f"{name}:{number}: {label} — donner la commande qui le produit. "
                        f"Ligne : {line.strip()[:90]}"
                    )

        _check_paths(body, name, problems)

    if problems:
        print(f"{len(problems)} problème(s) dans les skills :\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{len(directories)} skills conformes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
