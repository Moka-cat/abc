"""app.tools — all agent / MCP tools, auto-registered on import.

Importing this package triggers @ToolRegistry.register on every tool class,
making them available via ToolRegistry.schemas() and ToolRegistry.create().

Tool groups
-----------
  retrieval_skills   — 9 知识库检索工具（search_memory / get_entity_card / …）
  experiment_skills  — 3 实验 Agent 工具（design_experiment / check_safety / …）
  chem_skills        — 化学计算工具（OB% / TMD / 燃速 / 爆速 / RDKit / …）
"""
from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry

# Import skill modules — each module registers its classes via @ToolRegistry.register
from app.tools import retrieval_skills    # noqa: F401
from app.tools import experiment_skills   # noqa: F401
from app.tools import chem_skills         # noqa: F401

# Re-export tool classes for convenience
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
from app.tools.chem_skills import (
    CalcOxygenBalanceTool,
    CalcDensityTool,
    CalcBurningRateTool,
    RDKitMolPropertiesTool,
    ValidateFormulationTool,
    ResolveCompoundTool,
    CalcDetonationParamsTool,
    FindSimilarCompoundsTool,
    CalcPropellantIspTool,
    CanteraCHNOEquilibriumTool,
    RDKit3DPropertiesTool,
    PymatgenCompositionAnalysisTool,
)

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolRegistry",
    # Retrieval skills
    "SearchMemoryTool",
    "GetEntityCardTool",
    "TraceEvidenceTool",
    "FindTablesTool",
    "CompareValuesTool",
    "GetSourceSnippetTool",
    "FindRelatedEntitiesTool",
    "GetFormulationTool",
    "AgenticSearchTool",
    # Experiment skills
    "DesignExperimentTool",
    "CheckSafetyTool",
    "LogExperimentResultTool",
    # Chemistry skills
    "CalcOxygenBalanceTool",
    "CalcDensityTool",
    "CalcBurningRateTool",
    "RDKitMolPropertiesTool",
    "ValidateFormulationTool",
    "ResolveCompoundTool",
    "CalcDetonationParamsTool",
    "FindSimilarCompoundsTool",
    "CalcPropellantIspTool",
    "CanteraCHNOEquilibriumTool",
    "RDKit3DPropertiesTool",
    "PymatgenCompositionAnalysisTool",
]
