#!/usr/bin/env python
"""Migration: populate MemoryGraphNode / MemoryGraphEdge from existing data.

Run once after upgrading the code:
    uv run python scripts/migrate_build_graph.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from app.core.database import SessionLocal
from app.pipeline.graph_builder import build_graph


def main() -> None:
    db = SessionLocal()
    try:
        stats = build_graph(db)
        logger.info(
            f"Done — {stats['nodes']} entity nodes, {stats['edges']} edges created"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
