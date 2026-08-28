"""Alembic environment.

The database URL always comes from the application settings, never from
``alembic.ini``: there is one source of truth for where the data lives.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from metreo_api.config import get_settings
from metreo_api.db import ensure_sqlite_directory
from metreo_api.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL = get_settings().database_url
# Même geste que l'application : sur un clone neuf, le répertoire de la
# base SQLite par défaut n'existe pas, et « upgrade head » échouait sur un
# message qui ne nomme ni le chemin ni ce qui manque.
ensure_sqlite_directory(DATABASE_URL)
config.set_main_option("sqlalchemy.url", DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Batch mode keeps future ALTERs working on SQLite as well.
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
