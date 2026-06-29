import os
from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////app/data/ship_log.db")

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def init_db():
    os.makedirs("/app/data", exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
