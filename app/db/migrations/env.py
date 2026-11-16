import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core import ssm

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

# Read DATABASE_URL directly so migrations don't need the app's other secrets.
ssm.load_into_env()
url = os.environ["DATABASE_URL"]

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, connect_args={"prepare_threshold": None}).connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()
