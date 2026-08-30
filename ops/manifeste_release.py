"""Fabrique le manifeste d'un candidat de release.

Le manifeste répond à une seule question, posée des mois plus tard : « qu'y
avait-il exactement dans ce qui tournait ? » Un numéro de version ne suffit
pas — il désigne une intention, pas un contenu.

Tout ce qu'il porte est MESURÉ, jamais déclaré : les empreintes viennent des
images construites, la tête Alembic du graphe des migrations, les sommes des
fichiers eux-mêmes. Une valeur qu'on ne sait pas mesurer est absente, ou
explicitement `null` — jamais devinée.

Il ne contient AUCUNE valeur de variable, seulement des noms. Un manifeste
finit dans un artefact, une pièce jointe, un ticket.

    python ops/manifeste_release.py --sortie manifeste.json \
        --horodatage 2026-08-30T00:00:00Z --sha <sha-git-complet>

`--horodatage` est fourni par l'appelant plutôt que lu de l'horloge : le
manifeste doit être reproductible à contenu égal, et une date interne le
rendrait différent à chaque exécution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

# Les fichiers dont une modification change ce qui est déployé. Leur somme est
# ce qui permet de dire « le déploiement d'aujourd'hui est celui d'hier ».
FICHIERS_DE_DEPLOIEMENT = [
    "infra/api.Dockerfile",
    "infra/web.Dockerfile",
    "infra/Caddyfile",
    "infra/docker-compose.staging.yml",
    "infra/docker-compose.repetition.yml",
    "infra/docker-compose.demo.yml",
    "infra/staging.env.example",
    "infra/repetition.env.example",
    "ops/sauvegarder.sh",
    "ops/restaurer.sh",
    "ops/verifier_restauration.py",
    "ops/verifier_disponibilite.sh",
    "ops/repetition_staging.sh",
    "ops/demonstration.sh",
    "constraints/api.txt",
]


def _executer(commande: list[str]) -> str | None:
    try:
        return (
            subprocess.run(
                commande, capture_output=True, text=True, cwd=str(RACINE), timeout=60
            ).stdout.strip()
            or None
        )
    except Exception:
        return None


def tete_alembic() -> str | None:
    """La révision qui n'est la `down_revision` d'aucune autre.

    Calculée sur le graphe plutôt que demandée à Alembic : cela ne dépend
    d'aucune base, d'aucune installation, et se relit sans outil.
    """
    dossier = RACINE / "apps/api/alembic/versions"
    if not dossier.is_dir():
        return None
    revisions, parents = set(), set()
    for fichier in dossier.glob("*.py"):
        texte = fichier.read_text(encoding="utf-8")
        if m := re.search(r'^revision(?::\s*\w+)?\s*=\s*["\'](\w+)["\']', texte, re.M):
            revisions.add(m.group(1))
        if m := re.search(r'^down_revision[^=]*=\s*["\'](\w+)["\']', texte, re.M):
            parents.add(m.group(1))
    tetes = sorted(revisions - parents)
    if len(tetes) != 1:
        # Deux têtes est une erreur de fusion, pas une curiosité : la dire
        # plutôt que d'en choisir une au hasard.
        return f"ANOMALIE: {len(tetes)} têtes — {', '.join(tetes) or 'aucune'}"
    return tetes[0]


def _lire(chemin: str) -> str | None:
    fichier = RACINE / chemin
    return fichier.read_text(encoding="utf-8") if fichier.is_file() else None


def _extraire(chemin: str, motif: str) -> str | None:
    texte = _lire(chemin)
    if texte is None:
        return None
    m = re.search(motif, texte, re.M)
    return m.group(1) if m else None


def versions() -> dict[str, str | None]:
    package = _lire("apps/web/package.json")
    web = json.loads(package) if package else {}
    return {
        "application": _extraire(
            "packages/domain/src/metreo_domain/__init__.py",
            r'__version__\s*=\s*["\']([^"\']+)["\']',
        ),
        "api_pyproject": _extraire("apps/api/pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "python_image": _extraire("infra/api.Dockerfile", r"^FROM (python:[^\s]+)"),
        "python_requis": _extraire("apps/api/pyproject.toml", r'requires-python\s*=\s*"([^"]+)"'),
        "node_image": _extraire("infra/web.Dockerfile", r"^FROM (node:[^\s]+)"),
        "next": (web.get("dependencies") or {}).get("next"),
        "react": (web.get("dependencies") or {}).get("react"),
        "postgres_image": _extraire(
            "infra/docker-compose.staging.yml", r"image:\s*(postgis/postgis:[^\s]+)"
        ),
        "proxy_image": _extraire("infra/docker-compose.staging.yml", r"image:\s*(caddy:[^\s]+)"),
    }


def variables_obligatoires() -> list[str]:
    """Les noms, et rien que les noms.

    Ce sont les `${VAR:?...}` de la composition de préproduction : celles dont
    l'absence empêche la pile de se résoudre.
    """
    texte = _lire("infra/docker-compose.staging.yml") or ""
    return sorted(set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", texte)))


def empreintes_fichiers() -> dict[str, str | None]:
    resultat: dict[str, str | None] = {}
    for chemin in FICHIERS_DE_DEPLOIEMENT:
        fichier = RACINE / chemin
        resultat[chemin] = (
            hashlib.sha256(fichier.read_bytes()).hexdigest() if fichier.is_file() else None
        )
    return resultat


def empreintes_images(images: list[str]) -> list[dict[str, str | None]]:
    """L'empreinte que Docker a calculée, demandée à Docker.

    `Id` est l'empreinte du contenu de l'image locale. `RepoDigests` reste vide
    tant que rien n'est publié — et c'est exactement ce qu'on veut voir ici :
    ce candidat n'a été poussé nulle part.
    """
    resultat = []
    for image in images:
        identifiant = _executer(["docker", "image", "inspect", "-f", "{{.Id}}", image])
        publiee = _executer(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", image])
        resultat.append(
            {
                "image": image,
                "empreinte_locale": identifiant,
                "publiee_sous": publiee if publiee and publiee != "[]" else None,
            }
        )
    return resultat


def _json_ou_none(chemin: str | None):
    if not chemin:
        return None
    fichier = Path(chemin)
    if not fichier.is_file():
        return {"etat": "absent", "fichier": chemin}
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"etat": "illisible", "fichier": chemin}


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description="Manifeste d'un candidat de release")
    parseur.add_argument("--sortie", required=True)
    parseur.add_argument("--sha", required=True, help="SHA Git complet du commit répété")
    parseur.add_argument("--horodatage", required=True, help="Date UTC ISO-8601")
    parseur.add_argument(
        "--image", action="append", default=[], help="Image construite (répétable)"
    )
    parseur.add_argument("--repetition", help="JSON du résultat de la répétition")
    parseur.add_argument("--sauvegarde", help="JSON du résultat de sauvegarde/restauration")
    arguments = parseur.parse_args(argv)

    if len(arguments.sha) != 40 or not re.fullmatch(r"[0-9a-f]{40}", arguments.sha):
        # Un SHA court désigne un commit aujourd'hui et peut-être deux dans dix
        # ans. Un manifeste qui ne désigne pas un contenu unique ne sert à rien.
        print(f"SHA Git incomplet : {arguments.sha!r} — 40 caractères attendus", file=sys.stderr)
        return 2

    manifeste = {
        "candidat_de_release": {
            "sha_git": arguments.sha,
            "date_utc": arguments.horodatage,
            "publie_dans_un_registre": False,
            "note": (
                "Le SHA vert constitue le candidat. Aucune étiquette stable n'est "
                "posée et aucune image n'est publiée tant que l'hébergement n'est "
                "pas choisi."
            ),
        },
        "schema": {"tete_alembic": tete_alembic()},
        "versions": versions(),
        "images": empreintes_images(arguments.image),
        "variables_obligatoires": variables_obligatoires(),
        "repetition_staging": _json_ou_none(arguments.repetition),
        "sauvegarde_restauration": _json_ou_none(arguments.sauvegarde),
        "empreintes_fichiers_de_deploiement": empreintes_fichiers(),
    }

    sortie = Path(arguments.sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(json.dumps(manifeste, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"manifeste écrit : {sortie}")
    print(f"  sha        {manifeste['candidat_de_release']['sha_git']}")
    print(f"  alembic    {manifeste['schema']['tete_alembic']}")
    print(f"  variables  {len(manifeste['variables_obligatoires'])} obligatoires")
    print(f"  fichiers   {len(manifeste['empreintes_fichiers_de_deploiement'])} empreintés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
