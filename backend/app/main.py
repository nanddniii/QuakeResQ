"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import SessionLocal, initialize_database
from app.dependencies import get_closure_service, init_graph_service, replay_active_closures
from app.routes import api_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    # Safe startup: initializes graph singleton without auto-downloading over the network
    gs = init_graph_service(auto_load_cache=True)
    # Idempotent replay: synchronizes active closures from SQLite into in-memory working graph
    with SessionLocal() as session:
        cs = get_closure_service(gs)
        replay_active_closures(session, cs)
    yield


app = FastAPI(
    title="QuakeResQ API",
    description="Starter API for earthquake rescue planning.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
