#!/usr/bin/env python3
"""Banc ciblé du parcours de connexion, distinct de la répétition complète.

Pourquoi un second banc. La répétition de préproduction monte toute la pile et
joue UNE connexion. Quand cette connexion échoue une fois sur trois, elle ne
dit ni quelle étape a lâché ni si l'utilisateur aurait pu recommencer — et la
rejouer coûte huit minutes. Ce banc ne monte que ce qui touche à la connexion,
la joue des dizaines de fois, et sait couper l'API au milieu du parcours.

Pourquoi en HTTP et non au navigateur. Le parcours à la souris est déjà
éprouvé par `apps/web/e2e-premier-devis`. Ce qui manquait est ailleurs : jouer
la même séquence des dizaines de fois, en concurrence, et surtout redémarrer
l'API ENTRE deux étapes — ce qu'aucun test de navigateur ne sait exprimer. Les
requêtes reproduisent exactement celles que la page émet, dans le même ordre,
sans délai ajouté : c'est ce resserrement qui fait apparaître les courses.

Ce banc ne réessaie jamais. Un scénario qui n'aboutit qu'à la seconde tentative
est un scénario en échec : c'est précisément ce que masquerait une reprise
automatique, et c'est exactement le défaut qu'on cherche.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

RACINE = Path(__file__).resolve().parents[1]
DOSSIER_API = RACINE / "apps" / "api"

#: L'adresse de l'APPLICATION, où le fournisseur renvoie le navigateur. Ce banc
#: ne monte pas le front : il n'appelle jamais cette adresse, il lit seulement
#: les paramètres que le fournisseur y accroche, exactement comme la page le
#: ferait avant de les relayer à l'API.
REDIRECTION_APPLICATION = "http://127.0.0.1:1/application"

VERT, ROUGE, JAUNE, GRAS, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    scenario: str
    attendu: str
    obtenu: str
    reussi: bool
    detail: str = ""


VERDICTS: list[Verdict] = []


def conclure(scenario: str, attendu: str, obtenu: str, reussi: bool, detail: str = "") -> bool:
    VERDICTS.append(Verdict(scenario, attendu, obtenu, reussi, detail))
    marque = f"{VERT}✓{FIN}" if reussi else f"{ROUGE}✗{FIN}"
    print(f"   {marque} {scenario}")
    print(f"       attendu : {attendu}")
    print(f"       obtenu  : {obtenu}")
    if detail:
        for ligne in detail.splitlines():
            print(f"       {ligne}")
    return reussi


def titre(texte: str) -> None:
    print(f"\n{GRAS}── {texte}{FIN}")


# ---------------------------------------------------------------------------
# Le parcours, tel que la page l'émet
# ---------------------------------------------------------------------------


@dataclass
class Etape:
    nom: str
    code: int | None
    duree_ms: float


@dataclass
class Connexion:
    """Ce qu'une tentative de connexion a produit, étape par étape.

    `issue` ne connaît que trois valeurs, et c'est volontaire : une session
    ouverte, un refus que l'application sait expliquer, ou une panne — dont
    l'utilisateur ne peut rien faire. Le troisième cas est le seul inacceptable.
    """

    issue: str = "panne"
    motif: str | None = None
    etapes: list[Etape] = field(default_factory=list)
    jeton: str | None = None
    organisation: str | None = None
    courriel: str | None = None

    def resume(self) -> str:
        parcours = " → ".join(f"{e.nom}:{e.code}" for e in self.etapes)
        return f"{self.issue}" + (f" ({self.motif})" if self.motif else "") + f" [{parcours}]"


class Navigateur:
    """Un navigateur : ses cookies, son stockage de session, et rien d'autre.

    Deux navigateurs distincts ne partagent rien, comme deux personnes sur deux
    postes. Le `stockage` imite `sessionStorage` : ce que la page y écrirait.
    """

    def __init__(self, nom: str = "navigateur") -> None:
        self.nom = nom
        self.http = httpx.Client(follow_redirects=False, timeout=20.0)
        self.stockage: dict[str, str] = {}

    def fermer(self) -> None:
        self.http.close()

    # -- les quatre requêtes de la page ------------------------------------

    def _appel(
        self, connexion: Connexion, nom: str, methode: str, url: str, **kw
    ) -> httpx.Response:
        depart = time.perf_counter()
        reponse = self.http.request(methode, url, **kw)
        connexion.etapes.append(
            Etape(nom, reponse.status_code, (time.perf_counter() - depart) * 1000)
        )
        return reponse

    def commencer(self, api: str, connexion: Connexion) -> str | None:
        reponse = self._appel(connexion, "start", "GET", f"{api}/api/v1/auth/oidc/start")
        if reponse.status_code != 200:
            connexion.issue = "refus"
            connexion.motif = _motif(reponse)
            return None
        return str(reponse.json()["authorization_url"])

    def sauthentifier(
        self, url_autorisation: str, courriel: str, connexion: Connexion
    ) -> tuple[str | None, str | None]:
        """L'écran du fournisseur : une adresse, un bouton."""
        formulaire = self._appel(connexion, "authorize", "GET", url_autorisation)
        if formulaire.status_code != 200:
            connexion.issue = "panne"
            connexion.motif = f"fournisseur:{formulaire.status_code}"
            return None, None
        champs = dict(re.findall(r'name="(\w+)" value="([^"]*)"', formulaire.text))
        origine = urlsplit(url_autorisation)
        poste = self._appel(
            connexion,
            "connexion",
            "POST",
            f"{origine.scheme}://{origine.netloc}/authorize",
            data={**champs, "email": courriel},
        )
        if poste.status_code != 303:
            connexion.issue = "panne"
            connexion.motif = f"fournisseur:{poste.status_code}"
            return None, None
        parametres = parse_qs(urlsplit(poste.headers["location"]).query)
        return _premier(parametres, "code"), _premier(parametres, "state")

    def revenir(self, api: str, code: str, etat: str, connexion: Connexion) -> str | None:
        """Le relais que la page fait vers l'API, avec `code` et `state`."""
        reponse = self._appel(
            connexion,
            "callback",
            "GET",
            f"{api}/api/v1/auth/oidc/callback",
            params={"state": etat, "code": code},
        )
        if reponse.status_code != 303:
            connexion.issue = "panne"
            connexion.motif = f"callback:{reponse.status_code}"
            return None
        parametres = parse_qs(urlsplit(reponse.headers["location"]).query)
        erreur = _premier(parametres, "login_error")
        if erreur:
            connexion.issue = "refus"
            connexion.motif = erreur
            return None
        return _premier(parametres, "login_code")

    def echanger(self, api: str, code_connexion: str, connexion: Connexion) -> None:
        reponse = self._appel(
            connexion,
            "exchange",
            "POST",
            f"{api}/api/v1/auth/oidc/exchange",
            json={"login_code": code_connexion, "organization_id": None},
        )
        if reponse.status_code != 200:
            connexion.issue = "refus"
            connexion.motif = _motif(reponse)
            return
        corps = reponse.json()
        connexion.issue = "session"
        connexion.jeton = corps["access_token"]
        connexion.organisation = corps["organization_id"]
        self.stockage["metreo.token"] = corps["access_token"]
        self.stockage["metreo.context"] = json.dumps({"organizationId": corps["organization_id"]})

    def moi(self, api: str, connexion: Connexion) -> None:
        reponse = self._appel(
            connexion,
            "me",
            "GET",
            f"{api}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {connexion.jeton}"},
        )
        if reponse.status_code != 200:
            connexion.issue = "refus"
            connexion.motif = _motif(reponse)
            return
        connexion.courriel = reponse.json()["email"]

    # -- le parcours complet ------------------------------------------------

    def connecter(
        self,
        courriel: str,
        *,
        depart: str,
        retour: str | None = None,
        echange: str | None = None,
    ) -> Connexion:
        """Le parcours entier, sans un instant d'attente ajouté entre les étapes.

        Les trois adresses peuvent différer : c'est ainsi qu'on éprouve deux
        instances derrière un répartiteur qui n'a aucune affinité de session.
        """
        retour = retour or depart
        echange = echange or depart
        connexion = Connexion()
        url = self.commencer(depart, connexion)
        if url is None:
            return connexion
        code, etat = self.sauthentifier(url, courriel, connexion)
        if code is None or etat is None:
            return connexion
        code_connexion = self.revenir(retour, code, etat, connexion)
        if code_connexion is None:
            return connexion
        self.echanger(echange, code_connexion, connexion)
        if connexion.issue == "session":
            self.moi(echange, connexion)
        return connexion


def _premier(parametres: dict[str, list[str]], cle: str) -> str | None:
    valeurs = parametres.get(cle)
    return valeurs[0] if valeurs else None


def _motif(reponse: httpx.Response) -> str:
    try:
        detail = reponse.json()
        if isinstance(detail, dict):
            interne = detail.get("detail", detail)
            if isinstance(interne, dict):
                return f"{reponse.status_code}:{interne.get('code', '?')}"
    except Exception:
        pass
    return f"{reponse.status_code}"


# ---------------------------------------------------------------------------
# La pile
# ---------------------------------------------------------------------------


def _port_libre() -> int:
    with closing(socket.socket()) as prise:
        prise.bind(("127.0.0.1", 0))
        return int(prise.getsockname()[1])


class Pile:
    """Le fournisseur d'identité et N instances d'API, sur une base à part."""

    def __init__(self, racine: Path, url_base: str, instances: int = 1) -> None:
        self.racine = racine
        self.url_base = url_base
        self.nombre_instances = instances
        self.port_fournisseur = _port_libre()
        self.ports_api = [_port_libre() for _ in range(instances)]
        self.processus: dict[str, subprocess.Popen[bytes]] = {}
        self.decalage_fournisseur = 0

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port_fournisseur}"

    @property
    def apis(self) -> list[str]:
        return [f"http://127.0.0.1:{port}" for port in self.ports_api]

    @property
    def api(self) -> str:
        return self.apis[0]

    def _environnement(self) -> dict[str, str]:
        return {
            **os.environ,
            "METREO_DATABASE_URL": self.url_base,
            "METREO_ENVIRONMENT": "test",
            "METREO_AUTH_MODE": "oidc",
            "METREO_JWT_SECRET": "banc-oidc-jetable-0123456789abcdef",
            "METREO_OIDC_ISSUER": self.issuer,
            "METREO_OIDC_CLIENT_ID": "metreo-banc-oidc",
            "METREO_OIDC_CLIENT_SECRET": "jetable-sans-valeur-hors-de-ce-banc",
            "METREO_OIDC_REDIRECT_URI": REDIRECTION_APPLICATION,
            # Le stockage du banc vit dans SON dossier temporaire, jamais sous
            # le dépôt : un banc qui laisse des fichiers dans l'arbre de travail
            # finit par en faire commiter un.
            "METREO_STORAGE_ROOT": str(self.racine / "stockage"),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(DOSSIER_API / "src"),
                    str(RACINE / "packages" / "domain" / "src"),
                    str(RACINE / "packages" / "contracts" / "src"),
                ]
            ),
        }

    def _lancer(self, nom: str, argv: list[str]) -> None:
        journal = (self.racine / f"{nom}.log").open("ab")
        self.processus[nom] = subprocess.Popen(
            argv,
            cwd=DOSSIER_API,
            env=self._environnement(),
            stdout=journal,
            stderr=journal,
            start_new_session=True,
        )

    def _tuer(self, nom: str) -> None:
        processus = self.processus.pop(nom, None)
        if processus is None:
            return
        try:
            os.killpg(os.getpgid(processus.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            processus.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(processus.pid), signal.SIGKILL)

    def _executer(self, quoi: str, argv: list[str]) -> None:
        """Un préparatif qui rate DIT pourquoi.

        `capture_output` sans relecture transforme la moindre erreur de mise en
        place en « returned non-zero exit status 1 », et fait chercher le défaut
        dans le produit alors qu'il est dans le banc.
        """
        resultat = subprocess.run(
            argv, cwd=DOSSIER_API, env=self._environnement(), capture_output=True
        )
        if resultat.returncode != 0:
            sortie = (resultat.stdout + b"\n" + resultat.stderr).decode(errors="replace")
            raise RuntimeError(f"{quoi} a échoué :\n{sortie[-3000:]}")

    def migrer_et_amorcer(self, organisations: list[tuple[str, str]]) -> None:
        self._executer(
            "la migration",
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(DOSSIER_API / "alembic.ini"),
                "upgrade",
                "head",
            ],
        )
        for organisation, administrateur in organisations:
            self._executer(
                f"l'amorçage de {organisation!r}",
                [
                    sys.executable,
                    "-m",
                    "metreo_api.bootstrap",
                    "--organization",
                    organisation,
                    "--admin-email",
                    administrateur,
                    "--admin-name",
                    administrateur.split("@")[0],
                ],
            )

    def demarrer_fournisseur(self) -> None:
        argv = [
            sys.executable,
            "-m",
            "metreo_api.dev_oidc_provider",
            f"--port={self.port_fournisseur}",
            "--host=127.0.0.1",
            f"--issuer={self.issuer}",
            "--client-id=metreo-banc-oidc",
        ]
        if self.decalage_fournisseur:
            argv.append(f"--decalage-secondes={self.decalage_fournisseur}")
        self._lancer("fournisseur", argv)
        attendre(f"{self.issuer}/.well-known/openid-configuration")

    def demarrer_api(self, indice: int) -> None:
        self._lancer(
            f"api{indice}",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "metreo_api.main:app",
                "--port",
                str(self.ports_api[indice]),
                "--host",
                "127.0.0.1",
            ],
        )
        attendre(f"{self.apis[indice]}/api/v1/health")

    def redemarrer_api(self, indice: int = 0) -> None:
        self._tuer(f"api{indice}")
        self.demarrer_api(indice)

    def redemarrer_fournisseur(self) -> None:
        self._tuer("fournisseur")
        self.demarrer_fournisseur()

    def demarrer(self) -> None:
        self.demarrer_fournisseur()
        for indice in range(self.nombre_instances):
            self.demarrer_api(indice)

    def arreter(self) -> None:
        for nom in list(self.processus):
            self._tuer(nom)


def attendre(url: str, limite: float = 60.0) -> None:
    depart = time.monotonic()
    dernier = ""
    while time.monotonic() - depart < limite:
        try:
            reponse = httpx.get(url, timeout=3.0)
            if reponse.status_code < 500:
                return
            dernier = str(reponse.status_code)
        except Exception as erreur:
            dernier = type(erreur).__name__
        time.sleep(0.2)
    raise RuntimeError(f"{url} n'a pas répondu en {limite:.0f} s ({dernier})")


@contextmanager
def pile_montee(url_base: str, instances: int, racine: Path, organisations) -> Iterator[Pile]:
    pile = Pile(racine, url_base, instances)
    pile.migrer_et_amorcer(organisations)
    pile.demarrer()
    try:
        yield pile
    finally:
        pile.arreter()


# ---------------------------------------------------------------------------
# Les quatorze scénarios
# ---------------------------------------------------------------------------

ADMIN_A = "premiere.administratrice@banc-oidc.example"
ADMIN_B = "second.administrateur@banc-oidc.example"
ORGANISATIONS = [("Entreprise A du banc", ADMIN_A), ("Entreprise B du banc", ADMIN_B)]


def _echecs(connexions: list[Connexion]) -> str:
    perdues = [c for c in connexions if c.issue != "session"]
    if not perdues:
        return ""
    return "\n".join(f"· {c.resume()}" for c in perdues[:5])


def scenario_01_cinquante_sequentielles(pile: Pile) -> bool:
    """Cinquante connexions d'affilée sur une pile qui vient de démarrer.

    C'est le scénario qui a fait tomber la course : chacune enchaîne le retour
    du fournisseur et l'échange sans le moindre délai, là où un navigateur en
    met quelques dizaines de millisecondes.
    """
    connexions = []
    for numero in range(50):
        navigateur = Navigateur(f"séquentiel-{numero}")
        try:
            connexions.append(navigateur.connecter(ADMIN_A, depart=pile.api))
        finally:
            navigateur.fermer()
    reussies = sum(1 for c in connexions if c.issue == "session")
    return conclure(
        "50 connexions séquentielles sur une pile froide",
        "50 sessions ouvertes",
        f"{reussies} sessions sur 50",
        reussies == 50,
        _echecs(connexions),
    )


def scenario_02_dix_concurrentes(pile: Pile) -> bool:
    """Dix navigateurs distincts, en même temps."""

    def une(numero: int) -> Connexion:
        navigateur = Navigateur(f"concurrent-{numero}")
        try:
            return navigateur.connecter(ADMIN_A, depart=pile.api)
        finally:
            navigateur.fermer()

    with ThreadPoolExecutor(max_workers=10) as executeur:
        connexions = list(executeur.map(une, range(10)))
    reussies = sum(1 for c in connexions if c.issue == "session")
    return conclure(
        "10 connexions concurrentes, navigateurs séparés",
        "10 sessions ouvertes",
        f"{reussies} sessions sur 10",
        reussies == 10,
        _echecs(connexions),
    )


def scenario_03_plusieurs_organisations(pile: Pile) -> bool:
    """Deux organisations, deux personnes : chacune arrive chez elle."""
    resultats = []
    for adresse in (ADMIN_A, ADMIN_B, ADMIN_A, ADMIN_B):
        navigateur = Navigateur("multi-organisation")
        try:
            resultats.append((adresse, navigateur.connecter(adresse, depart=pile.api)))
        finally:
            navigateur.fermer()
    egarees = [
        f"· {adresse} → {connexion.resume()} courriel={connexion.courriel}"
        for adresse, connexion in resultats
        if connexion.issue != "session" or connexion.courriel != adresse
    ]
    organisations = {c.organisation for _, c in resultats if c.organisation}
    return conclure(
        "plusieurs organisations et utilisateurs",
        "chaque personne ouvre une session dans SON organisation",
        f"{len(resultats) - len(egarees)} sur {len(resultats)} correctes, "
        f"{len(organisations)} organisations distinctes",
        not egarees and len(organisations) == 2,
        "\n".join(egarees),
    )


def scenario_04_redemarrage_avant_callback(pile: Pile) -> bool:
    """L'API tombe entre le départ et le retour : la transaction est en base."""
    navigateur = Navigateur("redémarrage-avant-callback")
    connexion = Connexion()
    try:
        url = navigateur.commencer(pile.api, connexion)
        assert url is not None
        code, etat = navigateur.sauthentifier(url, ADMIN_A, connexion)
        pile.redemarrer_api(0)
        assert code is not None and etat is not None
        code_connexion = navigateur.revenir(pile.api, code, etat, connexion)
        if code_connexion is not None:
            navigateur.echanger(pile.api, code_connexion, connexion)
    finally:
        navigateur.fermer()
    return conclure(
        "redémarrage de l'API après /start, avant le callback",
        "session ouverte — la transaction vit en base, pas en mémoire",
        connexion.resume(),
        connexion.issue == "session",
    )


def scenario_05_redemarrage_avant_echange(pile: Pile) -> bool:
    """L'API tombe entre le retour et l'échange : le code opaque survit."""
    navigateur = Navigateur("redémarrage-avant-échange")
    connexion = Connexion()
    try:
        url = navigateur.commencer(pile.api, connexion)
        assert url is not None
        code, etat = navigateur.sauthentifier(url, ADMIN_A, connexion)
        assert code is not None and etat is not None
        code_connexion = navigateur.revenir(pile.api, code, etat, connexion)
        pile.redemarrer_api(0)
        if code_connexion is not None:
            navigateur.echanger(pile.api, code_connexion, connexion)
            if connexion.issue == "session":
                navigateur.moi(pile.api, connexion)
    finally:
        navigateur.fermer()
    return conclure(
        "redémarrage de l'API après le callback, avant l'échange",
        "session ouverte — le code opaque est écrit avant d'être annoncé",
        connexion.resume(),
        connexion.issue == "session",
    )


def scenario_06_deux_instances(pile: Pile) -> bool:
    """Chaque étape sur une instance différente, sans affinité de session."""
    if len(pile.apis) < 2:
        return conclure(
            "deux instances d'API derrière le répartiteur",
            "chaque étape sur une instance différente",
            "banc monté avec une seule instance",
            False,
        )
    premiere, seconde = pile.apis[0], pile.apis[1]
    connexions = []
    for depart, retour, echange in (
        (premiere, seconde, premiere),
        (seconde, premiere, seconde),
        (premiere, premiere, seconde),
    ):
        navigateur = Navigateur("réparti")
        try:
            connexions.append(
                navigateur.connecter(ADMIN_A, depart=depart, retour=retour, echange=echange)
            )
        finally:
            navigateur.fermer()
    reussies = sum(1 for c in connexions if c.issue == "session")
    return conclure(
        "deux instances d'API derrière le répartiteur",
        "3 sessions ouvertes malgré la bascule d'instance à chaque étape",
        f"{reussies} sessions sur 3",
        reussies == 3,
        _echecs(connexions),
    )


def scenario_07_redemarrage_fournisseur(pile: Pile) -> bool:
    """Le fournisseur redémarre — nouvelles clés — entre deux connexions."""
    premier = Navigateur("avant-redémarrage")
    try:
        avant = premier.connecter(ADMIN_A, depart=pile.api)
    finally:
        premier.fermer()
    pile.redemarrer_fournisseur()
    second = Navigateur("après-redémarrage")
    try:
        apres = second.connecter(ADMIN_A, depart=pile.api)
    finally:
        second.fermer()
    return conclure(
        "redémarrage du fournisseur entre deux connexions",
        "les deux connexions aboutissent — les clés sont relues, pas mises en cache à vie",
        f"avant={avant.resume()} ; après={apres.resume()}",
        avant.issue == "session" and apres.issue == "session",
    )


def scenario_08_transaction_expiree(pile: Pile, url_base: str) -> bool:
    """Une demande de connexion périmée : refus net, puis on recommence."""
    navigateur = Navigateur("transaction-expirée")
    connexion = Connexion()
    try:
        url = navigateur.commencer(pile.api, connexion)
        assert url is not None
        code, etat = navigateur.sauthentifier(url, ADMIN_A, connexion)
        assert etat is not None
        _perimer_transaction(url_base, etat)
        assert code is not None
        navigateur.revenir(pile.api, code, etat, connexion)
        # Et surtout : on doit pouvoir recommencer, proprement.
        reprise = navigateur.connecter(ADMIN_A, depart=pile.api)
    finally:
        navigateur.fermer()
    return conclure(
        "transaction expirée",
        "refus contrôlé « expired_state », puis une nouvelle tentative aboutit",
        f"refus={connexion.motif} ; reprise={reprise.resume()}",
        connexion.issue == "refus"
        and connexion.motif == "expired_state"
        and reprise.issue == "session",
    )


def scenario_09_rejeu_du_code_opaque(pile: Pile) -> bool:
    """Le code opaque ne sert qu'une fois, et la session déjà ouverte tient."""
    navigateur = Navigateur("rejeu")
    connexion = Connexion()
    try:
        url = navigateur.commencer(pile.api, connexion)
        assert url is not None
        code, etat = navigateur.sauthentifier(url, ADMIN_A, connexion)
        assert code is not None and etat is not None
        code_connexion = navigateur.revenir(pile.api, code, etat, connexion)
        assert code_connexion is not None
        navigateur.echanger(pile.api, code_connexion, connexion)
        premier_jeton = connexion.jeton
        rejeu = Connexion()
        navigateur.echanger(pile.api, code_connexion, rejeu)
        # La session déjà ouverte n'est pas emportée par le refus du rejeu.
        toujours = Connexion(jeton=premier_jeton, issue="session")
        navigateur.moi(pile.api, toujours)
    finally:
        navigateur.fermer()
    return conclure(
        "rejeu du code opaque",
        "premier échange accepté, second refusé, session initiale intacte",
        f"premier={connexion.issue} ; rejeu={rejeu.motif} ; session={toujours.issue}",
        connexion.issue == "session"
        and rejeu.issue == "refus"
        and rejeu.motif == "401:invalid_login_code"
        and toujours.issue == "session",
    )


def scenario_10_navigation_interrompue(pile: Pile) -> bool:
    """On abandonne à mi-chemin, puis on recommence depuis le début."""
    navigateur = Navigateur("interrompue")
    abandon = Connexion()
    try:
        url = navigateur.commencer(pile.api, abandon)
        assert url is not None
        navigateur.sauthentifier(url, ADMIN_A, abandon)
        # Rien de plus : l'utilisateur ferme l'onglet, puis revient.
        reprise = navigateur.connecter(ADMIN_A, depart=pile.api)
    finally:
        navigateur.fermer()
    return conclure(
        "navigation interrompue puis recommencée",
        "la seconde tentative ouvre une session, sans que la première la gêne",
        reprise.resume(),
        reprise.issue == "session",
    )


def scenario_11_restes_d_une_session_precedente(pile: Pile) -> bool:
    """Le même navigateur, avec ses cookies et son stockage d'avant."""
    navigateur = Navigateur("session-précédente")
    try:
        premiere = navigateur.connecter(ADMIN_A, depart=pile.api)
        # On ne nettoie RIEN : cookies du fournisseur, jeton en stockage.
        seconde = navigateur.connecter(ADMIN_A, depart=pile.api)
        distincts = premiere.jeton != seconde.jeton
    finally:
        navigateur.fermer()
    # Observation, et non exigence : deux connexions du même utilisateur dans
    # la même seconde rendent le MÊME jeton, faute de `jti` dans la charge.
    # Sans conséquence sur la connexion — même sujet, même organisation, même
    # échéance — mais cela mérite d'être vu plutôt que découvert plus tard.
    note = "jeton identique au précédent" if not distincts else "jeton renouvelé"
    return conclure(
        "cookies et sessionStorage d'une session précédente",
        "une seconde session s'ouvre malgré les restes de la première",
        f"première={premiere.issue} ; seconde={seconde.resume()}",
        premiere.issue == "session" and seconde.issue == "session",
        f"observation : {note}",
    )


def scenario_12_deux_onglets(pile: Pile) -> bool:
    """Deux onglets commencent une connexion en même temps, puis terminent."""
    navigateur = Navigateur("deux-onglets")
    onglet_un, onglet_deux = Connexion(), Connexion()
    try:
        url_un = navigateur.commencer(pile.api, onglet_un)
        url_deux = navigateur.commencer(pile.api, onglet_deux)
        assert url_un is not None and url_deux is not None
        code_un, etat_un = navigateur.sauthentifier(url_un, ADMIN_A, onglet_un)
        code_deux, etat_deux = navigateur.sauthentifier(url_deux, ADMIN_A, onglet_deux)
        for code, etat, connexion in (
            (code_un, etat_un, onglet_un),
            (code_deux, etat_deux, onglet_deux),
        ):
            assert code is not None and etat is not None
            opaque = navigateur.revenir(pile.api, code, etat, connexion)
            if opaque is not None:
                navigateur.echanger(pile.api, opaque, connexion)
    finally:
        navigateur.fermer()
    return conclure(
        "deux onglets démarrent une connexion simultanément",
        "les deux aboutissent — deux transactions indépendantes",
        f"onglet 1={onglet_un.resume()} ; onglet 2={onglet_deux.resume()}",
        onglet_un.issue == "session" and onglet_deux.issue == "session",
    )


def scenario_13_callback_recu_deux_fois(pile: Pile) -> bool:
    """Le retour est rejoué — rechargement, bouton « précédent », doublon réseau."""
    navigateur = Navigateur("callback-double")
    connexion = Connexion()
    try:
        url = navigateur.commencer(pile.api, connexion)
        assert url is not None
        code, etat = navigateur.sauthentifier(url, ADMIN_A, connexion)
        assert code is not None and etat is not None
        premier_opaque = navigateur.revenir(pile.api, code, etat, connexion)
        second = Connexion()
        second_opaque = navigateur.revenir(pile.api, code, etat, second)
        assert premier_opaque is not None
        navigateur.echanger(pile.api, premier_opaque, connexion)
    finally:
        navigateur.fermer()
    return conclure(
        "callback reçu deux fois",
        "le second retour est refusé « invalid_state », le premier code reste échangeable",
        f"second retour={second.motif} ; échange du premier={connexion.issue}",
        second.issue == "refus"
        and second.motif == "invalid_state"
        and second_opaque is None
        and connexion.issue == "session",
    )


def scenario_14_decalage_horloge(url_base: str, racine: Path) -> bool:
    """Le fournisseur avance ou retarde, dans les limites du jeton.

    Un jeton d'identité vit cinq minutes. Une horloge décalée de moins que cela
    doit passer ; au-delà, le refus doit être NET et l'utilisateur doit pouvoir
    recommencer — jamais rester coincé sur l'écran de connexion.
    """
    resultats: list[str] = []
    reussi = True
    # ±45 s : nettement à l'intérieur de la tolérance de 60 s, sans jouer sur
    # la limite exacte — un banc qui teste une borne au centième mesure sa
    # propre horloge. −1 h : franchement dehors.
    for decalage, attendu in ((45, "session"), (-45, "session"), (-3600, "refus")):
        sous_racine = racine / f"horloge{decalage}"
        sous_racine.mkdir(parents=True, exist_ok=True)
        pile = Pile(sous_racine, url_base, 1)
        pile.decalage_fournisseur = decalage
        pile.demarrer()
        try:
            navigateur = Navigateur("horloge")
            try:
                connexion = navigateur.connecter(ADMIN_A, depart=pile.api)
            finally:
                navigateur.fermer()
        finally:
            pile.arreter()
        resultats.append(f"{decalage:+d} s → {connexion.resume()}")
        if connexion.issue != attendu:
            reussi = False
        if attendu == "refus" and connexion.motif != "token_expired":
            reussi = False
    return conclure(
        "léger décalage d'horloge",
        "±45 s : session ouverte ; −1 h : refus contrôlé « token_expired »",
        " ; ".join(resultats),
        reussi,
    )


def _perimer_transaction(url_base: str, etat: str) -> None:
    """Ramène l'expiration d'une transaction dans le passé, en base.

    C'est le seul geste du banc qui touche la base directement, et il est
    assumé : faire vraiment vieillir une transaction demanderait d'attendre dix
    minutes, ce qui ne prouverait rien de plus.
    """
    sys.path.insert(0, str(DOSSIER_API / "src"))
    from sqlalchemy import create_engine, text

    moteur = create_engine(url_base.replace("+psycopg", "+psycopg"), future=True)
    with moteur.begin() as connexion:
        connexion.execute(
            text("UPDATE login_transactions SET expires_at = :quand WHERE state = :etat"),
            {"quand": "2000-01-01 00:00:00", "etat": etat},
        )
    moteur.dispose()


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


@contextmanager
def base_jetable(url_admin: str) -> Iterator[str]:
    """Une base que ce banc CRÉE et détruit lui-même.

    Un nom rassurant n'est pas une preuve qu'une base est jetable. Plutôt que
    de faire confiance à celui qu'on nous passe, on en fabrique une, on
    l'utilise, et on ne détruit que celle-là. L'URL fournie ne sert qu'à
    joindre le serveur.
    """
    if not url_admin.startswith("postgresql"):
        yield url_admin
        return

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    nom = f"metreo_banc_oidc_{os.getpid()}_{secrets.token_hex(3)}"
    moteur = create_engine(url_admin, future=True, isolation_level="AUTOCOMMIT")
    with moteur.connect() as connexion:
        connexion.execute(text(f'CREATE DATABASE "{nom}"'))
    moteur.dispose()
    try:
        # `str(URL)` masque le mot de passe par des étoiles — c'est une bonne
        # habitude d'affichage, et une URL inutilisable. Mesuré : le banc
        # transmettait « ***  » à ses processus fils, qui échouaient sur une
        # authentification refusée sans que rien ne dise pourquoi.
        yield make_url(url_admin).set(database=nom).render_as_string(hide_password=False)
    finally:
        moteur = create_engine(url_admin, future=True, isolation_level="AUTOCOMMIT")
        with moteur.connect() as connexion:
            connexion.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :nom AND pid <> pg_backend_pid()"
                ),
                {"nom": nom},
            )
            connexion.execute(text(f'DROP DATABASE IF EXISTS "{nom}"'))
        moteur.dispose()


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--base",
        default="",
        help="URL SQLAlchemy d'une base JETABLE. Par défaut, un SQLite dans un dossier temporaire.",
    )
    parseur.add_argument("--instances", type=int, default=2)
    parseur.add_argument(
        "--garder", action="store_true", help="Ne pas effacer le dossier de travail"
    )
    arguments = parseur.parse_args(argv)

    racine = Path(tempfile.mkdtemp(prefix="banc-oidc-"))
    with base_jetable(arguments.base or f"sqlite+pysqlite:///{racine / 'banc.sqlite3'}") as base:
        return _jouer(base, arguments, racine)


def _jouer(url_base: str, arguments: argparse.Namespace, racine: Path) -> int:
    print(f"{GRAS}BANC CIBLÉ DU PARCOURS DE CONNEXION{FIN}")
    print(f"  base       {url_base.split('@')[-1]}")
    print(f"  instances  {arguments.instances}")
    print(f"  travail    {racine}")

    try:
        titre("Pile froide")
        with pile_montee(url_base, arguments.instances, racine, ORGANISATIONS) as pile:
            print(f"   fournisseur {pile.issuer}")
            for adresse in pile.apis:
                print(f"   api         {adresse}")

            titre("Volume et concurrence")
            scenario_01_cinquante_sequentielles(pile)
            scenario_02_dix_concurrentes(pile)
            scenario_03_plusieurs_organisations(pile)

            titre("Pannes au milieu du parcours")
            scenario_04_redemarrage_avant_callback(pile)
            scenario_05_redemarrage_avant_echange(pile)
            scenario_06_deux_instances(pile)
            scenario_07_redemarrage_fournisseur(pile)

            titre("Refus qui doivent rester des refus")
            scenario_08_transaction_expiree(pile, url_base)
            scenario_09_rejeu_du_code_opaque(pile)
            scenario_10_navigation_interrompue(pile)
            scenario_11_restes_d_une_session_precedente(pile)
            scenario_12_deux_onglets(pile)
            scenario_13_callback_recu_deux_fois(pile)

        titre("Horloges décalées")
        scenario_14_decalage_horloge(url_base, racine)
    finally:
        if not arguments.garder:
            shutil.rmtree(racine, ignore_errors=True)
        else:
            print(f"\n   dossier conservé : {racine}")

    titre("Verdict")
    echecs = [v for v in VERDICTS if not v.reussi]
    print(f"   {len(VERDICTS) - len(echecs)} scénarios conformes, {len(echecs)} en échec\n")
    if echecs:
        print(f"   {ROUGE}Échecs :{FIN}")
        for verdict in echecs:
            print(f"     · {verdict.scenario} — obtenu : {verdict.obtenu}")
        print(f"\n{ROUGE}BANC OIDC EN ÉCHEC{FIN}")
        return 1
    print(f"{VERT}BANC OIDC ENTIÈREMENT CONFORME{FIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
