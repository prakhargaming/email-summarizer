from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Supabase's transaction pooler breaks server-side prepared statements.
engine = create_engine(
    settings.database_url, connect_args={"prepare_threshold": None}, pool_pre_ping=True
)
SessionLocal = sessionmaker(engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
