"""Configuration Alembic du projet bot-td.

L'URL de connexion vient de la configuration applicative (DATABASE_URL du
.env via app.config.settings), jamais en dur dans alembic.ini. Le driver
psycopg 3 fonctionne aussi en mode synchrone pour les migrations.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from app.config.settings import get_settings
from app.database.models import Base

# Objet de configuration Alembic (valeurs du .ini).
config = context.config

# Logging selon alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata des modèles pour le support autogenerate.
target_metadata = Base.metadata

# URL de connexion depuis la configuration applicative.
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """Mode 'offline' : émet le SQL sans se connecter."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Mode 'online' : se connecte et applique les migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
