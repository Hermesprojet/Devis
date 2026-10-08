#!/usr/bin/env python3
"""D'où vient chaque commit du candidat, et lesquels ne tiennent qu'à sa branche.

    python3 scripts/verifier_provenance_du_candidat.py            # le candidat livré
    python3 scripts/verifier_provenance_du_candidat.py 191749b    # un candidat précis

Sortie 0 si rien ne se perd, 1 si un commit du candidat examiné n'est porté ni
par une branche de PR ni par la branche réellement livrée, 2 si `git` a échoué.

**Le défaut que ce contrôle ferme.** Une branche d'intégration sert à éprouver
la COMBINAISON de plusieurs PR : on y fusionne les branches, on lance tous les
contrôles, et un vert dit que les PR fonctionnent ensemble. Rien n'empêche
alors d'y corriger directement un conflit ou un défaut découvert au passage —
c'est même tentant, puisque la boucle est courte.

Un tel commit n'appartient à aucune PR. **Si la branche d'intégration n'est
jamais fusionnée**, il part à la poubelle avec elle : le vert qu'il a produit
décrit un arbre qui n'existera plus, et le défaut qu'il corrigeait revient le
jour de la mise en ligne, sans que personne se souvienne qu'il avait été vu.

**Ce contrôle ne décide plus la mise en ligne, et son texte l'affirmait
encore.** Le paragraphe ci-dessus tient à une condition, et cette condition
appartient à la voie de livraison, pas au script. Elle était vraie de la
séquence de onze fusions de demandes empilées, qui amenait les branches de PR
une à une sur `main` et laissait le candidat derrière. Cette séquence est
retirée — elle était inexécutable. `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`,
section « La voie unique, de bout en bout », n'en décrit plus qu'une : UNE
demande de fusion de `claude/candidat-integre` vers `main`, par un merge
commit. La branche d'intégration **est** ce qui est fusionné, et ce qu'elle
porte arrive sur `main` avec elle.

Ce script garde donc son inventaire et renonce à son ancien verdict. Il dit de
quelle branche vient chaque commit du candidat ; il ne parle de perte que si le
candidat examiné n'est pas porté par `BRANCHE_LIVREE`. Ce qui ferme l'écart
entre « éprouvé » et « en ligne » n'est pas ici : c'est la comparaison de
contenu de la fiche, `git diff --stat origin/main origin/claude/candidat-integre`,
dont la sortie doit être vide.

Le choix retenu est donc de corriger ce script ET de borner ce qu'il prouve.
Le corriger seul aurait laissé un contrôle qui annonce la perte de commits
qu'une fusion emporte ; le laisser en l'état aurait gardé un contrôle faux.

**Pourquoi ses deux constantes ont été corrigées.** Mesuré sur
2d18299ee08b2a66bb54573d665f233a72aaf6ee : avec l'ancien candidat par défaut
(`origin/claude/integration-cinq-pr`, le brouillon #84, qui n'est pas ancêtre
du candidat) et l'ancien tuple, ce script annonçait **31 orphelins**. Quatre
branches de PR manquaient au tuple, et elles portaient **22 de ces 31** à
elles seules. Les 9 autres ne vivent effectivement que sur les branches de
candidat ; la demande de fusion unique les emporte, et leur relecture est
celle du candidat lui-même (`docs/REVUE_FINALE_DU_CANDIDAT.md`). Un contrôle
qui crie à tort cesse d'être lu : c'est le motif de la correction.

**La comparaison se fait par `patch-id`, pas par SHA.** Un commit reporté dans
une autre branche change de SHA, et une comparaison de SHA le déclarerait
orphelin à tort. Le `patch-id` est une empreinte du diff : il survit au report
et au rebasement. Il ne survit pas à une résolution de conflit qui modifie le
diff, et c'est une limite réelle de ce contrôle — elle est signalée plutôt que
tue, et le remède est de lire le commit signalé plutôt que de croire l'outil.

**Deux commits peuvent porter le même `patch-id`, et l'inventaire en perdait
un.** Mesuré sur le même SHA : `git log --no-merges` compte 70 commits et ce
script en annonçait 69. `caf3889` et `8a2e41f` portent le même diff — le même
passage du formateur, reporté dans deux branches — et une table indexée par
`patch-id` n'en gardait qu'un. Le verdict n'en dépendait pas ici, les deux
étant portés par une branche de PR, mais un inventaire qui perd une ligne en
silence ne vaut pas comme inventaire. Le compte est désormais tenu par commit.

Les commits de fusion sont écartés : leur diff dépend de l'ordre des fusions,
pas d'un travail à reporter.
"""

from __future__ import annotations

import subprocess
import sys

#: Les branches dont les PR portent le travail du candidat.
#:
#: Écrite à la main, et c'est voulu : la liste des PR ouvertes changerait à
#: chaque passage et rendrait le contrôle non reproductible. Une branche
#: oubliée ici fait apparaître ses commits comme ne tenant qu'au candidat — un
#: faux positif bruyant, qui se corrige en une ligne. L'inverse, un commit
#: manqué, serait silencieux.
#:
#: Les quatre dernières manquaient, et leur absence faisait 22 des 31
#: « orphelins » annoncés sur 2d18299ee08b2a66bb54573d665f233a72aaf6ee.
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
    "claude/cotes-dans-les-blocs",
    "claude/mesures-pdf",
    "claude/cotes-de-reference",
    "claude/corrections-du-candidat",
)

BASE = "origin/main"

#: La branche que la demande de fusion unique amène sur `main`. C'est elle qui
#: décide si un commit propre au candidat se perd ou arrive : il se perd si la
#: branche n'est pas fusionnée, il arrive si elle l'est. Tant que cette
#: constante nomme la branche de `docs/FICHE_DE_DECISION_MISE_EN_LIGNE.md`,
#: section « La voie unique, de bout en bout », le script dit vrai.
BRANCHE_LIVREE = "origin/claude/candidat-integre"

CANDIDAT_PAR_DEFAUT = BRANCHE_LIVREE


def _git(*arguments: str) -> str:
    resultat = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    if resultat.returncode != 0:
        print(
            f"ÉCHEC : git {' '.join(arguments)}\n{resultat.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return resultat.stdout


def _est_ancetre(ancetre: str, descendant: str) -> bool | None:
    """`True`, `False`, ou `None` si la question n'a pas pu être posée.

    `git merge-base --is-ancestor` répond par son code de sortie : 0 oui,
    1 non, autre chose une erreur. Confondre le 1 avec l'erreur ferait dire
    « ce commit se perd » à une référence seulement introuvable, et ce verdict
    serait tiré d'une panne et non d'un fait.
    """
    resultat = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancetre, descendant],
        capture_output=True,
        text=True,
        check=False,
    )
    if resultat.returncode == 0:
        return True
    if resultat.returncode == 1:
        return False
    return None


def _empreintes(plage: str) -> dict[str, list[tuple[str, str]]]:
    """`{patch_id: [(sha court, sujet), …]}` pour chaque commit non-fusion.

    La valeur est une liste, et non un couple : deux commits peuvent porter le
    même diff, donc le même `patch-id`, et une table à une valeur en perdrait
    un sans le dire.
    """
    table: dict[str, list[tuple[str, str]]] = {}
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
            table.setdefault(empreinte[0], []).append((sha[:7], sujet))
    return table


def main() -> int:
    candidat = sys.argv[1] if len(sys.argv) > 1 else CANDIDAT_PAR_DEFAUT

    print(f"Candidat : {_git('rev-parse', candidat).strip()}")
    print(f"Base     : {BASE} ({_git('rev-parse', BASE).strip()[:7]})")
    print(f"Livrée   : {BRANCHE_LIVREE}")
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

    nombre_de_commits = 0
    propres: list[tuple[str, str]] = []
    for empreinte, commits in du_candidat.items():
        porteuses = [b for b, ids in par_branche.items() if empreinte in ids]
        for sha, sujet in commits:
            nombre_de_commits += 1
            if porteuses:
                print(f"  ok        {sha}  {sujet[:58]:<58} <- {', '.join(porteuses)}")
            else:
                propres.append((sha, sujet))
                print(f"  candidat  {sha}  {sujet[:58]}")

    print()
    print(f"{nombre_de_commits} commit(s) hors fusion, {len(propres)} propre(s) au candidat.")

    if not propres:
        print()
        print("Chaque commit du candidat est porté par une branche de PR.")
        return 0

    porte = _est_ancetre(candidat, BRANCHE_LIVREE)

    if porte is None:
        print()
        print(f"Impossible de situer {candidat} par rapport à {BRANCHE_LIVREE} :")
        print("sans cette réponse, on ne peut pas dire si ces commits sont")
        print("emportés par la fusion ou perdus avec la branche. Récupérer les")
        print("références manquantes (git fetch origin), puis relancer.")
        return 1

    if porte:
        meme_tete = _git("rev-parse", candidat).strip() == _git("rev-parse", BRANCHE_LIVREE).strip()
        appartenance = (
            f"le candidat examiné EST {BRANCHE_LIVREE}"
            if meme_tete
            else f"{candidat} est porté par {BRANCHE_LIVREE}"
        )
        print()
        print("Ces commits n'appartiennent à aucune branche de PR : ils ont été")
        print("écrits sur les branches de candidat elles-mêmes. Ils ne se perdent")
        print(f"pas pour autant — {appartenance},")
        print("et la demande de fusion unique amène cette branche sur main avec")
        print("son historique. Leur relecture est celle du candidat :")
        print("docs/REVUE_FINALE_DU_CANDIDAT.md.")
        return 0

    print()
    print(f"{candidat} n'est PAS porté par {BRANCHE_LIVREE}.")
    print("Ces commits n'appartiennent à aucune branche de PR et à aucune branche")
    print("fusionnée : ils disparaîtront avec celle qui les porte, et le défaut")
    print("qu'ils corrigeaient reviendra :")
    for sha, sujet in propres:
        print(f"  - {sha} {sujet}")
    print()
    print("Reporter chacun dans la branche à laquelle il appartient, ou constater")
    print("par écrit qu'il n'était pas nécessaire.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
