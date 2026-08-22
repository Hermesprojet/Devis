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
