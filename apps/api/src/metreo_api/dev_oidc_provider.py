"""Un fournisseur OIDC minimal, servi localement, pour la recette navigateur.

Il n'existe que pour les tests de bout en bout : un parcours de connexion ne
se prouve pas sans fournisseur, et dépendre d'un service externe rendrait la
recette payante et fragile.

Il signe de vrais jetons RS256 avec une paire de clés engendrée au démarrage.
Ce n'est pas un fournisseur d'identité : il accepte n'importe quelle adresse
sans mot de passe. Il refuse donc de démarrer hors développement et hors test.
"""

from __future__ import annotations

import base64
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "recette-1"
_CODES: dict[str, dict[str, Any]] = {}


def _b64u(entier: int) -> str:
    brut = entier.to_bytes((entier.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(brut).rstrip(b"=").decode()


def create_provider_app(issuer: str, *, client_id: str, decalage_secondes: int = 0) -> FastAPI:
    application = FastAPI(title="Fournisseur OIDC de recette", docs_url=None, redoc_url=None)

    @application.get("/.well-known/openid-configuration")
    def decouverte() -> dict[str, Any]:
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/jwks",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    @application.get("/jwks")
    def jwks() -> dict[str, Any]:
        nombres = _KEY.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": _KID,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64u(nombres.n),
                    "e": _b64u(nombres.e),
                }
            ]
        }

    @application.get("/authorize", response_class=HTMLResponse)
    def autoriser(
        redirect_uri: str = Query(...),
        state: str = Query(...),
        nonce: str = Query(""),
    ) -> HTMLResponse:
        """Un écran de connexion volontairement nu : une adresse, un bouton."""
        return HTMLResponse(
            f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Fournisseur de recette</title></head><body>
<h1>Connexion (fournisseur de recette)</h1>
<form method="post" action="/authorize">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="nonce" value="{nonce}">
  <label for="email">Adresse e-mail</label>
  <input id="email" name="email" type="email" required>
  <button type="submit">Se connecter</button>
</form></body></html>"""
        )

    @application.post("/authorize")
    def valider(
        email: str = Form(...),
        redirect_uri: str = Form(...),
        state: str = Form(...),
        nonce: str = Form(""),
    ) -> RedirectResponse:
        code = secrets.token_urlsafe(24)
        _CODES[code] = {"email": email.strip().lower(), "nonce": nonce}
        separateur = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            f"{redirect_uri}{separateur}{urlencode({'code': code, 'state': state})}",
            status_code=303,
        )

    @application.post("/token")
    def jeton(code: str = Form(...)) -> dict[str, Any]:
        donnees = _CODES.pop(code, None)
        if donnees is None:
            raise HTTPException(status_code=400, detail={"error": "invalid_grant"})
        # `decalage_secondes` simule une horloge de fournisseur qui avance ou
        # retarde. Sans lui, aucun banc ne peut éprouver ce que l'application
        # fait d'un jeton daté de travers — et c'est le premier reproche fait
        # aux connexions qui échouent « sans raison » en exploitation.
        maintenant = int(time.time()) + decalage_secondes
        id_token = jwt.encode(
            {
                "iss": issuer,
                "sub": f"recette|{donnees['email']}",
                "aud": client_id,
                "iat": maintenant,
                "exp": maintenant + 300,
                "nonce": donnees["nonce"],
                "email": donnees["email"],
                "email_verified": True,
                "name": donnees["email"].split("@")[0],
            },
            _KEY,
            algorithm="RS256",
            headers={"kid": _KID},
        )
        return {"access_token": "recette", "token_type": "Bearer", "id_token": id_token}

    return application


def main() -> int:  # pragma: no cover - point d'entrée
    import argparse
    import os

    import uvicorn

    environnement = os.environ.get("METREO_ENVIRONMENT", "development")
    if environnement not in ("development", "test"):
        print(
            "Ce fournisseur accepte n'importe quelle adresse sans mot de passe. "
            f"Il refuse de démarrer en environnement {environnement!r}.",
        )
        return 2

    parseur = argparse.ArgumentParser(prog="python -m metreo_api.dev_oidc_provider")
    parseur.add_argument("--port", type=int, default=8021)
    parseur.add_argument("--client-id", default="metreo-recette")
    # L'émetteur ANNONCÉ, qui peut différer de l'adresse d'écoute. Dans un
    # réseau de conteneurs, l'application joint le fournisseur par son nom de
    # service (`http://oidc:8021`) et non par `127.0.0.1` — et le document de
    # découverte doit déclarer exactement l'émetteur configuré côté
    # application, sinon `discover()` refuse, à juste titre.
    parseur.add_argument("--issuer", default="")
    # L'adresse d'écoute reste la boucle locale par défaut : ce fournisseur
    # accepte n'importe quelle adresse sans mot de passe, et ne doit s'ouvrir
    # au réseau que lorsqu'on le demande explicitement.
    parseur.add_argument("--host", default="127.0.0.1")
    # Décalage d'horloge simulé, en secondes, positif ou négatif. Outil de banc
    # uniquement : il ne sert qu'à dater les jetons de travers.
    parseur.add_argument("--decalage-secondes", type=int, default=0)
    arguments = parseur.parse_args()
    issuer = arguments.issuer or f"http://127.0.0.1:{arguments.port}"
    uvicorn.run(
        create_provider_app(
            issuer,
            client_id=arguments.client_id,
            decalage_secondes=arguments.decalage_secondes,
        ),
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
