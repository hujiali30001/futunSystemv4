from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    pool_kwargs = {}
    if "postgresql" in database_url:
        connect_args["connect_timeout"] = 5
        pool_kwargs = {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "pool_timeout": 5,
        }
    return create_engine(database_url, future=True, connect_args=connect_args, **pool_kwargs)


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = build_engine(database_url)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
