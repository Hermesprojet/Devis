"""La garde ASGI qui borne un corps AVANT que FastAPI ne le lise.

Elle vit à l'extérieur de l'application, et c'est tout son intérêt : un
intergiciel FastAPI ordinaire s'exécute déjà à l'intérieur du traitement de la
requête, mais le corps multipart, lui, n'est lu qu'au moment de résoudre les
paramètres — après les dépendances, donc après l'authentification. Une garde
posée là arriverait encore trop tard.

Deux chemins, selon ce que le client annonce.

**Avec un `Content-Length` crédible.** Le refus est immédiat : aucun appel à
l'application, aucune dépendance ouverte, aucun octet du corps lu. C'est le cas
courant, et le moins cher.

**Sans `Content-Length`, ou avec un menteur.** `receive` est enveloppé et les
octets sont comptés MORCEAU PAR MORCEAU. Le corps n'est jamais reconstitué pour
être mesuré — le mesurer ainsi coûterait exactement la mémoire que la borne
existe pour refuser. Dès le dépassement, plus rien ne passe à l'application.

C'est la deuxième voie qui fait autorité : un en-tête est une déclaration du
client, les octets observés sont un fait. Un `Content-Length` qui annonce
moins que ce qui arrive ne gagne donc rien.
"""

from __future__ import annotations

from typing import Any

#: Le corps de la réponse de refus, en JSON, écrit une fois.
#:
#: Ni chemin interne, ni configuration complète, ni le moindre écho du contenu
#: reçu : un refus est un refus, pas une page de diagnostic. Seul le plafond
#: est rendu, parce que c'est ce qu'il faut savoir pour réessayer utilement.
CODE_DE_REFUS = "request_too_large"
MESSAGE_DE_REFUS = "Le fichier dépasse la taille autorisée."


def _corps_du_refus(plafond: int) -> bytes:
    import json

    return json.dumps(
        {
            "detail": {
                "code": CODE_DE_REFUS,
                "message": MESSAGE_DE_REFUS,
                "max_bytes": plafond,
            }
        }
    ).encode()


def _longueur_annoncee(entetes: list[tuple[bytes, bytes]]) -> int | None:
    """La taille annoncée, ou `None` si elle est absente ou inutilisable.

    Trois cas rendent `None` plutôt qu'un refus, et le comptage réel tranchera :

    * **absente** — c'est le cas normal d'un envoi en `chunked` ;
    * **non numérique ou négative** — une valeur qu'on ne sait pas lire ne
      permet aucune conclusion, ni dans un sens ni dans l'autre ;
    * **contradictoire** — plusieurs `Content-Length` différents. Refuser sur
      la plus grande punirait un client dont le corps est peut-être minuscule ;
      accepter sur la plus petite serait exactement le mensonge qu'on veut
      déjouer. On ne croit donc ni l'une ni l'autre.

    Plusieurs valeurs IDENTIQUES sont acceptées : elles ne se contredisent pas.
    """
    valeurs = [valeur for nom, valeur in entetes if nom.lower() == b"content-length"]
    if not valeurs:
        return None
    if len({valeur.strip() for valeur in valeurs}) > 1:
        return None
    brute = valeurs[0].strip()
    if not brute.isdigit():  # signe, espaces, lettres, vide
        return None
    return int(brute)


class GardeDeCorps:
    """Enveloppe l'application ASGI et borne le corps de chaque requête.

    `plafonds` associe (méthode, chemin de route) à un nombre d'octets. Le
    chemin est celui du ROUTAGE — avec ses accolades — et la correspondance se
    fait sur le chemin réel de la requête : à ce niveau, aucun routeur n'a
    encore tourné, et il faut donc comparer nous-mêmes.
    """

    def __init__(self, app: Any, plafonds: dict[tuple[str, str], int]) -> None:
        self.app = app
        self._plafonds = plafonds
        # Les segments variables sont remplacés par un joker à la comparaison.
        self._motifs = [
            (methode, tuple(chemin.strip("/").split("/")), plafond)
            for (methode, chemin), plafond in plafonds.items()
        ]

    def plafond_pour(self, methode: str, chemin: str) -> int | None:
        """Le plafond de cette requête, ou `None` si la route n'en a pas.

        La comparaison est segment à segment : un segment de motif entouré
        d'accolades accepte n'importe quelle valeur, les autres doivent
        coïncider exactement. C'est assez pour les routes de dépôt, et cela
        évite d'embarquer un moteur d'expressions dont le coût se paierait à
        chaque requête, y compris celles qui ne portent aucun corps.
        """
        segments = tuple(chemin.strip("/").split("/"))
        for motif_methode, motif, plafond in self._motifs:
            if motif_methode != methode or len(motif) != len(segments):
                continue
            if all(
                attendu.startswith("{") or attendu == recu
                for attendu, recu in zip(motif, segments, strict=True)
            ):
                return plafond
        return None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        plafond = self.plafond_pour(scope.get("method", ""), scope.get("path", ""))
        if plafond is None:
            await self.app(scope, receive, send)
            return

        annoncee = _longueur_annoncee(scope.get("headers") or [])
        if annoncee is not None and annoncee > plafond:
            # Le chemin le moins cher : l'application n'est jamais appelée, donc
            # aucune dépendance n'est ouverte, aucune session de base n'est
            # prise, et pas un octet du corps n'est lu.
            await _refuser(send, plafond)
            return

        await self.app(scope, _receive_borne(receive, plafond, send), send)


class _CorpsTropGrand(Exception):
    """Levée dans `receive` quand le comptage dépasse le plafond.

    Une exception plutôt qu'un retour silencieux : rendre un corps tronqué à
    l'application lui ferait analyser un multipart coupé au milieu, et elle
    répondrait « fichier illisible » là où le motif est « trop volumineux ».
    """


def _receive_borne(receive: Any, plafond: int, send: Any) -> Any:
    """Un `receive` qui compte, et qui cesse de transmettre au dépassement.

    Le compteur porte sur les octets RÉELLEMENT arrivés, morceau par morceau.
    C'est ce qui rend l'en-tête sans pouvoir : qu'il annonce moins, qu'il
    annonce mal ou qu'il n'annonce rien, la borne est la même.

    Un corps découpé en des centaines de petits morceaux est traité comme un
    seul : c'est un total qui est comparé, pas une taille de morceau.
    """
    recus = 0
    epuise = False

    async def borne() -> dict[str, Any]:
        nonlocal recus, epuise
        if epuise:
            # Après le refus, l'application ne doit plus rien recevoir. On lui
            # rend une déconnexion : c'est le seul message qu'un cadre ASGI
            # traite comme « il n'y aura plus rien », sans lui faire croire à
            # un corps complet qu'elle analyserait.
            return {"type": "http.disconnect"}

        message = await receive()
        if message.get("type") != "http.request":
            return message

        recus += len(message.get("body") or b"")
        if recus > plafond:
            epuise = True
            raise _CorpsTropGrand(plafond)
        return message

    return borne


async def _refuser(send: Any, plafond: int) -> None:
    corps = _corps_du_refus(plafond)
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(corps)).encode()),
                # Le client peut avoir encore des octets à envoyer ; fermer la
                # connexion évite de les absorber pour rien.
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": corps})


def poser_la_garde(app: Any, plafonds: dict[tuple[str, str], int]) -> Any:
    """Enveloppe `app`, et transforme le dépassement compté en réponse 413.

    Le second étage est nécessaire parce que `_CorpsTropGrand` est levée DANS
    `receive`, donc au milieu du traitement de l'application : sans lui, elle
    remonterait en erreur serveur.
    """
    garde = GardeDeCorps(app, plafonds)

    async def enveloppe(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await garde(scope, receive, send)
            return

        commencee = False

        async def send_surveille(message: dict[str, Any]) -> None:
            nonlocal commencee
            if message.get("type") == "http.response.start":
                commencee = True
            await send(message)

        try:
            await garde(scope, receive, send_surveille)
        except _CorpsTropGrand as trop_grand:
            if commencee:
                # La réponse est déjà partie : on ne peut plus la remplacer, et
                # écrire un second en-tête casserait le protocole. Le cas est
                # improbable — l'application aurait répondu avant d'avoir fini
                # de lire — mais le silence vaut mieux qu'une trame invalide.
                raise
            await _refuser(send, trop_grand.args[0])

    return enveloppe
