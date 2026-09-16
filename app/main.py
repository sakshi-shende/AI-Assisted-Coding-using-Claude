"""FastAPI application entrypoint for ExpenseFlow."""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI

from app.db import init_db
from app.routes import router

load_dotenv()

app = FastAPI(title="ExpenseFlow")
app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    """Create database tables if they don't already exist."""
    init_db()
