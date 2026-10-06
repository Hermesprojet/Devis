#!/usr/bin/env python3
"""Chaque correction d'une branche d'intégration appartient-elle à une PR ?

    python3 scripts/verifier_provenance_du_candidat.py            # la branche d'intégration
    python3 scripts/verifier_provenance_du_candidat.py 191749b    # un candidat précis

**Le défaut que ce contrôle ferme.** Une branche d'intégration sert à éprouver
la COMBINAISON de plusieurs PR : on y fusionne les branches, on lance tous les
contrôles, et un vert dit que les PR fonctionnent ensemble. Rien n'empêche
alors d'y corriger directement un conflit ou un défaut découvert au passage —
c'est même tentant, puisque la boucle est courte.

Un tel commit n'appartient à aucune PR. Il part donc à la poubelle avec la
branche d'intégration, qui n'est jamais fusionnée. Le vert qu'il a produit
décrit un arbre qui n'existera plus. Et le défaut qu'il corrigeait revient le
jour de la mise en ligne, sans que personne se souvienne qu'il avait été vu.

Ce contrôle les nomme. Il ne les corrige pas : la correction est un choix —
reporter le commit dans la bonne branche, ou constater qu'il n'était pas
nécessaire — et ce choix appartient à celui qui a écrit le commit.

**La comparaison se fait par `patch-id`, pas par SHA.** Un commit reporté dans
une autre branche change de SHA, et une comparaison de SHA le déclarerait
orphelin à tort. Le `patch-id` est une empreinte du diff : il survit au report
et au rebasement. Il ne survit pas à une résolution de conflit qui modifie le
diff, et c'est une limite réelle de ce contrôle — elle est signalée plutôt que
tue, et le remède est de lire le commit signalé plutôt que de croire l'outil.

Les commits de fusion sont écartés : leur diff dépend de l'ordre des fusions,
pas d'un travail à reporter.
"""

from __future__ import annotations

import subprocess
import sys

#: Les branches dont les PR sont destinées à être fusionnées.
#:
#: Écrite à la main, et c'est voulu : la liste des PR ouvertes changerait à
#: chaque passage et rendrait le contrôle non reproductible. Une branche
#: oubliée ici fait apparaître ses commits comme orphelins — un faux positif
#: bruyant, qui se corrige en une ligne. L'inverse, un orphelin manqué, serait
#: silencieux.
BRANCHES_DE_PR: tuple[str, ...] = (
    "claude/pyjwt-treize-failles",
    "claude/lecture-de-plans",
    "claude/parcours-plan-essayable",
    "claude/image-qui-lit-les-plans",
    "claude/lecteur-pdf",
    "claude/codes-de-connexion",
    "codex/login-account-choice",
    "claude/preprod-fiabilisation",
    "claude/documentation-de-deploiement",
    "claude/audit-javascript",
)

BASE = "origin/main"
CANDIDAT_PAR_DEFAUT = "origin/claude/integration-cinq-pr"


def _git(*arguments: str) -> str:
    resultat = subprocess.run(
        ["git", *arguments], capture_output=True, text=True, check=False
    )
    if resultat.returncode != 0:
        print(
            f"ÉCHEC : git {' '.join(arguments)}\n{resultat.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return resultat.stdout


def _empreintes(plage: str) -> dict[str, tuple[str, str]]:
    """`{patch_id: (sha court, sujet)}` pour chaque commit non-fusion."""
    table: dict[str, tuple[str, str]] = {}
    for sha in _git("log", "--format=%H", "--no-merges", plage).split():
        diff = _git("diff-tree", "-p", "--no-commit-id", sha)
        empreinte = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=diff,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if empreinte:
            sujet = _git("log", "-1", "--format=%s", sha).strip()
            table[empreinte[0]] = (sha[:7], sujet)
    return table


def main() -> int:
    candidat = sys.argv[1] if len(sys.argv) > 1 else CANDIDAT_PAR_DEFAUT

    print(f"Candidat : {_git('rev-parse', candidat).strip()}")
    print(f"Base     : {BASE} ({_git('rev-parse', BASE).strip()[:7]})")
    print()

    du_candidat = _empreintes(f"{BASE}..{candidat}")
    if not du_candidat:
        print("Aucun commit à vérifier : le candidat n'ajoute rien à la base.")
        return 0

    par_branche: dict[str, set[str]] = {}
    for branche in BRANCHES_DE_PR:
        try:
            par_branche[branche] = set(_empreintes(f"{BASE}..origin/{branche}"))
        except SystemExit:
            # Une branche supprimée après fusion : ce n'est pas une erreur, et
            # ses commits seront alors trouvés dans la base plutôt qu'ici.
            print(f"  (branche absente, ignorée : {branche})")

    orphelins: list[tuple[str, str]] = []
    for empreinte, (sha, sujet) in du_candidat.items():
        porteuses = [b for b, ids in par_branche.items() if empreinte in ids]
        if porteuses:
            print(f"  ok        {sha}  {sujet[:58]:<58} <- {', '.join(porteuses)}")
        else:
            orphelins.append((sha, sujet))
            print(f"  ORPHELIN  {sha}  {sujet[:58]}")

    print()
    print(f"{len(du_candidat)} commit(s) hors fusion, {len(orphelins)} orphelin(s).")
    if orphelins:
        print()
        print("Ces corrections n'appartiennent à AUCUNE branche de PR. Elles")
        print("disparaîtront avec la branche d'intégration, et le défaut qu'elles")
        print("corrigeaient reviendra :")
        for sha, sujet in orphelins:
            print(f"  - {sha} {sujet}")
        print()
        print("Reporter chacune dans la branche à laquelle elle appartient, ou")
        print("constater par écrit qu'elle n'était pas nécessaire.")
        return 1

    print("Chaque correction du candidat appartient à une PR destinée à être fusionnée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
