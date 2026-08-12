"""SQLAlchemy engine setup — supports both SQLite (dev/test) and PostgreSQL (prod).

SQLite path : WAL mode + foreign keys via PRAGMA; used for local dev and all tests.
PostgreSQL path: psycopg2 driver; pgvector extension managed by setup_pgvector().
"""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


def _make_engine():
    if settings.is_postgres:
        return create_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    else:
        eng = create_engine(
            settings.DATABASE_URL,
            connect_args={"check_same_thread": False},
            echo=False,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=180000")  # wait up to 3 min for write lock
            cur.close()

        return eng


engine = _make_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all_tables():
    """Create all ORM-managed tables."""
    import app.models.orm  # noqa: F401
    Base.metadata.create_all(bind=engine)


def setup_pgvector(drop_index: bool = False) -> dict:
    """Create pgvector extension, add embedding_vec column, build HNSW index.

    Safe to call repeatedly — all statements use IF NOT EXISTS guards.
    Only runs when is_postgres=True; returns {"skipped": reason} on SQLite.
    """
    if not settings.is_postgres:
        return {"skipped": "not PostgreSQL"}

    steps: list[str] = []
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        steps.append("extension: vector")

        conn.execute(text(
            f"ALTER TABLE document_chunks "
            f"ADD COLUMN IF NOT EXISTS embedding_vec vector({settings.EMBED_DIM})"
        ))
        steps.append(f"column: embedding_vec vector({settings.EMBED_DIM})")

        if drop_index:
            conn.execute(text("DROP INDEX IF EXISTS idx_chunks_embedding_vec_hnsw"))
            steps.append("dropped: idx_chunks_embedding_vec_hnsw")

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_vec_hnsw
            ON document_chunks
            USING hnsw (embedding_vec vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))
        steps.append("index: HNSW cosine (m=16, ef=64)")

        conn.commit()

    return {"done": steps}


def backfill_pgvector_from_bytes() -> int:
    """Populate embedding_vec from existing LargeBinary embedding blobs.

    Run this after migrating a SQLite export into PostgreSQL to fill
    the vector column from the serialised float32 bytes already stored.
    Returns number of chunks updated.
    """
    if not settings.is_postgres:
        return 0

    from app.models.orm.document import DocumentChunk
    from app.services.embedding import EmbeddingService

    updated = 0
    with SessionLocal() as db:
        chunks = db.query(DocumentChunk).filter(DocumentChunk.embedding.isnot(None)).all()
        for chunk in chunks:
            if chunk.embedding:
                vec = EmbeddingService.from_bytes(chunk.embedding).tolist()
                db.execute(
                    text("UPDATE document_chunks SET embedding_vec = :v WHERE id = :id"),
                    {"v": str(vec), "id": chunk.id},
                )
                updated += 1
                if updated % 500 == 0:
                    db.commit()
        db.commit()
    return updated
