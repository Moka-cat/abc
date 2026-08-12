from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Settings(BaseModel):
    cluster_config: Path = Path(os.getenv("CLUSTER_CONFIG", "/app/config/cluster.yaml"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_cluster_config() -> dict[str, Any]:
    config_path = get_settings().cluster_config
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)
