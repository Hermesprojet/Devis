"""Prouve qu'une installation faite depuis les seuls manifestes démarre.

Le défaut `pydantic[email]` a tenu parce que la CI installait une liste de
paquets écrite à la main : elle validait un jeu de dépendances qui n'était pas
celui du dépôt. Ce contrôle refait l'expérience à l'envers — un environnement
vierge, rien d'autre que `packages/domain` et `apps/api`, puis on importe
l'application et on interroge son point de santé. Un extra oublié dans un
`pyproject.toml` échoue ici, et non chez le premier développeur qui clone.

Démarrer ne suffit pas : le contrôle inspecte ensuite les versions réellement
installées. Le verrou doit correspondre exactement à ce que pip a posé, la
clôture installée doit être entièrement épinglée, et chaque exigence des
manifestes — extras compris — doit être satisfaite par la version présente.

Usage : python scripts/check_clean_install.py [--constraints constraints/api.txt]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Installées par la construction de l'environnement lui-même, et non par les
# manifestes : elles n'ont pas à figurer dans le verrou d'exécution.
BOOTSTRAP = {"pip", "setuptools", "wheel"}
# Les deux distributions du dépôt, installées depuis l'arborescence locale.
LOCAL = {"metreo-domain", "metreo-api"}


def normalise(name: str) -> str:
    """Normalisation PEP 503, sans dépendre de `packaging`.

    L'interpréteur qui exécute ce script n'a rien d'installé : la comparaison
    des noms doit tenir avec la seule bibliothèque standard.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, _, pinned = line.partition("==")
        pins[normalise(name.strip())] = pinned.strip()
    return pins


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", default=None)
    args = parser.parse_args()

    if args.constraints:
        lock = (ROOT / args.constraints).read_text(encoding="utf-8")
        unportable = [
            line
            for line in lock.splitlines()
            if line and not line.startswith("#") and ("@" in line or line.startswith("/"))
        ]
        if unportable:
            # `pip freeze` écrit les paquets installés depuis un chemin local
            # sous la forme « nom @ file:///... ». Un tel verrou épingle
            # l'arborescence de la machine qui l'a produit : il échoue partout
            # ailleurs, et il y fuite un chemin personnel.
            print("Verrou non portable — chemins locaux épinglés :", file=sys.stderr)
            for line in unportable:
                print(f"  {line}", file=sys.stderr)
            return 1

    with tempfile.TemporaryDirectory() as tmp:
        env_dir = Path(tmp) / "venv"
        print(f"Environnement vierge : {env_dir}")
        venv.EnvBuilder(with_pip=True).create(env_dir)
        python = env_dir / "bin" / "python"

        # Comme `make install` : un pip d'origine peut être trop ancien pour
        # les métadonnées de certaines roues.
        run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])

        install = [str(python), "-m", "pip", "install", "--quiet", "--no-cache-dir"]
        if args.constraints:
            install += ["-c", str(ROOT / args.constraints)]
        # Le même jeu que `make lock` résout : sans l'extra `postgres`, le
        # verrou décrirait un environnement que ce contrôle n'installe pas,
        # et la comparaison des versions n'aurait plus de sens.
        install += [str(ROOT / "packages" / "domain"), f"{ROOT / 'apps' / 'api'}[postgres]"]

        print("Installation depuis les seuls manifestes…")
        proc = run(install)
        if proc.returncode != 0:
            print("ÉCHEC de l'installation :", file=sys.stderr)
            # Sortie complète : pip place la section « The conflict is caused
            # by » au milieu, et la tronquer avait masqué la cause réelle
            # pendant deux tours de CI.
            print(proc.stdout, file=sys.stderr)
            print(proc.stderr, file=sys.stderr)
            return 1

        # Aucune dépendance de développement n'est installée : le contrôle
        # échoue si le code applicatif en réclame une au moment de l'import.
        probe = (
            "import json, os;"
            "os.environ.setdefault('METREO_ENVIRONMENT', 'development');"
            "os.environ.setdefault('METREO_AUTH_MODE', 'dev');"
            "from metreo_api.main import create_app;"
            "app = create_app();"
            # Générer le schéma complet exerce chaque modèle Pydantic : c'est
            # exactement là que l'extra `email` manquant se manifestait.
            "spec = app.openapi();"
            "print(json.dumps({'paths': len(spec['paths']),"
            " 'schemas': len(spec.get('components', {}).get('schemas', {}))}))"
        )
        print("Import de l'application et génération du schéma OpenAPI…")
        proc = run([str(python), "-c", probe])
        if proc.returncode != 0:
            print("ÉCHEC de l'import :", file=sys.stderr)
            print(proc.stdout[-3000:], proc.stderr[-3000:], file=sys.stderr)
            return 1

        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        if payload["paths"] < 10 or payload["schemas"] < 10:
            print(f"Schéma anormalement pauvre : {payload}", file=sys.stderr)
            return 1

        print("Relevé des versions réellement installées…")
        proc = run([str(python), "-m", "pip", "list", "--format=json"])
        if proc.returncode != 0:
            print("ÉCHEC du relevé :", proc.stderr, file=sys.stderr)
            return 1
        installed = {
            normalise(entry["name"]): entry["version"] for entry in json.loads(proc.stdout)
        }

        failures: list[str] = []

        if args.constraints:
            pins = read_pins(ROOT / args.constraints)
            # Le verrou promet une résolution ; il faut vérifier que c'est bien
            # celle-là qui a été posée. Un verrou périmé ou contourné se voit
            # ici, alors que l'application démarrerait sans rien signaler.
            for name, pinned in sorted(pins.items()):
                actual = installed.get(name)
                if actual is None:
                    failures.append(f"{name}=={pinned} est verrouillé mais n'est pas installé")
                elif actual != pinned:
                    failures.append(f"{name} : verrou {pinned}, installé {actual}")
            # Et l'inverse : une dépendance transitive absente du verrou n'est
            # pas reproductible — elle flotte au gré des publications amont.
            unpinned = sorted(
                name
                for name in installed
                if name not in pins and name not in BOOTSTRAP and name not in LOCAL
            )
            for name in unpinned:
                failures.append(f"{name}=={installed[name]} est installé mais absent du verrou")

        # `packaging` n'est pas une dépendance du produit : il n'est posé
        # qu'après le relevé ci-dessus, pour ne pas fausser la clôture mesurée.
        run([str(python), "-m", "pip", "install", "--quiet", "--no-cache-dir", "packaging"])
        print("Vérification des exigences des manifestes contre les versions posées…")
        proc = run([str(python), str(ROOT / "scripts" / "verify_dependency_closure.py")])
        if not proc.stdout.strip():
            print("ÉCHEC de la vérification :", proc.stderr, file=sys.stderr)
            return 1
        closure = json.loads(proc.stdout.strip().splitlines()[-1])
        failures.extend(closure["problems"])

        if failures:
            print("Versions installées non conformes :", file=sys.stderr)
            for failure in failures:
                print(f"  {failure}", file=sys.stderr)
            return 1

        print(
            "Installation propre valide — "
            f"{payload['paths']} chemins, {payload['schemas']} schémas, "
            f"{len(installed)} distributions, {closure['edges']} exigences honorées."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
