"""
Alembic environment for the ClearClaim tables (cc_*) only.

The legacy rule-engine tables are created by app/database/seed.py and are
excluded here (include_object), so autogenerate never proposes dropping them.
The database URL comes from DATABASE_URL (same variable the app uses).
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clearclaim.config import settings  # noqa: E402
from app.clearclaim.models import CCBase  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = CCBase.metadata


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name.startswith("cc_")
    return True


def run_migrations_offline():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
