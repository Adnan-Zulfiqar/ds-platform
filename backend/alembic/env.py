"""Alembic migration environment.

Runs synchronously against ``sync_dsn`` even though the application is async.
Migrations are a serial administrative operation with no concurrency to gain
from, and the sync driver keeps this script simple.

The database URL comes from application settings rather than ``alembic.ini`` so
that migrations and the running application can never disagree about which
database they are pointed at.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.core.config import settings
from app.models import *  # noqa: F403

# Importing the models package registers every table on Base.metadata.
# Without this import autogenerate produces an empty migration.
from app.models import Base
from sqlalchemy import engine_from_config, pool

config = context.config
config.set_main_option("sqlalchemy.url", settings.database.sync_dsn)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Filter objects out of autogenerate.

    Postgres extensions and anything in a non-public schema are managed
    separately from application migrations.
    """
    if type_ == "table" and getattr(obj, "schema", None) not in (None, "public"):
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    Used to hand a reviewable SQL script to a DBA when production access is
    gated, rather than running DDL directly.
    """
    context.configure(
        url=settings.database.sync_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type and server-default changes. Off by default in
            # Alembic, which means a widened varchar silently produces no
            # migration.
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            # Wrap DDL in a transaction. Postgres supports transactional DDL, so
            # a failed migration rolls back cleanly instead of leaving the schema
            # half-applied.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
