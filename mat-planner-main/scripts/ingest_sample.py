#!/usr/bin/env python
"""摄入样例文档"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.models.orm  # noqa: F401 — ensure all models are imported
from app.core.database import SessionLocal, create_all_tables
from app.services.ingestion import IngestionService

if __name__ == "__main__":
    create_all_tables()

    sample_path = Path(__file__).parent.parent / "sample_data" / "htpb_ap_al_sample.md"

    with SessionLocal() as db:
        service = IngestionService(db)
        doc = service.ingest_local_file(
            str(sample_path),
            namespace="default",
            title="HTPB/AP/Al Burning Rate Study",
        )
        print(f"Ingested document: {doc.id} (status={doc.status})")

        # 打印摘要
        print(f"  Sections: {len(doc.sections)}")
        print(f"  Chunks: {len(doc.chunks)}")
