from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from campaignpilot.config import get_settings
from campaignpilot.db.models import Base


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def get_session() -> Session:
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
