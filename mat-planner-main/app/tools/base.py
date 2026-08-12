"""Tool base classes — single source of truth for name, description, and OpenAI schema."""
from __future__ import annotations

from typing import Any, ClassVar
from dataclasses import dataclass


@dataclass
class ToolResult:
    success: bool
    data: Any
    error: str | None = None


class BaseTool:
    """Base class for all agent / MCP tools.

    Subclasses must define:
      - name        : str  — matches the LLM function-call name
      - description : str  — shown to both the LLM and MCP clients
      - parameters  : dict — OpenAI-compatible parameter properties dict
      - required    : list — list of required parameter names
      - run(**kwargs) → ToolResult

    The ``openai_schema`` classmethod auto-generates the full OpenAI function
    schema from these attributes, so schemas never diverge from implementations.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict] = {}   # OpenAI parameter "properties" dict
    required: ClassVar[list[str]] = []

    @classmethod
    def openai_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": {
                    "type": "object",
                    "properties": cls.parameters,
                    "required": cls.required,
                },
            },
        }

    def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def __call__(self, **kwargs) -> ToolResult:
        return self.run(**kwargs)
