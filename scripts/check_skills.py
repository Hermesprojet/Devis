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
#:
#: The first version of this list only knew ``N tests``, ``N routes`` and
#: ``N tables`` — none of the shapes a tool actually prints. A skill could
#: therefore carry ``61 passed`` and ``no issues found in 33 source files``
#: while the checker announced every skill conformant. The point of the check
#: is the *recopied tool output*, so that is what the patterns describe.
VOLATILE: tuple[tuple[str, str], ...] = (
    (r"\b\d+\s+(?:tests?|routes?|tables?)\b", "compteur figé (tests / routes / tables)"),
    (
        r"\b\d+\s+(?:passed|failed|skipped|xfailed|xpassed|errors?|deselected)\b",
        "sortie de pytest recopiée",
    ),
    (
        r"\b\d+\s+(?:source\s+)?(?:files?|fichiers?)\b",
        "compteur de fichiers recopié (ruff, mypy)",
    ),
    (r"\b\d+\s+(?:jobs?|skills?|r[ée]visions?|revisions?)\b", "compteur figé"),
    # L'autre sens de lecture. Les motifs ci-dessus lisent « 61 tests » ; un
    # tableau, une légende ou une note écrit tout aussi bien « Tests : 61 » ou
    # « | Tests | 61 | », et la valeur y vieillit exactement pareil.
    (
        r"\b(?:tests?|routes?|tables?|jobs?|skills?|fichiers?|files?)\s*[:=|]+\s*\d+",
        "compteur figé (nom puis nombre)",
    ),
    (r"\b(?:ok|succ[èe]s|success)\s*\(\s*\d+\s*\)", "sortie d'outil recopiée"),
    (
        r"\b\d+\s+(?:r[ée]ussis|r[ée]ussites|[ée]chou[ée]s|[ée]checs|ignor[ée]s)\b",
        "sortie de test recopiée",
    ),
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


#: Un délimiteur de bloc : son caractère et sa longueur comptent tous deux,
#: donc ils sont capturés. CommonMark ferme un bloc par un délimiteur du même
#: caractère et d'une longueur au moins égale ; tout le reste est du contenu,
#: y compris une fence plus courte — c'est ainsi qu'on montre un bloc de code
#: à l'intérieur d'un autre.
_FENCE = re.compile(r"^\s*(?P<delimiter>`{3,}|~{3,})(?P<info>.*)$")


def _fenced_lines(lines: list[str]) -> tuple[set[int], int | None]:
    """Which line numbers sit inside a fenced code block, and any block left open.

    CommonMark closes a fence with a delimiter of the **same character** and of
    **at least the same length**, carrying no info string. Everything else is
    content — including a shorter fence, which is precisely how one shows a
    code block inside another. Ignoring character and length made a valid
    nested example read as two gaping blocks, and the refusal that followed
    disarmed the heading exemption, so the checker went on to accuse an
    ordinary heading of being copied tool output. Wrong in both directions, on
    a correct document.
    """
    inside: set[int] = set()
    opened_at: int | None = None
    delimiter = ""
    for number, line in enumerate(lines, start=1):
        match = _FENCE.match(line)
        if match and not delimiter:
            delimiter, opened_at = match.group("delimiter"), number
            continue
        if match and delimiter:
            candidate, info = match.group("delimiter"), match.group("info").strip()
            if candidate[0] == delimiter[0] and len(candidate) >= len(delimiter) and not info:
                delimiter, opened_at = "", None
                continue
        if delimiter:
            inside.add(number)
    return inside, opened_at


def volatile_problems(body: str, skill: str) -> list[str]:
    """List the frozen figures written into ``body``.

    A line starting with ``#`` is skipped **only outside a fenced block**,
    where it is a Markdown heading. Inside a fence it is a shell comment, and
    that is precisely where the stale counters lived: skipping it made the
    whole check ornamental.

    A block left open is reported in its own right: the scan can no longer tell
    a heading from a shell comment past that point, so it stops trusting the
    distinction and reads every line rather than silently reopening the blind
    spot.

    Markdown also allows a code block indented by four spaces, with no fence at
    all. Those lines are read too: a comment hidden there is just as followed
    as one inside a fence.
    """
    problems: list[str] = []
    lines = body.splitlines()
    inside, opened_at = _fenced_lines(lines)

    if opened_at is not None:
        problems.append(
            f"{skill}:{opened_at}: bloc de code non refermé — le suivi des blocs "
            "ne peut plus distinguer un titre Markdown d'un commentaire de shell, "
            "et les compteurs figés y redeviendraient invisibles. Refermer le bloc."
        )

    for number, line in enumerate(lines, start=1):
        if _FENCE.match(line) and number not in inside:
            continue
        indented_code = line.startswith(("    ", "\t")) and line.strip().startswith("#")
        heading = line.lstrip().startswith(("#", ">"))
        if number not in inside and heading and not indented_code:
            continue
        for pattern, label in VOLATILE:
            if re.search(pattern, line, re.IGNORECASE):
                problems.append(
                    f"{skill}:{number}: {label} — donner la commande qui le produit. "
                    f"Ligne : {line.strip()[:90]}"
                )
    return problems


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


def lint_skill(directory: Path) -> list[str]:
    """Return every problem found in one skill directory.

    Public and directory-scoped on purpose: the checker is itself checked, on
    skills built for the occasion, by ``apps/api/tests/test_skills_checker.py``.
    A verifier nobody can make fail proves nothing.
    """
    problems: list[str] = []
    skill_file = directory / "SKILL.md"
    name = directory.name
    if not skill_file.is_file():
        return [f"{name}: SKILL.md manquant"]

    text = skill_file.read_text(encoding="utf-8")
    parsed = _frontmatter(text)
    if parsed is None:
        return [f"{name}: frontmatter YAML absent ou malformé"]
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

    problems += volatile_problems(body, name)
    _check_paths(body, name, problems)
    return problems


def main() -> int:
    if not SKILLS_ROOT.is_dir():
        print(f"Aucun dossier de skills à {SKILLS_ROOT}", file=sys.stderr)
        return 1

    directories = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    if not directories:
        print("Aucun skill trouvé.", file=sys.stderr)
        return 1

    problems: list[str] = []
    for directory in directories:
        problems += lint_skill(directory)

    if problems:
        print(f"{len(problems)} problème(s) dans les skills :\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{len(directories)} skills conformes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
