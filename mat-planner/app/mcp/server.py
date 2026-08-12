"""FastMCP server — exposes all registered tools over HTTP.

Each MCP tool keeps an explicit Python signature (required for FastMCP to
generate the JSON schema that Claude Desktop reads), but shares a single
``_db_run`` helper that eliminates the SessionLocal / try-finally boilerplate.

Adding a new tool:
  1. Add a class to the appropriate skill group file:
       retrieval_skills.py  — knowledge-base retrieval tools
       experiment_skills.py — experiment agent tools (design / safety / analysis)
       chem_skills.py       — standalone chemistry calculation tools
  2. Add one @mcp.tool() function here that delegates to _db_run(MyTool, ...)
"""
import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

import app.tools  # noqa: F401 — triggers @ToolRegistry.register for all skill groups
from app.tools.retrieval_skills import (
    SearchMemoryTool,
    GetEntityCardTool,
    TraceEvidenceTool,
    FindTablesTool,
    CompareValuesTool,
    GetSourceSnippetTool,
    FindRelatedEntitiesTool,
    GetFormulationTool,
    AgenticSearchTool,
)
from app.tools.experiment_skills import (
    DesignExperimentTool,
    CheckSafetyTool,
    LogExperimentResultTool,
)
from app.core.database import SessionLocal


def _db_run(tool_cls, **kwargs) -> dict:
    """Run a tool synchronously with its own DB session.

    Creates a fresh SessionLocal, calls tool_cls(db).run(**kwargs),
    and always closes the session. Raises RuntimeError on tool failure.
    """
    db = SessionLocal()
    try:
        result = tool_cls(db).run(**kwargs)
        if not result.success:
            raise RuntimeError(result.error)
        return result.data
    finally:
        db.close()


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        "mat-planner-memory",
        stateless_http=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    async def search_memory(query: str, namespace: str = "default", top_k: int = 10) -> dict:
        """Search the energetic materials knowledge base (hybrid keyword + vector retrieval).
        Use as first tool for any question about properties, synthesis, or performance."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(SearchMemoryTool, query=query, namespace=namespace, top_k=top_k)
        )

    @mcp.tool()
    async def get_entity_card(name: str) -> dict:
        """Retrieve a full property profile for a named energetic material or ingredient
        (density, detonation velocity, burning rate, aliases, evidence sources, etc.)."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(GetEntityCardTool, name=name)
        )

    @mcp.tool()
    async def trace_evidence(evidence_id: str) -> dict:
        """Trace a property value back to its exact source quote, section, and page number."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(TraceEvidenceTool, evidence_id=evidence_id)
        )

    @mcp.tool()
    async def find_tables(query: str, namespace: str = "default", top_k: int = 5) -> dict:
        """Find data tables in documents matching a keyword query (content + caption search)."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(FindTablesTool, query=query, namespace=namespace, top_k=top_k)
        )

    @mcp.tool()
    async def compare_values(
        property_name: str,
        entity_names: list[str] = [],
        namespace: str = "default",
    ) -> dict:
        """Compare a numeric property across entities and return ranked statistics.
        property_name: density | detonation_velocity | burning_rate | detonation_pressure | …"""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(CompareValuesTool, property_name=property_name,
                            entity_names=entity_names or None, namespace=namespace)
        )

    @mcp.tool()
    async def get_source_snippet(chunk_id: str) -> dict:
        """Get the full original text of a document chunk by its UUID."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(GetSourceSnippetTool, chunk_id=chunk_id)
        )

    @mcp.tool()
    async def find_related_entities(
        entity_name: str,
        relation: str = "",
        limit: int = 10,
    ) -> dict:
        """Find entities related via the knowledge graph.
        relation: CO_OCCURS_WITH | COMPARED_BY_density | COMPARED_BY_detonation_velocity | …"""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(FindRelatedEntitiesTool, entity_name=entity_name,
                            relation=relation or None, limit=limit)
        )

    @mcp.tool()
    async def get_formulation(entity_name: str, top_k: int = 10) -> dict:
        """Find composite propellant / explosive formulations that contain a given ingredient."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(GetFormulationTool, entity_name=entity_name, top_k=top_k)
        )

    @mcp.tool()
    async def agentic_search(query: str, namespace: str = "default") -> dict:
        """Deep navigational search: LLM reads section outlines to navigate the document tree
        before fetching content. Use for complex, multi-section questions where flat retrieval
        returns fragmented evidence."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(AgenticSearchTool, query=query, namespace=namespace)
        )

    @mcp.tool()
    async def design_experiment(
        goal: str,
        namespace: str = "default",
        num_candidates: int = 3,
    ) -> dict:
        """Design a complete experimental protocol for energetic materials research.
        Given a natural language goal, retrieves relevant literature, recommends formulations,
        predicts properties, generates step-by-step protocol, and runs safety checks.
        Returns 1-3 ranked candidate protocols."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(DesignExperimentTool, goal=goal,
                            namespace=namespace, num_candidates=num_candidates)
        )

    @mcp.tool()
    async def check_safety(
        formulation: dict,
        steps: list = [],
        namespace: str = "default",
    ) -> dict:
        """Run a safety check on a proposed energetic material formulation.
        Checks component compatibility, sensitivity, temperature limits, and hazard level.
        Returns approved / needs_review / blocked with specific issues and recommendations."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(CheckSafetyTool, formulation=formulation,
                            steps=steps or [], namespace=namespace)
        )

    @mcp.tool()
    async def log_experiment_result(
        experiment_id: str,
        measured_properties: dict,
        protocol_id: str = "",
        raw_data_notes: str = "",
        write_to_kb: bool = True,
    ) -> dict:
        """Upload measured experimental results. Compares against predictions, generates
        an analysis report with deviation analysis and next-step recommendations,
        and writes verified data back to the knowledge base."""
        return await anyio.to_thread.run_sync(
            lambda: _db_run(
                LogExperimentResultTool,
                experiment_id=experiment_id,
                measured_properties=measured_properties,
                protocol_id=protocol_id or None,
                raw_data_notes=raw_data_notes,
                write_to_kb=write_to_kb,
            )
        )

    return mcp
