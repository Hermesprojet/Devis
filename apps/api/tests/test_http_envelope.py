"""L'enveloppe HTTP : origines autorisées, corrélation, en-têtes lisibles.

Trois propriétés portées par `create_app`, aucune vérifiée. Mesuré, sur `main`,
par une campagne de mutation : six mutations sur `main.py`, une seule tuée.

* `allow_origins=settings.cors_origins` remplacé par `["*"]` — la liste
  d'origines autorisées n'était contrôlée nulle part ;
* la borne de 64 caractères sur un `X-Request-Id` fourni par le client —
  le commentaire du code l'annonce, rien ne l'exerçait ;
* `expose_headers` — sans lui, un navigateur ne peut lire ni l'identifiant de
  corrélation ni le nom de fichier d'un export, et le téléchargement CSV
  arrive sans nom.

La seule mutation tuée était le renvoi de `X-Request-Id`, couvert par
`test_audit_integrity.py::test_the_request_id_reaches_the_response_header_and_the_event`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

#: L'origine que la configuration par défaut autorise (`config.py`).
ORIGINE_AUTORISEE = "http://localhost:3000"
ORIGINE_INCONNUE = "https://ailleurs.example"

UNE_ROUTE_PUBLIQUE = "/api/v1/health"


def test_only_a_configured_origin_is_allowed(client: TestClient) -> None:
    """Une origine hors liste ne reçoit pas d'autorisation.

    Les deux moitiés comptent : sans la première, une configuration qui
    n'autoriserait rien passerait ; sans la seconde, `allow_origins=["*"]`
    passerait aussi.
    """
    autorisee = client.get(UNE_ROUTE_PUBLIQUE, headers={"Origin": ORIGINE_AUTORISEE})
    assert autorisee.headers.get("access-control-allow-origin") == ORIGINE_AUTORISEE

    inconnue = client.get(UNE_ROUTE_PUBLIQUE, headers={"Origin": ORIGINE_INCONNUE})
    assert inconnue.headers.get("access-control-allow-origin") is None, (
        "une origine hors liste ne doit recevoir aucune autorisation"
    )


def test_the_browser_can_read_the_correlation_id_and_the_download_name(
    client: TestClient,
) -> None:
    """`expose_headers` n'est pas décoratif.

    Sans lui, `Content-Disposition` reste invisible au JavaScript qui déclenche
    le téléchargement, et l'export arrive sans nom de fichier.
    """
    reponse = client.get(UNE_ROUTE_PUBLIQUE, headers={"Origin": ORIGINE_AUTORISEE})
    exposes = reponse.headers.get("access-control-expose-headers", "")
    normalises = {partie.strip().lower() for partie in exposes.split(",")}
    assert "x-request-id" in normalises, exposes
    assert "content-disposition" in normalises, exposes


class TestTheSuppliedCorrelationId:
    """Un identifiant fourni par le client est repris, mais pas à n'importe quel prix."""

    def test_a_short_identifier_is_kept_as_given(self, client: TestClient) -> None:
        reponse = client.get(UNE_ROUTE_PUBLIQUE, headers={"X-Request-Id": "trace-42"})
        assert reponse.headers["X-Request-Id"] == "trace-42"

    def test_an_over_long_identifier_is_replaced_not_truncated(self, client: TestClient) -> None:
        """Remplacé, jamais coupé : un préfixe conservé resterait attribuable.

        Le code borne à 64 caractères ; l'identifiant de remplacement est un
        `uuid4().hex`, donc 32 caractères hexadécimaux.
        """
        trop_long = "x" * 200
        reponse = client.get(UNE_ROUTE_PUBLIQUE, headers={"X-Request-Id": trop_long})
        renvoye = reponse.headers["X-Request-Id"]

        assert renvoye != trop_long
        assert not trop_long.startswith(renvoye), "un préfixe conservé serait une troncature"
        assert len(renvoye) == 32
        assert all(caractere in "0123456789abcdef" for caractere in renvoye)

    def test_the_boundary_is_where_the_code_says_it_is(self, client: TestClient) -> None:
        """64 caractères passent, 65 non — la borne est `<= 64`."""
        juste = "a" * 64
        assert (
            client.get(UNE_ROUTE_PUBLIQUE, headers={"X-Request-Id": juste}).headers["X-Request-Id"]
            == juste
        )

        un_de_trop = "a" * 65
        assert (
            client.get(UNE_ROUTE_PUBLIQUE, headers={"X-Request-Id": un_de_trop}).headers[
                "X-Request-Id"
            ]
            != un_de_trop
        )
