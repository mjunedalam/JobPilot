from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from jobpilot.core.config import get_settings
from jobpilot.core.db import make_engine
from jobpilot.core.models import Base

target_metadata = Base.metadata


def _url() -> str:
    return (
        context.config.get_main_option("sqlalchemy.url") or get_settings().resolved_database_url()
    )


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        compare_type=True,
    )


if context.is_offline_mode():
    _configure(url=_url())
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = context.config.attributes.get("connection")
    if connectable is not None:
        _configure(connection=connectable)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with make_engine(_url()).connect() as connection:
            _configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
