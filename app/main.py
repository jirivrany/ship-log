import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import voyages, legs, log_entries


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.environ.get("UPLOAD_DIR", "/app/data/uploads"), exist_ok=True)
    init_db()
    yield


app = FastAPI(title="Ship Log", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(voyages.router)
app.include_router(legs.router)
app.include_router(log_entries.router)
