from __future__ import annotations

from fastapi import FastAPI

from app.api import health, workflows

app = FastAPI(title="Energetic Platform")

app.include_router(health.router)
app.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
