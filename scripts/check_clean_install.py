"""Prouve qu'une installation faite depuis les seuls manifestes démarre.

Le défaut `pydantic[email]` a tenu parce que la CI installait une liste de
paquets écrite à la main : elle validait un jeu de dépendances qui n'était pas
celui du dépôt. Ce contrôle refait l'expérience à l'envers — un environnement
vierge, rien d'autre que `packages/domain` et `apps/api`, puis on importe
l'application et on interroge son point de santé. Un extra oublié dans un
`pyproject.toml` échoue ici, et non chez le premier développeur qui clone.

Usage : python scripts/check_clean_install.py [--constraints constraints/api.txt]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False, **kw)  # type: ignore[arg-type]


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
        install += [str(ROOT / "packages" / "domain"), str(ROOT / "apps" / "api")]

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

        print(
            "Installation propre valide — "
            f"{payload['paths']} chemins, {payload['schemas']} schémas."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
