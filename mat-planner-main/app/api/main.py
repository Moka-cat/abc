from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.core.config import settings
from app.core.database import create_all_tables
from app.api.routes import health, jobs, documents, retrieval, entities, graph
from app.api.routes import chat
from app.api.routes import experiments
from app.mcp.server import create_mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting mat-planner API...")
    create_all_tables()
    logger.info("Database tables initialized.")
    yield
    logger.info("Shutting down mat-planner API.")


app = FastAPI(
    title="mat-planner",
    description="Memory + MCP + Planning Agent for propellant and energetic material literature",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"],
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
app.include_router(entities.router, prefix="/entities", tags=["entities"])
app.include_router(graph.router, prefix="/graph", tags=["graph"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(experiments.router, prefix="/experiments", tags=["experiments"])

# Mount the FastMCP server at /mcp
mcp_server = create_mcp_server()
app.mount("/mcp", mcp_server.streamable_http_app())
