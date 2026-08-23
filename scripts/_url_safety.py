"""Ce que le pilote ouvrira réellement — pas ce que l'URL prétend nommer.

Deux scripts détruisent ce qu'ils touchent : le contrôle de base jetable, qui
autorise une suite créant et supprimant un schéma par test, et l'aller-retour
des migrations, qui applique `downgrade base`. Tous deux doivent savoir quelle
base sera **ouverte**, et non quelle base le chemin de l'URL désigne.

Le dialecte psycopg fusionne la chaîne de requête dans ses arguments de
connexion : `…/metreo_gate?dbname=metreo` nomme `metreo_gate` dans son chemin
et ouvre `metreo`. Ce défaut a été reproduit deux fois, avec destruction
réelle : une base témoin est passée de deux organisations à zéro pendant que
le nom rassurant s'affichait à l'écran.

Une seule liste, ici : deux listes divergent. Celle du contrôle de nom avait
`dbname` mais pas `database`, et l'aller-retour n'en avait aucune.

Et un seul constructeur d'URL cible, pour la même raison. Le défaut a été fermé
dans l'aller-retour, puis rouvert quelques commits plus loin par le helper
témoin des tests, qui avait réécrit le sien avec `set(database=…)` — il créait
sa base aléatoire et écrivait ses sentinelles dans la base redirigée. Ce module
est donc importé aussi bien par `scripts/` que par `apps/api/tests/`.
"""

from __future__ import annotations

from sqlalchemy.engine import URL, make_url

#: Paramètres de requête qui déplacent la connexion ailleurs que là où le
#: chemin de l'URL le dit. libpq les lit, SQLAlchemy les transmet, et le chemin
#: nomme alors une base que personne n'ouvre. Aucun n'a de raison d'être dans
#: l'URL d'une commande destructrice : ils sont refusés, pas suivis.
REDIRECTING_PARAMETERS = frozenset(
    {"dbname", "database", "host", "hostaddr", "port", "user", "service", "passfile"}
)


class UnknownDialect(Exception):
    """Le pilote ne se charge pas : on ne peut rien dire de la cible."""


class UnsafeUrl(RuntimeError):
    """L'URL ne désigne pas de façon univoque la base qui sera ouverte."""


def redirecting_parameters(url: str | URL) -> list[str]:
    """Les paramètres redirecteurs portés par cette URL, triés."""
    parsed = make_url(url) if isinstance(url, str) else url
    return sorted(set(parsed.query) & REDIRECTING_PARAMETERS)


def effective_database(url: str | URL) -> str:
    """La base que le pilote ouvrira, demandée au dialecte lui-même.

    Lire `urlsplit(url).path` revient à lire le mauvais composant. On interroge
    donc `create_connect_args()`, qui est ce que SQLAlchemy passera au pilote.
    Quand on ne peut pas l'interroger — pilote inconnu — l'appelant est
    prévenu, pas servi d'une supposition.
    """
    parsed = make_url(url) if isinstance(url, str) else url
    if parsed.get_backend_name() == "sqlite":
        # Une URL SQLite nomme un fichier ; sa dernière composante est la base.
        return (parsed.database or "").rsplit("/", 1)[-1]
    arguments = parsed.get_dialect()().create_connect_args(parsed)[1]
    return str(arguments.get("dbname") or arguments.get("database") or parsed.database or "")


def refuse_redirection(url: str | URL, *, doing: str) -> None:
    """Refuser, **avant toute connexion**, une URL qui peut se déplacer ailleurs.

    Premier des deux gardes. Il ne répare rien et ne devine rien : une URL
    d'administration portant `dbname`, `host` ou `service` n'est pas exploitable
    par un appelant qui va créer puis détruire, parce que le nom qu'il lit n'est
    pas celui qu'il ouvrira. ``doing`` nomme la conséquence dans le message, pour
    que le refus se lise sans traceback.
    """
    redirecting = redirecting_parameters(url)
    if redirecting:
        raise UnsafeUrl(
            f"l'URL porte {redirecting} dans sa chaîne de requête, ce qui déplace la "
            f"connexion ailleurs que là où son chemin le dit : {doing}"
        )


def safe_target_url(admin: str | URL, name: str) -> str:
    """L'URL de ``name`` sur ce serveur, débarrassée de toute redirection.

    Second des deux gardes, et le seul constructeur d'URL cible du dépôt.
    `parsed.set(database=name)` ne suffit pas : il change le CHEMIN et conserve
    la chaîne de requête, à laquelle libpq obéit. Reproduit deux fois avec
    conséquence réelle — d'abord dans l'aller-retour des migrations, où
    `…/postgres?dbname=metreo_victim_a` a fait tourner `downgrade base` sur la
    victime, puis dans le helper témoin des tests, qui créait bien une base
    aléatoire mais écrivait ses sentinelles dans la base redirigée, et les
    relisait au même endroit — donc passait au vert en ayant modifié une base
    étrangère.

    Deux implémentations divergeraient, comme les deux listes de paramètres
    l'avaient déjà fait : il n'y en a qu'une, ici, et les deux appelants
    l'utilisent.

    On retire les paramètres redirecteurs, puis on demande au dialecte ce qu'il
    ouvrira réellement — la vérification ne fait pas confiance au retrait.
    """
    parsed = make_url(admin) if isinstance(admin, str) else admin
    candidate = parsed.set(database=name).difference_update_query(sorted(REDIRECTING_PARAMETERS))
    opened = effective_database(candidate)
    if opened != name:
        raise UnsafeUrl(
            f"l'URL construite ouvrirait « {opened} » et non « {name} » : refus, "
            "aucune écriture ni destruction ne doit toucher une autre base"
        )
    # `str()` sur une URL SQLAlchemy remplace le mot de passe par « *** » :
    # l'URL rendue serait inutilisable, avec une erreur d'authentification pour
    # tout diagnostic.
    return candidate.render_as_string(hide_password=False)
