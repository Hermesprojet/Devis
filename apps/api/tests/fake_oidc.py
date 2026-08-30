"""Un fournisseur OIDC minimal, en mémoire, pour les tests.

Il signe de vrais jetons RS256 avec une vraie paire de clés et publie un vrai
document de découverte et un vrai JWKS. Un faux fournisseur qui signerait en
HS256 ou renverrait des jetons non signés ne prouverait rien : c'est justement
la vérification de signature asymétrique que l'on veut exercer.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64u(entier: int) -> str:
    brut = entier.to_bytes((entier.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(brut).rstrip(b"=").decode()


@dataclass
class FakeProvider:
    """Émetteur, clés, et un transport httpx qui répond à sa place."""

    issuer: str = "https://issuer.example.invalid"
    client_id: str = "metreo-staging"
    client_secret: str = "secret-de-test-sans-valeur"
    kid: str = "essai-1"
    _key: Any = field(default=None, init=False)
    #: Codes d'autorisation en attente : code -> revendications à signer.
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Codes déjà échangés, pour refuser le rejeu comme un vrai fournisseur.
    used: set[str] = field(default_factory=set)
    token_calls: int = 0

    def __post_init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # -- ce que le fournisseur publie -------------------------------------

    @property
    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }

    @property
    def jwks(self) -> dict[str, Any]:
        nombres = self._key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64u(nombres.n),
                    "e": _b64u(nombres.e),
                }
            ]
        }

    # -- fabrication de jetons --------------------------------------------

    def id_token(
        self,
        *,
        subject: str = "sujet-1",
        email: str | None = "admin@example.invalid",
        email_verified: bool = True,
        nonce: str = "",
        audience: str | None = None,
        issuer: str | None = None,
        signer: Any = None,
        name: str = "Administrateur",
        expires_in: int = 300,
        decalage: int = 0,
    ) -> str:
        # `decalage` déplace l'horloge du fournisseur, en secondes. Deux
        # machines n'ont jamais la même heure ; sans ce réglage, aucun test ne
        # peut dire ce que l'application fait d'un jeton daté de travers.
        maintenant = int(time.time()) + decalage
        charge: dict[str, Any] = {
            "iss": issuer or self.issuer,
            "sub": subject,
            "aud": audience or self.client_id,
            "iat": maintenant,
            "exp": maintenant + expires_in,
            "nonce": nonce,
            "name": name,
        }
        if email is not None:
            charge["email"] = email
            charge["email_verified"] = email_verified
        return jwt.encode(charge, signer or self._key, algorithm="RS256", headers={"kid": self.kid})

    def authorize(self, code: str, **revendications: Any) -> None:
        """Prépare ce que l'échange de `code` renverra."""
        self.pending[code] = revendications

    # -- transport --------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        def repondre(requete: httpx.Request) -> httpx.Response:
            chemin = requete.url.path
            if chemin.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json=self.discovery)
            if chemin.endswith("/jwks"):
                return httpx.Response(200, json=self.jwks)
            if chemin.endswith("/token"):
                self.token_calls += 1
                corps = dict(
                    pair.split("=", 1)
                    for pair in requete.content.decode().split("&")
                    if "=" in pair
                )
                from urllib.parse import unquote_plus

                code = unquote_plus(corps.get("code", ""))
                if code in self.used or code not in self.pending:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.used.add(code)
                revendications = self.pending[code]
                return httpx.Response(
                    200,
                    json={
                        "access_token": "jeton-d-acces-sans-usage",
                        "token_type": "Bearer",
                        "id_token": self.id_token(**revendications),
                    },
                )
            return httpx.Response(404, json={"error": "not_found"})

        return httpx.MockTransport(repondre)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport(), base_url=self.issuer)

    def jwk_client(self) -> Any:
        """Un `PyJWKClient` qui lit le JWKS par le transport simulé."""
        from jwt import PyJWKClient

        client = PyJWKClient(f"{self.issuer}/jwks", cache_keys=False)
        jeu = json.dumps(self.jwks)

        def fetch(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return json.loads(jeu)

        client.fetch_data = fetch  # type: ignore[method-assign]
        return client
