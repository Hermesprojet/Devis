"""Le parcours de connexion OIDC, et tout ce qui doit le faire échouer.

Un test qui ne vérifierait que le cas heureux ne dirait rien : la valeur d'une
authentification est dans ce qu'elle refuse. Chaque refus ci-dessous
correspond à une attaque ou à une erreur d'exploitation réelle.

Le faux fournisseur signe en RS256 avec une vraie paire de clés (voir
`fake_oidc.py`) : c'est bien la vérification de signature asymétrique qui est
exercée, pas une version allégée.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from metreo_api.config import Settings
from metreo_api.models import ExternalIdentity, LoginTransaction, Membership, User
from metreo_api.services import oidc

from .fake_oidc import FakeProvider

REDIRECT = "https://app.example.invalid/connexion"


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def settings(provider: FakeProvider) -> Settings:
    return Settings(
        environment="test",
        auth_mode="oidc",
        jwt_secret="secret-de-test-sans-valeur-0123456789",
        oidc_issuer=provider.issuer,
        oidc_client_id=provider.client_id,
        oidc_client_secret=provider.client_secret,
        oidc_redirect_uri=REDIRECT,
    )


def _session(seeded_client: TestClient):
    from metreo_api.db import get_session_factory

    return get_session_factory()()


def _transaction(session, provider: FakeProvider, settings: Settings) -> LoginTransaction:
    metadata = oidc.discover(settings, client=provider.client())
    oidc.start(session, settings, metadata=metadata)
    session.commit()
    return session.scalars(
        select(LoginTransaction).order_by(LoginTransaction.created_at.desc())
    ).first()


# -- la découverte ---------------------------------------------------------


def test_discovery_reads_every_endpoint_from_the_provider(
    provider: FakeProvider, settings: Settings
) -> None:
    """Rien n'est codé en dur : tout vient du document de découverte."""
    metadata = oidc.discover(settings, client=provider.client())
    assert metadata.authorization_endpoint == f"{provider.issuer}/authorize"
    assert metadata.token_endpoint == f"{provider.issuer}/token"
    assert metadata.jwks_uri == f"{provider.issuer}/jwks"


def test_a_provider_that_announces_another_issuer_is_refused(
    provider: FakeProvider, settings: Settings
) -> None:
    """Le document doit se réclamer de l'émetteur qu'on a configuré.

    Sans ce contrôle, un document servi ailleurs pourrait rediriger le parcours
    vers les points d'entrée d'un tiers.
    """
    provider.issuer = "https://autre.example.invalid"
    with pytest.raises(oidc.OidcError) as leve:
        oidc.discover(settings, client=provider.client())
    assert leve.value.code == "issuer_mismatch"


def test_an_unreachable_provider_is_a_service_error_not_a_crash(settings: Settings) -> None:
    def refuser(_requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injoignable")

    client = httpx.Client(transport=httpx.MockTransport(refuser))
    with pytest.raises(oidc.OidcError) as leve:
        oidc.discover(settings, client=client)
    assert leve.value.code == "provider_unavailable"
    assert leve.value.status_code == 503


# -- PKCE ------------------------------------------------------------------


def test_the_pkce_challenge_is_the_sha256_of_the_verifier() -> None:
    """S256, et rien d'autre : `plain` n'apporte aucune protection."""
    import base64
    import hashlib

    verificateur = oidc.new_verifier()
    attendu = (
        base64.urlsafe_b64encode(hashlib.sha256(verificateur.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert oidc.challenge_for(verificateur) == attendu
    assert 43 <= len(verificateur) <= 128


def test_the_authorization_url_carries_state_nonce_and_challenge(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    with _session(seeded_client) as session:
        metadata = oidc.discover(settings, client=provider.client())
        url = oidc.start(session, settings, metadata=metadata)
        session.commit()

    parametres = parse_qs(urlsplit(url).query)
    assert parametres["response_type"] == ["code"]
    assert parametres["code_challenge_method"] == ["S256"]
    assert parametres["client_id"] == [provider.client_id]
    assert parametres["redirect_uri"] == [REDIRECT]
    for obligatoire in ("state", "nonce", "code_challenge"):
        assert parametres[obligatoire][0], f"{obligatoire} absent de l'URL d'autorisation"
    # Le vérificateur ne doit jamais partir chez le fournisseur.
    assert "code_verifier" not in parametres


# -- le retour -------------------------------------------------------------


def test_a_valid_round_trip_links_the_identity_and_yields_a_session(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    with _session(seeded_client) as session:
        transaction = _transaction(session, provider, settings)
        provider.authorize(
            "code-1",
            subject="sujet-1",
            email="admin@dubois.demo",
            email_verified=True,
            nonce=transaction.nonce,
        )
        metadata = oidc.discover(settings, client=provider.client())
        jetons = oidc._exchange(
            settings,
            metadata=metadata,
            code="code-1",
            verifier=transaction.code_verifier,
            redirect_uri=REDIRECT,
            client=provider.client(),
        )
        claims = oidc.verify_id_token(
            settings,
            metadata=metadata,
            id_token=jetons["id_token"],
            nonce=transaction.nonce,
            jwk_client=provider.jwk_client(),
        )
        assert claims.subject == "sujet-1"
        assert claims.email == "admin@dubois.demo"

        utilisateur = oidc.resolve_user(session, claims)
        session.commit()
        assert utilisateur.email == "admin@dubois.demo"

        liaison = session.scalars(
            select(ExternalIdentity).where(ExternalIdentity.subject == "sujet-1")
        ).one()
        assert liaison.user_id == utilisateur.id
        assert liaison.email_at_link == "admin@dubois.demo"


def test_an_unknown_state_is_refused(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    with _session(seeded_client) as session, pytest.raises(oidc.OidcError) as leve:
        oidc._consume_transaction(session, "un-state-jamais-emis")
    assert leve.value.code == "invalid_state"


def test_a_state_cannot_be_used_twice(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    """Le rejeu est refusé par l'état persistant, pas par une fenêtre de temps."""
    with _session(seeded_client) as session:
        transaction = _transaction(session, provider, settings)
        oidc._consume_transaction(session, transaction.state)
        session.commit()
        with pytest.raises(oidc.OidcError) as leve:
            oidc._consume_transaction(session, transaction.state)
    assert leve.value.code == "invalid_state"


def test_an_expired_transaction_is_refused(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    from datetime import datetime, timedelta

    with _session(seeded_client) as session:
        transaction = _transaction(session, provider, settings)
        transaction.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
        with pytest.raises(oidc.OidcError) as leve:
            oidc._consume_transaction(session, transaction.state)
    assert leve.value.code == "expired_state"


def test_a_wrong_nonce_is_refused(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    """Un jeton d'identité obtenu ailleurs ne doit pas pouvoir être rejoué.

    Le `nonce` n'est pas contrôlé par la bibliothèque : c'est à l'appelant de
    le comparer à celui qu'il a émis, et l'oublier est une faille silencieuse.
    """
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="un-nonce-etranger")
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings,
            metadata=metadata,
            id_token=jeton,
            nonce="le-notre",
            jwk_client=provider.jwk_client(),
        )
    assert leve.value.code == "invalid_nonce"


def test_an_authorization_code_cannot_be_replayed(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    metadata = oidc.discover(settings, client=provider.client())
    provider.authorize("code-unique", nonce="n")
    oidc._exchange(
        settings,
        metadata=metadata,
        code="code-unique",
        verifier="v",
        redirect_uri=REDIRECT,
        client=provider.client(),
    )
    with pytest.raises(oidc.OidcError) as leve:
        oidc._exchange(
            settings,
            metadata=metadata,
            code="code-unique",
            verifier="v",
            redirect_uri=REDIRECT,
            client=provider.client(),
        )
    assert leve.value.code == "code_rejected"


# -- la signature et les revendications ------------------------------------


def test_a_token_signed_by_another_key_is_refused(
    provider: FakeProvider, settings: Settings
) -> None:
    """Le cœur du sujet : une signature étrangère ne passe pas."""
    metadata = oidc.discover(settings, client=provider.client())
    autre = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jeton = provider.id_token(nonce="n", signer=autre)
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "invalid_token"


def test_a_token_for_another_audience_is_refused(
    provider: FakeProvider, settings: Settings
) -> None:
    """Un jeton valide pour une autre application n'est pas valide ici."""
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="n", audience="une-autre-application")
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "invalid_audience"


def test_a_token_from_another_issuer_is_refused(provider: FakeProvider, settings: Settings) -> None:
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="n", issuer="https://ailleurs.example.invalid")
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "invalid_issuer"


def test_an_expired_token_is_refused(provider: FakeProvider, settings: Settings) -> None:
    metadata = oidc.discover(settings, client=provider.client())
    # Au-DELÀ de la tolérance d'horloge : sans quoi ce test ne dirait plus si
    # le jeton est refusé parce qu'il a expiré ou parce qu'on ne tolère rien.
    perime = -(settings.oidc_clock_skew_seconds + 10)
    jeton = provider.id_token(nonce="n", expires_in=perime)
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "token_expired"


def test_a_provider_clock_slightly_ahead_does_not_break_every_login(
    provider: FakeProvider, settings: Settings
) -> None:
    """Un fournisseur qui avance d'une minute reste utilisable.

    Sans tolérance, PyJWT déclare « pas encore valide » un jeton daté du futur,
    et PLUS AUCUNE connexion n'aboutit tant que les horloges divergent — mesuré
    au banc OIDC : à +60 s, toutes les tentatives échouaient sur un motif qui ne
    parlait pas d'heure.
    """
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="n", decalage=settings.oidc_clock_skew_seconds - 5)
    revendications = oidc.verify_id_token(
        settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
    )
    assert revendications.subject == "sujet-1"


def test_a_provider_clock_far_ahead_is_refused_by_its_own_name(
    provider: FakeProvider, settings: Settings
) -> None:
    """Au-delà de la tolérance, le refus nomme l'heure et non une fraude.

    Confondre « daté du futur » avec « jeton invalide » envoyait chercher une
    attaque là où il n'y a qu'un serveur de temps à régler.
    """
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="n", decalage=settings.oidc_clock_skew_seconds + 600)
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "token_not_yet_valid"


def test_a_token_barely_expired_still_passes_within_the_tolerance(
    provider: FakeProvider, settings: Settings
) -> None:
    """La tolérance joue des deux côtés, et c'est voulu.

    Un jeton périmé de quelques secondes vient d'une horloge en retard, pas
    d'un rejeu : le refuser déconnecterait des gens sans motif qu'ils puissent
    corriger.
    """
    metadata = oidc.discover(settings, client=provider.client())
    jeton = provider.id_token(nonce="n", expires_in=-(settings.oidc_clock_skew_seconds - 5))
    revendications = oidc.verify_id_token(
        settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
    )
    assert revendications.subject == "sujet-1"


def test_an_unsigned_token_is_refused(provider: FakeProvider, settings: Settings) -> None:
    """`alg: none` est l'attaque classique contre une vérification bâclée."""
    metadata = oidc.discover(settings, client=provider.client())
    jeton = jwt.encode(
        {
            "iss": provider.issuer,
            "sub": "s",
            "aud": provider.client_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": "n",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(oidc.OidcError) as leve:
        oidc.verify_id_token(
            settings, metadata=metadata, id_token=jeton, nonce="n", jwk_client=provider.jwk_client()
        )
    assert leve.value.code == "invalid_token"


# -- la liaison des comptes ------------------------------------------------


def test_an_unverified_email_never_creates_a_link(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    """Une adresse non certifiée par le fournisseur ne vaut rien.

    C'est le chemin par lequel on prendrait le compte de quelqu'un : déclarer
    son adresse chez un fournisseur complaisant.
    """
    claims = oidc.ExternalClaims(
        issuer=provider.issuer,
        subject="sujet-x",
        email="admin@dubois.demo",
        email_verified=False,
        full_name=None,
    )
    with _session(seeded_client) as session, pytest.raises(oidc.OidcError) as leve:
        oidc.resolve_user(session, claims)
    assert leve.value.code == "email_not_verified"


def test_an_unknown_user_is_refused_rather_than_created(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    """Aucune inscription automatique, même avec un jeton parfaitement valide."""
    claims = oidc.ExternalClaims(
        issuer=provider.issuer,
        subject="sujet-inconnu",
        email="personne@example.invalid",
        email_verified=True,
        full_name="Personne",
    )
    with _session(seeded_client) as session:
        avant = len(session.scalars(select(User)).all())
        with pytest.raises(oidc.OidcError) as leve:
            oidc.resolve_user(session, claims)
        assert len(session.scalars(select(User)).all()) == avant
    assert leve.value.code == "unknown_user"
    assert leve.value.status_code == 403


def test_a_deactivated_account_is_refused(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    with _session(seeded_client) as session:
        utilisateur = session.scalars(select(User).where(User.email == "admin@dubois.demo")).one()
        utilisateur.is_active = False
        session.commit()
        claims = oidc.ExternalClaims(
            issuer=provider.issuer,
            subject="sujet-desactive",
            email="admin@dubois.demo",
            email_verified=True,
            full_name=None,
        )
        with pytest.raises(oidc.OidcError) as leve:
            oidc.resolve_user(session, claims)
    assert leve.value.code == "account_disabled"


def test_once_linked_the_email_no_longer_decides(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    """Après la liaison, `(issuer, subject)` seul identifie.

    Si l'adresse continuait de décider, obtenir l'adresse d'un collègue chez le
    fournisseur suffirait à reprendre son compte.
    """
    with _session(seeded_client) as session:
        premier = oidc.resolve_user(
            session,
            oidc.ExternalClaims(
                issuer=provider.issuer,
                subject="sujet-stable",
                email="admin@dubois.demo",
                email_verified=True,
                full_name=None,
            ),
        )
        session.commit()

        # Le même sujet, avec une AUTRE adresse : c'est toujours le même compte.
        encore = oidc.resolve_user(
            session,
            oidc.ExternalClaims(
                issuer=provider.issuer,
                subject="sujet-stable",
                email="autre@example.invalid",
                email_verified=True,
                full_name=None,
            ),
        )
        assert encore.id == premier.id

        # Un AUTRE sujet portant l'adresse du premier crée sa propre liaison,
        # vers le même compte — c'est le cas légitime de deux fournisseurs —
        # mais ne remplace pas la première.
        oidc.resolve_user(
            session,
            oidc.ExternalClaims(
                issuer=provider.issuer,
                subject="sujet-autre",
                email="admin@dubois.demo",
                email_verified=True,
                full_name=None,
            ),
        )
        session.commit()
        liaisons = session.scalars(
            select(ExternalIdentity).where(ExternalIdentity.user_id == premier.id)
        ).all()
        assert {liaison.subject for liaison in liaisons} == {"sujet-stable", "sujet-autre"}


def test_a_user_without_active_membership_is_refused(
    seeded_client: TestClient, provider: FakeProvider, settings: Settings
) -> None:
    with _session(seeded_client) as session:
        utilisateur = session.scalars(select(User).where(User.email == "admin@dubois.demo")).one()
        for appartenance in session.scalars(
            select(Membership).where(Membership.user_id == utilisateur.id)
        ).all():
            appartenance.is_active = False
        session.commit()
        assert oidc.active_memberships(session, utilisateur) == []


# ---------------------------------------------------------------------------
# `max_age` demandé, `auth_time` constaté
# ---------------------------------------------------------------------------
#
# La réserve que ces tests ferment, mot pour mot : « Metreo n'envoie ni
# `max_age`, ni ne lit `auth_time` : il ne peut donc ni forcer la
# réauthentification, ni constater qu'elle a eu lieu. »
#
# La seconde moitié est celle qui compte. Forcer une réauthentification chez un
# fournisseur amont n'est pas en notre pouvoir — Auth0 le documente, et aucun
# paramètre n'y changera rien. CONSTATER qu'elle n'a pas eu lieu l'est, et
# c'est ce qui permet de dire à la personne pourquoi elle retombe sur le même
# compte, au lieu de la laisser croire à un défaut de Metreo.


def _transaction_de_test(*, demandee: bool, il_y_a_secondes: int = 0) -> LoginTransaction:
    return LoginTransaction(
        state="s",
        nonce="n",
        code_verifier="v",
        redirect_uri="https://exemple.invalid/retour",
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=il_y_a_secondes),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
        reauthentication_requested=demandee,
    )


def _claims(auth_time: datetime | None) -> oidc.ExternalClaims:
    return oidc.ExternalClaims(
        issuer="https://fournisseur.invalid/",
        subject="sujet-1",
        email="personne@exemple.invalid",
        email_verified=True,
        full_name=None,
        auth_time=auth_time,
    )


def test_no_verdict_is_given_when_no_reauthentication_was_asked_for() -> None:
    """La question ne se pose pas, et la réponse est « on ne demande rien ».

    C'est le cas de presque toutes les connexions. Répondre « non, elle n'a pas
    eu lieu » ici serait exact et inutile — une authentification antérieure à
    la demande est le fonctionnement même d'une session SSO. L'avis
    s'afficherait sur chaque connexion ordinaire et cesserait d'être lu.
    """
    transaction = _transaction_de_test(demandee=False)
    ancienne = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=5)

    verdict = oidc.reauthentification_constatee(
        transaction, _claims(ancienne), tolerance_secondes=60
    )
    assert verdict is None


def test_an_authentication_older_than_the_request_means_the_session_was_reused() -> None:
    """Le cas que le propriétaire doit pouvoir constater au scénario C.

    Il a cliqué « utiliser un autre compte », Metreo a exigé `max_age=0`, et le
    fournisseur a rendu une authentification vieille de cinq heures : il a
    repris la session ouverte au lieu d'en redemander une.
    """
    transaction = _transaction_de_test(demandee=True)
    ancienne = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=5)

    assert (
        oidc.reauthentification_constatee(transaction, _claims(ancienne), tolerance_secondes=60)
        is False
    )


def test_a_fresh_authentication_is_recognised_as_such() -> None:
    """Et le contre-exemple : le fournisseur a bien réauthentifié.

    Sans lui, une fonction qui répondrait toujours « non » passerait le test
    précédent, et l'avis s'afficherait même quand tout s'est bien passé.
    """
    transaction = _transaction_de_test(demandee=True)
    maintenant = datetime.now(UTC).replace(tzinfo=None)

    assert (
        oidc.reauthentification_constatee(transaction, _claims(maintenant), tolerance_secondes=60)
        is True
    )


def test_a_missing_auth_time_counts_as_no_reauthentication() -> None:
    """OpenID Connect EXIGE `auth_time` dès que `max_age` est envoyé.

    Un fournisseur qui l'omet alors ne respecte pas la spécification. On ne
    peut donc rien constater — et « je ne peux pas constater » se traite comme
    « ce n'est pas constaté », jamais comme « c'est arrivé ». Supposer que la
    réauthentification a eu lieu parce que le jeton se tait serait exactement
    l'affirmation que ce contrôle existe pour éviter.
    """
    transaction = _transaction_de_test(demandee=True)

    assert (
        oidc.reauthentification_constatee(transaction, _claims(None), tolerance_secondes=60)
        is False
    )


def test_a_slightly_early_authentication_is_accepted_within_clock_skew() -> None:
    """Deux horloges ne sont jamais d'accord à la seconde près.

    Un fournisseur qui retarde de deux secondes daterait son authentification
    d'avant une demande qu'elle suit pourtant. Sans tolérance, l'avis
    s'afficherait sur des connexions parfaitement réauthentifiées — et la même
    tolérance sert déjà à `exp` et `iat`, pour la même raison.
    """
    transaction = _transaction_de_test(demandee=True)
    un_peu_avant = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=3)

    assert (
        oidc.reauthentification_constatee(transaction, _claims(un_peu_avant), tolerance_secondes=60)
        is True
    )
    assert (
        oidc.reauthentification_constatee(transaction, _claims(un_peu_avant), tolerance_secondes=1)
        is False
    ), "au-delà de la tolérance, le constat redevient négatif"


def test_an_unreadable_auth_time_does_not_refuse_the_login() -> None:
    """Une revendication mal formée vaut « on ne sait pas », pas un refus.

    `auth_time` ne décide d'aucun accès : refuser sur une valeur illisible
    fermerait la porte à cause d'un fournisseur bavard. Le jeton reste valide,
    et c'est l'avis qui se déclenche.
    """
    for valeur in ("hier", None, True, [], {}):
        assert oidc._instant(valeur) is None, valeur

    assert oidc._instant(0) == datetime(1970, 1, 1)
