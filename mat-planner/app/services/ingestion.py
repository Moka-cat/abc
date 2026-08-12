from pathlib import Path
from sqlalchemy.orm import Session
from loguru import logger

from app.core.config import settings
from app.models.orm.document import Document, DocumentSection, DocumentChunk
from app.models.orm.asset import DocumentAsset
from app.models.orm.domain import DomainEntity, EntityAlias, PropertyValue
from app.pipeline.router import parse_file
from app.pipeline.section_builder import build_section_tree, flatten_sections
from app.pipeline.chunk_builder import build_chunks
from app.pipeline.asset_extractor import extract_assets
from app.pipeline.extraction import ExtractionPipeline, ExtractionContext
from app.pipeline.graph_builder import build_graph
from app.services.embedding import EmbeddingService
from app.services.retrieval import invalidate_embedding_cache


class IngestionService:
    def __init__(self, db: Session, job_id: str | None = None):
        self.db = db
        self._emb = EmbeddingService() if settings.EMBED_ENABLED else None
        self._job_id = job_id
        self._extraction = ExtractionPipeline()

    def _push_progress(self, progress: int, message: str) -> None:
        """Update job progress non-fatally (no-op if no job_id is set)."""
        if not self._job_id:
            return
        try:
            from app.services.job_store import job_store
            job_store.update(self._job_id, progress=progress, message=message)
        except Exception:
            pass

    def ingest_local_file(
        self, path: str, namespace: str = "default", title: str | None = None,
        force: bool = False,
    ) -> Document:
        """Ingest a local file into the knowledge base.

        Parameters
        ----------
        path : str
            Absolute or relative path to the file.
        namespace : str
            Logical partition for multi-tenant use.
        title : str | None
            Override document title; defaults to filename stem.
        force : bool
            If True and the document already exists (same path + namespace),
            delete it first and re-ingest from scratch. Useful after updating
            source files or fixing parser bugs.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {path}")

        # 同路径+namespace 已存在处理
        existing = (
            self.db.query(Document)
            .filter_by(source_path=str(p), namespace=namespace)
            .first()
        )
        if existing:
            if not force:
                logger.info(f"Document already ingested: {existing.id} ({existing.title})")
                return existing
            # force=True: delete existing document and all derived data
            logger.info(f"Force re-ingesting: deleting existing {existing.id} ({existing.title})")
            self.db.delete(existing)
            self.db.flush()
            try:
                from app.services.retrieval import invalidate_embedding_cache
                invalidate_embedding_cache()
            except Exception:
                pass

        doc_title = title or p.stem
        doc = Document(
            title=doc_title,
            source_path=str(p),
            file_type=p.suffix.lstrip("."),
            namespace=namespace,
            status="processing",
        )
        self.db.add(doc)
        self.db.flush()

        try:
            self._push_progress(5, "Parsing document…")
            parse_result = parse_file(p)
            self._push_progress(15, "Building section tree…")
            section_roots = build_section_tree(parse_result.blocks)
            sections_flat = flatten_sections(section_roots)
            self._push_progress(25, "Chunking sections…")

            # 写入 sections 和 chunks（收集所有 chunk ORM 供后续批量 embed）
            all_chunk_orms: list[DocumentChunk] = []
            section_orm_map: dict[str, DocumentSection] = {}
            for section_node in sections_flat:
                if section_node.title == "__root__":
                    section_orm = None
                else:
                    section_orm = DocumentSection(
                        document_id=doc.id,
                        section_path=section_node.section_path,
                        level=section_node.level,
                        title=section_node.title,
                        page_start=section_node.page_start,
                    )
                    self.db.add(section_orm)
                    self.db.flush()
                    section_orm_map[section_node.section_path] = section_orm

                chunks = build_chunks(section_node)
                assets = extract_assets(chunks)

                # ── 写 chunks（层级：先写父，再写子）─────────────────
                # parent_key_to_id maps "<section_path>:parent" → ORM id
                parent_key_to_id: dict[str, str] = {}
                chunk_orm_map: dict[str, DocumentChunk] = {}

                for chunk in chunks:
                    parent_chunk_id: str | None = None
                    if chunk.parent_key:
                        parent_chunk_id = parent_key_to_id.get(chunk.parent_key)

                    chunk_orm = DocumentChunk(
                        document_id=doc.id,
                        section_id=section_orm.id if section_orm else None,
                        chunk_type=chunk.chunk_type,
                        chunk_text=chunk.text,
                        chunk_index=chunk.chunk_index,
                        section_path=chunk.section_path,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        parent_chunk_id=parent_chunk_id,
                    )
                    self.db.add(chunk_orm)
                    self.db.flush()
                    chunk_orm_map[f"{chunk.section_path}:{chunk.chunk_index}"] = chunk_orm

                    # 记录父 chunk 的 id 供子 chunk 引用
                    if chunk.chunk_type == "parent":
                        section_key = f"{chunk.section_path}:parent"
                        parent_key_to_id[section_key] = chunk_orm.id
                    else:
                        # 只有子 chunk（非 parent）才加入嵌入队列
                        all_chunk_orms.append(chunk_orm)

                # 写 assets
                for asset in assets:
                    asset_orm = DocumentAsset(
                        document_id=doc.id,
                        section_id=section_orm.id if section_orm else None,
                        asset_type=asset.asset_type,
                        content=asset.content,
                        caption=asset.caption,
                        page=asset.page,
                        asset_index=asset.asset_index,
                        section_path=asset.section_path,
                    )
                    self.db.add(asset_orm)

                # Entity / property / formulation extraction via pluggable pipeline
                for chunk in chunks:
                    if chunk.chunk_type not in ("text", "table"):
                        continue
                    chunk_orm_ref = chunk_orm_map.get(f"{chunk.section_path}:{chunk.chunk_index}")
                    self._extraction.run(ExtractionContext(
                        chunk=chunk,
                        chunk_orm=chunk_orm_ref,
                        section_orm=section_orm,
                        document_id=doc.id,
                        namespace=namespace,
                        db=self.db,
                    ))

            # 批量生成向量（只对子 chunk，parent chunk 不嵌入）
            self._push_progress(60, f"Embedding {len(all_chunk_orms)} chunks…")
            if self._emb and all_chunk_orms:
                logger.info(f"Embedding {len(all_chunk_orms)} leaf chunks …")
                texts = [c.chunk_text for c in all_chunk_orms]
                embeddings = self._emb.embed(texts)
                use_pgvector = settings.is_postgres
                for chunk_orm, emb in zip(all_chunk_orms, embeddings):
                    chunk_orm.embedding = EmbeddingService.to_bytes(emb)
                    if use_pgvector and any(emb):
                        # Also populate pgvector column for indexed ANN search
                        from sqlalchemy import text as sql_text
                        self.db.flush()  # ensure chunk_orm.id is set
                        self.db.execute(
                            sql_text(
                                "UPDATE document_chunks SET embedding_vec = :v WHERE id = :id"
                            ),
                            {"v": str(emb), "id": chunk_orm.id},
                        )

            # Generate document-level summary + embedding for cross-document recall
            self._push_progress(80, "Generating document summary…")
            self._generate_doc_summary(doc, all_chunk_orms)

            self._push_progress(90, "Rebuilding knowledge graph…")
            doc.status = "done"
            self.db.commit()
            # Invalidate embedding cache so new chunks are included in next search
            invalidate_embedding_cache()
            # Rebuild knowledge graph to include new entity co-occurrences
            try:
                graph_stats = build_graph(self.db)
                logger.info(
                    f"Knowledge graph rebuilt: {graph_stats['nodes']} nodes, "
                    f"{graph_stats['edges']} edges"
                )
            except Exception as e:
                logger.warning(f"Graph rebuild failed (non-fatal): {e}")
            self._push_progress(100, "Done")
            logger.info(f"Ingested document: {doc.id} ({doc_title})")
            return doc

        except Exception as e:
            doc.status = "failed"
            doc.error_message = str(e)
            self.db.commit()
            logger.error(f"Ingestion failed for {path}: {e}")
            raise

    def _generate_doc_summary(
        self, doc: Document, leaf_chunks: list[DocumentChunk]
    ) -> None:
        """Generate an LLM summary of the document and embed it for document-level retrieval.

        Uses the first ~6000 chars of leaf chunk text as the source.
        Non-fatal: any error is logged and silently suppressed.
        """
        if not leaf_chunks:
            return

        # Build a compact representation from the first N leaf chunks
        combined = "\n\n".join(c.chunk_text for c in leaf_chunks[:30])[:6000]

        # ── LLM summary generation ────────────────────────────────
        summary: str | None = None
        if settings.LLM_BASE_URL and settings.LLM_MODEL:
            try:
                from app.services.llm_client import get_llm_client
                client = get_llm_client()
                resp = client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a scientific document summarizer for energetic materials research.\n"
                                "Summarize the following document excerpt in 3-5 sentences covering:\n"
                                "  1. What compound(s) / formulations are studied\n"
                                "  2. What properties are measured or discussed\n"
                                "  3. Key numerical findings (with units)\n"
                                "  4. Main conclusion or application context\n"
                                "Be dense and factual. No preamble."
                            ),
                        },
                        {"role": "user", "content": combined},
                    ],
                    max_tokens=300,
                    timeout=20,
                )
                summary = (resp.choices[0].message.content or "").strip() or None
            except Exception as e:
                logger.debug(f"Doc summary LLM call failed (non-fatal): {e}")

        # Fallback: use first 500 chars of chunk text as pseudo-summary
        if not summary:
            summary = combined[:500]

        doc.summary = summary

        # ── Embed the summary ─────────────────────────────────────
        if self._emb and summary:
            try:
                emb = self._emb.embed([summary])[0]
                doc.summary_embedding = EmbeddingService.to_bytes(emb)
            except Exception as e:
                logger.debug(f"Doc summary embedding failed (non-fatal): {e}")

        self.db.flush()
        logger.info(f"Doc summary generated for '{doc.title}' ({len(summary or '')} chars)")
