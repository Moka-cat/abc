#!/usr/bin/env python
"""初始化数据库，创建所有表"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import create_all_tables
import app.models.orm  # noqa: F401 — ensure all models are imported

if __name__ == "__main__":
    create_all_tables()
    print("Database tables created successfully.")
