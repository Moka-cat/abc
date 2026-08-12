"""Add experiment tables: experiments, experiment_protocols, experiment_results.

Run once after upgrading to the version that adds ExperimentDesignService:

    uv run python scripts/migrate_add_experiments.py

Safe to re-run: uses IF NOT EXISTS / PRAGMA checks.
"""
import sys
sys.path.insert(0, ".")

from loguru import logger
from sqlalchemy import text
from app.core.database import engine


_SQLITE_DDL = [
    # experiments
    """
    CREATE TABLE IF NOT EXISTS experiments (
        id          TEXT PRIMARY KEY,
        namespace   TEXT NOT NULL DEFAULT 'default',
        goal        TEXT NOT NULL,
        target_properties_json TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_experiments_namespace ON experiments(namespace)",
    "CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)",

    # experiment_protocols
    """
    CREATE TABLE IF NOT EXISTS experiment_protocols (
        id              TEXT PRIMARY KEY,
        experiment_id   TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
        rank            INTEGER NOT NULL DEFAULT 0,
        formulation_json            TEXT,
        steps_json                  TEXT,
        predicted_properties_json   TEXT,
        reference_chunk_ids_json    TEXT,
        rationale                   TEXT,
        safety_status               TEXT NOT NULL DEFAULT 'pending',
        safety_report_json          TEXT,
        required_instruments_json   TEXT,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_proto_experiment ON experiment_protocols(experiment_id)",

    # experiment_results
    """
    CREATE TABLE IF NOT EXISTS experiment_results (
        id              TEXT PRIMARY KEY,
        experiment_id   TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
        protocol_id     TEXT REFERENCES experiment_protocols(id) ON DELETE SET NULL,
        measured_properties_json    TEXT,
        raw_data_notes              TEXT,
        analysis_report             TEXT,
        deviation_json              TEXT,
        written_to_kb               INTEGER NOT NULL DEFAULT 0,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_result_experiment ON experiment_results(experiment_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_result_protocol ON experiment_results(protocol_id)",
]

_POSTGRES_DDL = [
    """
    CREATE TABLE IF NOT EXISTS experiments (
        id          VARCHAR(36) PRIMARY KEY,
        namespace   VARCHAR(128) NOT NULL DEFAULT 'default',
        goal        TEXT NOT NULL,
        target_properties_json TEXT,
        status      VARCHAR(32) NOT NULL DEFAULT 'pending',
        error_message TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_experiments_namespace ON experiments(namespace)",
    "CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)",

    """
    CREATE TABLE IF NOT EXISTS experiment_protocols (
        id              VARCHAR(36) PRIMARY KEY,
        experiment_id   VARCHAR(36) NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
        rank            INTEGER NOT NULL DEFAULT 0,
        formulation_json            TEXT,
        steps_json                  TEXT,
        predicted_properties_json   TEXT,
        reference_chunk_ids_json    TEXT,
        rationale                   TEXT,
        safety_status               VARCHAR(32) NOT NULL DEFAULT 'pending',
        safety_report_json          TEXT,
        required_instruments_json   TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_proto_experiment ON experiment_protocols(experiment_id)",

    """
    CREATE TABLE IF NOT EXISTS experiment_results (
        id              VARCHAR(36) PRIMARY KEY,
        experiment_id   VARCHAR(36) NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
        protocol_id     VARCHAR(36) REFERENCES experiment_protocols(id) ON DELETE SET NULL,
        measured_properties_json    TEXT,
        raw_data_notes              TEXT,
        analysis_report             TEXT,
        deviation_json              TEXT,
        written_to_kb               BOOLEAN NOT NULL DEFAULT FALSE,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_result_experiment ON experiment_results(experiment_id)",
    "CREATE INDEX IF NOT EXISTS idx_exp_result_protocol ON experiment_results(protocol_id)",
]


def main() -> None:
    ddl_list = _SQLITE_DDL if engine.dialect.name == "sqlite" else _POSTGRES_DDL
    with engine.connect() as conn:
        for ddl in ddl_list:
            conn.execute(text(ddl))
        conn.commit()
    logger.info("Experiment tables created (or already existed). Done.")


if __name__ == "__main__":
    main()
