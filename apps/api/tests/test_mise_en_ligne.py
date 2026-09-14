"""Ce que le dépôt doit garantir AVANT qu'une installation existe.

Ces trois contrôles n'éprouvent aucun comportement de l'application : ils
lisent les fichiers d'infrastructure et vérifient qu'ils disent la vérité.
Chacun vient d'un défaut réel, trouvé en préparant la première mise en ligne,
et chacun produisait une panne SILENCIEUSE — une pile qui démarre, répond 200
à tous les contrôles d'exploitation, et ne sert à rien.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 1. Le fichier qui portera les secrets doit être ignoré par Git
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fichier", ["infra/staging.env", "infra/repetition.env"])
def test_les_fichiers_d_environnement_sont_ignores_par_git(fichier: str) -> None:
    """`infra/staging.env` portera trois secrets, sur le serveur lui-même.

    Le mot de passe PostgreSQL, le secret JWT et le secret client OIDC. Il a
    longtemps échappé au `.gitignore` alors que deux endroits du dépôt
    affirmaient le contraire — le commentaire au-dessus de
    `infra/repetition.env`, et `infra/staging.env.example` lui-même. Un
    `git add -A` lancé sur le serveur où on vient de le renseigner poussait
    les trois vers la forge.

    Le contrôle interroge Git plutôt que de chercher une ligne dans le
    fichier : une règle peut exister et ne rien couvrir.
    """
    if shutil.which("git") is None:
        pytest.skip("git absent")
    resultat = subprocess.run(
        ["git", "check-ignore", "-q", fichier],
        cwd=RACINE,
        capture_output=True,
        check=False,
    )
    assert resultat.returncode == 0, (
        f"{fichier} n'est PAS ignoré par Git alors qu'il porte des secrets. "
        "Ajoutez-le au .gitignore avant d'écrire quoi que ce soit dedans."
    )


# ---------------------------------------------------------------------------
# 2. L'image web publiée doit savoir où joindre l'API
# ---------------------------------------------------------------------------


def _chemin_du_cookie_public() -> str:
    source = (
        RACINE / "apps" / "api" / "src" / "metreo_api" / "routers" / "devis_public.py"
    ).read_text(encoding="utf-8")
    trouve = re.search(r'path="(/api/[^"]+)"', source)
    assert trouve, "la page publique ne pose plus de cookie sur un chemin /api/…"
    return trouve.group(1)


def test_le_travail_de_publication_fige_l_url_de_l_api_dans_l_image() -> None:
    """Sans `--build-arg`, l'image publiée pointe la boucle locale du VISITEUR.

    `NEXT_PUBLIC_API_URL` est compilée dans le JavaScript livré — la poser sur
    un conteneur déjà construit n'a aucun effet. Le défaut de
    `infra/web.Dockerfile` vaut `http://localhost:8000/api/v1` : une image
    publiée sans cet argument démarre, répond 200 à `/api/v1/health` par le
    proxy, et affiche « Chargement… » indéfiniment dans un vrai navigateur.

    La répétition de préproduction ne l'attrape pas : elle construit l'image
    AVEC l'argument. Elle est donc verte sur une image correcte pendant que
    l'image publiée serait fausse. D'où ce contrôle, qui lit le travail de
    publication lui-même.
    """
    travail = (RACINE / ".github" / "workflows" / "publier-images.yml").read_text(encoding="utf-8")

    trouve = re.search(r"--build-arg\s+NEXT_PUBLIC_API_URL=(\S+)", travail)
    assert trouve, (
        "« Publier les images » ne passe plus --build-arg NEXT_PUBLIC_API_URL : "
        "l'image publiée porterait le défaut du Dockerfile, soit la boucle "
        "locale, et le front serait muet chez tout visiteur."
    )
    valeur = trouve.group(1).rstrip("\\").strip()

    assert not valeur.endswith("/"), (
        f"{valeur!r} finit par une barre oblique : le client d'API concatène "
        "directement, les chemins deviendraient //quelque-chose"
    )

    # La valeur doit préfixer le chemin sur lequel la page client pose son
    # cookie, sinon ce cookie est posé sur un chemin que les requêtes
    # suivantes ne portent pas : la page du devis s'ouvre, puis tout répond
    # 401 « Ouvrez le lien que l'entreprise vous a communiqué ».
    cookie = _chemin_du_cookie_public()
    assert cookie.startswith(valeur), (
        f"la base d'API publiée ({valeur!r}) ne préfixe pas le chemin du "
        f"cookie de la page client ({cookie!r}) : le lien client s'ouvrirait "
        "puis refuserait tout"
    )


# ---------------------------------------------------------------------------
# 3. La surcouche « derrière un proxy » doit viser le bon port
# ---------------------------------------------------------------------------


def test_la_surcouche_derriere_proxy_vise_le_port_du_conteneur() -> None:
    """Le proxy de devant vise l'IP du conteneur, jamais la boucle locale.

    Avec le provider Docker de Traefik, `loadbalancer.server.port` désigne le
    port DU CONTENEUR. Après avoir publié un port de diagnostic en 8081, le
    réflexe est d'écrire 8081 ici — Traefik viserait alors une adresse où rien
    n'écoute : 502 sur tout le site, pile parfaitement saine.

    Et `!override` n'est pas décoratif : Compose FUSIONNE les listes de ports.
    Sans cette marque, la pile publierait encore 80 et 443 sur toutes les
    interfaces, c'est-à-dire exactement ce que la surcouche existe pour
    éviter — avec, au prochain redémarrage de l'hôte, une course au bind que
    le proxy de la machine peut perdre.
    """
    surcouche = (RACINE / "infra" / "docker-compose.derriere-proxy.yml").read_text(encoding="utf-8")

    assert "ports: !override" in surcouche, (
        "la surcouche n'emploie plus `ports: !override` : Compose fusionnerait "
        "les listes et 80/443 resteraient publiés"
    )
    assert re.search(r'-\s*"127\.0\.0\.1:', surcouche), (
        "le port de diagnostic n'est plus restreint à la boucle locale"
    )
    assert not re.search(r'-\s*"[^"]*:443"', surcouche), (
        "la surcouche publie encore le 443 : en HTTP simple Caddy ne l'ouvre "
        "pas, ce port réserverait un écouteur inexistant"
    )

    trouve = re.search(
        r"traefik\.http\.services\.\w+\.loadbalancer\.server\.port:\s*\"?(\d+)", surcouche
    )
    assert trouve, "la surcouche ne déclare plus le port du service Traefik"
    port = int(trouve.group(1))

    # Le port que Caddy écoute réellement, lu dans la composition de base.
    base = (RACINE / "infra" / "docker-compose.staging.yml").read_text(encoding="utf-8")
    cible = re.search(r'\$\{HTTP_PORT:-80\}:(\d+)"', base)
    assert cible, "la composition de base ne publie plus le port HTTP du proxy"

    assert port == int(cible.group(1)), (
        f"la surcouche envoie le proxy de devant sur le port {port} alors que "
        f"Caddy écoute le {cible.group(1)} dans le conteneur : 502 sur tout le site"
    )

    for obligatoire in ("traefik.enable", "routers.metreo.tls:", "tls.certresolver"):
        assert obligatoire in surcouche, (
            f"{obligatoire} a disparu : sans `tls` Traefik sert du clair sur le "
            "443, sans `certresolver` aucun certificat n'est jamais demandé"
        )
