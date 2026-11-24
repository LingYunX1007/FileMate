import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from services.filemate.config import postgres_url
from services.filemate.db import Base


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


provided = context.config.attributes.get("connection")
if provided is not None:
    migrate(provided)
elif context.is_offline_mode():
    context.configure(url=postgres_url(os.environ["FILEMATE_DATABASE_URL"]), target_metadata=Base.metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(postgres_url(os.environ["FILEMATE_DATABASE_URL"]), poolclass=NullPool)
    with engine.connect() as active:
        migrate(active)
    engine.dispose()
