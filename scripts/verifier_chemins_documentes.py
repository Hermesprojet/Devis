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
import subprocess
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


#: Une branche nommée par un document, dans la forme que ce dépôt emploie.
#:
#: Sert à la seconde règle de résolution ci-dessous. Volontairement restreinte
#: aux préfixes réellement utilisés : `main`, et les branches de travail.
BRANCHE = re.compile(r"\b(?:origin/)?((?:claude|codex)/[A-Za-z0-9_.-]+|main)\b")


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


def fabriquee_a_la_demande(chemin: str) -> bool:
    """Vrai si une fabrique du dépôt déclare produire ce fichier.

    **Le défaut que cette règle ferme.** `fixtures/plans/plan_cote.pdf` est un
    PDF fabriqué par `scripts/fabriquer_plans_de_test.py` et volontairement non
    commité — un binaire commité devient un bloc que personne n'ouvre. Il existe
    donc sur la machine de qui a lancé la fabrique, et nulle part ailleurs. Un
    document qui le cite passait ce contrôle chez l'auteur et le faisait tomber
    sur un dépôt propre : exactement le genre d'écart que ce script existe pour
    attraper, retourné contre lui.

    La règle plutôt qu'une exception : la fabrique DÉCLARE ce qu'elle produit
    dans son tuple `ATTENDUS`, et c'est cette déclaration qui est lue. Un
    fichier qu'aucune fabrique ne promet reste refusé.
    """
    nom = Path(chemin).name
    for fabrique in sorted((RACINE / "scripts").glob("fabriquer_*.py")):
        texte = fabrique.read_text(encoding="utf-8")
        declaration = re.search(r"ATTENDUS[^=]*=\s*\(([^)]*)\)", texte, re.S)
        if declaration and f'"{nom}"' in declaration.group(1):
            return True
    return False


def porte_par_une_branche_citee(chemin: str, branches: frozenset[str]) -> str | None:
    """La branche, parmi celles que le DOCUMENT nomme, qui porte ce chemin.

    **Pourquoi cette seconde règle existe.** Un dépôt qui travaille par demandes
    de fusion empilées a des documents qui parlent, à juste titre, de fichiers
    vivant sur une autre branche : `docs/MISE_EN_LIGNE_LECTURE_DE_PLANS.md` décrit
    la mise en ligne et doit citer `ops/verifier_deploiement.sh`, qui arrive par
    une autre demande. Refuser cette citation forcerait le document à taire
    l'outil même dont il parle.

    **Pourquoi elle reste stricte.** Elle n'accepte pas « ce fichier existe
    quelque part » : elle exige que le document NOMME la branche qui le porte,
    et vérifie que cette branche l'a vraiment. Un lecteur qui ne trouve pas le
    fichier lit donc, dans le même document, où le chercher — ce qui est
    exactement la propriété que ce contrôle défend. Et une citation dont la
    branche n'est pas nommée reste refusée.

    Rend le nom de la branche, pour que la sortie puisse le dire.
    """
    if not branches:
        return None
    for branche in sorted(branches):
        for prefixe in ("", "apps/api/src/metreo_api/", "docs/"):
            acces = subprocess.run(
                ["git", "cat-file", "-e", f"{branche}:{prefixe}{chemin}"],
                cwd=RACINE,
                capture_output=True,
            )
            if acces.returncode == 0:
                return branche
    return None


def introuvables() -> dict[Path, set[str]]:
    manquants: dict[Path, set[str]] = {}
    for document in DOCUMENTS:
        if not document.exists():
            continue
        texte = document.read_text(encoding="utf-8")
        # Les branches que CE document nomme, et elles seules : la seconde règle
        # de résolution ne regarde pas plus loin que ce que le document dit.
        branches = frozenset(BRANCHE.findall(texte))
        for trouve in CITATION.finditer(texte):
            chemin = trouve.group(1)
            if chemin in TOLERES:
                continue
            if resoudre(chemin) is not None:
                continue
            if fabriquee_a_la_demande(chemin):
                continue
            if porte_par_une_branche_citee(chemin, branches) is not None:
                continue
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
