"""Refuse un chemin cité par la documentation et qui n'existe pas.

**Le problème mesuré.** `docs/DATA_MODEL.md` décrivait un modèle sans les sept
tables ajoutées par trois PR successives. `docs/ARCHITECTURE.md` ne citait ni
les clients, ni le devis remis, ni la page publique. `scripts/README.md`
annonçait un répertoire « vide volontairement » alors qu'il portait sept
scripts — dont la purge RGPD qu'il promettait pour plus tard.

Aucun de ces écarts n'était visible. Rien ne les regardait.

Ce contrôle n'attrape pas tout — il ne sait pas dire qu'une table manque à un
tableau. Il attrape la moitié vérifiable : **un chemin cité qui n'existe
plus.** C'est la forme la plus coûteuse de documentation périmée, parce qu'elle
envoie chercher quelque chose qui n'est nulle part, et qu'une personne qui ne
trouve pas cesse de faire confiance au reste du document.

**Chemins abrégés.** La documentation écrit `services/tenant.py` et non
`apps/api/src/metreo_api/services/tenant.py` ; `adr/0002-multi-tenancy.md` et
non `docs/adr/…`. La convention est déclarée dans `docs/CONVENTIONS.md` ; ce
script la résout, il ne l'invente pas.

Usage : python scripts/verifier_chemins_documentes.py
Sortie 0 si tout chemin cité se résout, 1 sinon, avec la liste.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

#: Les documents inspectés. Les skills ont déjà leur propre contrôle
#: (`check_skills.py`) : les inspecter deux fois donnerait deux verdicts à
#: tenir d'accord.
DOCUMENTS: tuple[Path, ...] = (
    RACINE / "README.md",
    RACINE / "scripts" / "README.md",
    *sorted((RACINE / "docs").rglob("*.md")),
)

#: Les racines sous lesquelles un chemin abrégé se résout, dans l'ordre.
#: La racine du dépôt vient en dernier : un chemin complet doit gagner.
RACINES_ABREGEES: tuple[Path, ...] = (
    RACINE / "apps" / "api" / "src" / "metreo_api",
    RACINE / "docs",
    RACINE,
)

#: Un chemin cité : entre accents graves, avec une extension connue et au moins
#: un séparateur. Sans le séparateur on ramasserait « pytest.ini » ou
#: « package.json » cités comme des noms génériques, et le contrôle deviendrait
#: bruyant sans être plus utile.
CITATION = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]+\.[a-z]{2,4})`")

#: Ce qui ressemble à un chemin sans en être un. Bornée et justifiée : une
#: liste d'exceptions qui s'allonge sans motif est une liste qui ne veut plus
#: rien dire.
TOLERES: frozenset[str] = frozenset(
    {
        # Cité comme exemple d'URL de dépôt, pas comme fichier du dépôt.
        "claude.ai/code",
    }
)


def resoudre(chemin: str) -> Path | None:
    """Le fichier ou dossier désigné, ou `None` si aucun ne correspond.

    Un fichier que l'EXPLOITANT crée compte comme résolu quand son modèle est
    versionné à côté : `infra/staging.env` est cité par `docs/EXPLOITATION.md`,
    n'existe pas dans le dépôt, et ne doit jamais y exister — le document le dit
    lui-même. Son `.example` prouve que la citation vise un fichier prévu, pas
    un fichier disparu.

    Cette règle plutôt qu'une exception nommée : une liste d'exceptions grandit
    à chaque cas et finit par tout accepter, là où la convention `.example` est
    déjà celle du dépôt et se vérifie.
    """
    for racine in RACINES_ABREGEES:
        candidat = racine / chemin
        if candidat.exists():
            return candidat
        modele = candidat.with_name(candidat.name + ".example")
        if modele.exists():
            return modele
    return None


def introuvables() -> dict[Path, set[str]]:
    manquants: dict[Path, set[str]] = {}
    for document in DOCUMENTS:
        if not document.exists():
            continue
        texte = document.read_text(encoding="utf-8")
        for trouve in CITATION.finditer(texte):
            chemin = trouve.group(1)
            if chemin in TOLERES:
                continue
            if resoudre(chemin) is None:
                manquants.setdefault(document, set()).add(chemin)
    return manquants


def main() -> int:
    manquants = introuvables()
    if not manquants:
        cites = sum(
            len(CITATION.findall(d.read_text(encoding="utf-8"))) for d in DOCUMENTS if d.exists()
        )
        print(f"{cites} chemins cités par la documentation, tous résolus.")
        return 0

    total = sum(len(v) for v in manquants.values())
    print(
        f"{total} chemin(s) cité(s) par la documentation et introuvable(s) :",
        file=sys.stderr,
    )
    for document in sorted(manquants):
        print(f"  {document.relative_to(RACINE)}", file=sys.stderr)
        for chemin in sorted(manquants[document]):
            print(f"      {chemin}", file=sys.stderr)
    print(
        "\nUn document qui cite un fichier disparu envoie chercher ce qui n'est "
        "nulle part. Corrigez le chemin, ou retirez la citation.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
