"""Tool registry — single registration point for all agent tools.

Usage
-----
Registering a tool:
    @ToolRegistry.register
    class MyTool(BaseTool):
        name = "my_tool"
        ...

Using the registry:
    schemas  = ToolRegistry.schemas()          # → list[dict] for LLM tool_choice
    tool     = ToolRegistry.create("my_tool", db)  # → MyTool(db)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.tools.base import BaseTool


class ToolRegistry:
    _tools: dict[str, Type["BaseTool"]] = {}

    @classmethod
    def register(cls, tool_cls: Type["BaseTool"]) -> Type["BaseTool"]:
        """Decorator that registers a tool class by its ``name`` attribute."""
        assert tool_cls.name, f"{tool_cls.__name__} must define a non-empty 'name'"
        cls._tools[tool_cls.name] = tool_cls
        return tool_cls

    @classmethod
    def schemas(cls) -> list[dict]:
        """Return OpenAI function-call schemas for all registered tools."""
        return [t.openai_schema() for t in cls._tools.values()]

    @classmethod
    def create(cls, name: str, db: "Session") -> "BaseTool":
        """Instantiate a tool by name, injecting the DB session."""
        tool_cls = cls._tools.get(name)
        if tool_cls is None:
            raise KeyError(f"Tool {name!r} not registered. Known: {list(cls._tools)}")
        return tool_cls(db)

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._tools.keys())
