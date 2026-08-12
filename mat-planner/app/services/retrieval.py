"""Hybrid retrieval: keyword ILIKE + bge-m3 vector cosine, fused with RRF.

Retrieval flow:
  1. [optional] Query expansion: LLM generates 2-3 synonym queries → merge keyword results
  2. Keyword search (SQLite ILIKE) → top_k * 3 candidates
  3. Vector search (cosine over all chunk embeddings in memory) → top_k * 3 candidates
  4. RRF fusion (k=60) → RERANK_TOP_K candidates
  5. [optional] LLM reranker: score each candidate's relevance → final top_k
  6. Fallback to keyword-only when EMBED_ENABLED=False or no embeddings stored yet

Performance: embedding matrix is cached in-process after first load (~35MB for 9k chunks).
Cache is invalidated when new documents are ingested.
"""
from __future__ import annotations

import json
import threading
import numpy as np
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.config import settings
from app.models.orm.document import DocumentChunk, Document
from app.models.orm.graph import MemoryGraphNode, MemoryGraphEdge
from app.models.orm.retrieval import RetrievalRun, RetrievalStep
from app.models.schemas.retrieval import RetrievalResult, RetrievalQueryResponse
from app.services.embedding import EmbeddingService

# Domain-specific abbreviation expansion (supplement to LLM expansion)
_DOMAIN_SYNONYMS: dict[str, list[str]] = {
    "burning rate": ["combustion rate", "deflagration rate", "linear burning rate", "r_b"],
    "detonation velocity": ["VOD", "velocity of detonation", "detonation speed", "D_CJ"],
    "density": ["crystal density", "bulk density", "TMD", "theoretical maximum density"],
    "sensitivity": ["impact sensitivity", "shock sensitivity", "friction sensitivity"],
    "detonation pressure": ["CJ pressure", "P_CJ", "Chapman-Jouguet pressure"],
    "heat of explosion": ["heat of detonation", "Q_det", "explosive energy"],
    "melting point": ["melting temperature", "T_m", "fusion point"],
    "oxygen balance": ["OB%", "oxygen content", "oxidizer balance"],
}

_RRF_K = 60  # standard RRF constant

# ── In-process embedding cache ────────────────────────────────────
# Avoids re-reading 35MB of BLOB data from SQLite on every request.
# Structure: {"matrix": np.ndarray, "chunk_ids": list[str], "namespace": str}
_emb_cache: dict = {}
_emb_cache_lock = threading.Lock()

# ── In-process query result cache ─────────────────────────────────
# Key: (query_normalized, namespace, top_k)
# Value: (timestamp: float, results: list[RetrievalResult], evidence_text: str, result_count: int)
# TTL: QUERY_CACHE_TTL seconds (default 300 = 5 minutes)
# Invalidated together with embedding cache on new ingestion.
_query_cache: dict[tuple, tuple] = {}
_query_cache_lock = threading.Lock()
_QUERY_CACHE_TTL = 300  # seconds

# ── Prefetch cache for expansion + HyDE ───────────────────────────
# Key: query string  Value: {"expansions": Future, "hyde_emb": Future}
_prefetch_futures: dict[str, dict] = {}
_prefetch_lock = threading.Lock()


def prefetch_for_query(query: str) -> None:
    """Kick off background computation of query expansion and HyDE embedding.

    Call this as early as possible (before query_understanding runs).
    Results are stored as concurrent.futures.Future objects and consumed
    transparently by _expand_query() and _get_query_embedding().
    Completely non-blocking — never raises.
    """
    from concurrent.futures import ThreadPoolExecutor
    with _prefetch_lock:
        if query in _prefetch_futures:
            return  # already started
        _prefetch_futures[query] = {}

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")

    def _do_expansion():
        try:
            return _QueryExpander().expand(query)
        except Exception:
            return [query]

    def _do_hyde():
        try:
            return _HyDEGenerator().generate(query)
        except Exception:
            return None

    with _prefetch_lock:
        _prefetch_futures[query]["expansions"] = pool.submit(_do_expansion)
        _prefetch_futures[query]["hyde_text"] = pool.submit(_do_hyde)

    pool.shutdown(wait=False)


def _get_prefetched(query: str, key: str):
    """Return prefetch result if ready, else None (non-blocking)."""
    with _prefetch_lock:
        entry = _prefetch_futures.get(query, {})
        fut = entry.get(key)
    if fut is None:
        return None
    try:
        return fut.result(timeout=0) if fut.done() else fut.result(timeout=20)
    except Exception:
        return None


_EXPANSION_SYSTEM = (
    "You are a query expansion assistant for energetic materials research.\n"
    "Given a search query, generate up to 2 alternative phrasings that would "
    "retrieve relevant scientific literature. Focus on technical synonyms, "
    "abbreviations, and alternate terminology used in propellant/explosive papers.\n"
    'Return ONLY a JSON array of strings: ["query1", "query2"]\n'
    "Do NOT include the original query. Return [] if no good alternatives exist."
)

_HYDE_SYSTEM = (
    "You are an energetic materials scientist. "
    "Write a 2–3 sentence excerpt that would appear in a peer-reviewed paper "
    "that directly answers the user's query. "
    "Use precise technical terminology, typical units (g/cm³, km/s, J/g, MPa, s…), "
    "and realistic numerical values where appropriate. "
    "Do NOT explain or preface — output only the passage text."
)


class _QueryExpander:
    def expand(self, query: str) -> list[str]:
        expansions: list[str] = [query]
        q_lower = query.lower()
        for term, synonyms in _DOMAIN_SYNONYMS.items():
            if term in q_lower:
                for syn in synonyms[:2]:
                    candidate = q_lower.replace(term, syn)
                    if candidate not in expansions:
                        expansions.append(candidate)
        if not settings.QUERY_EXPANSION_ENABLED or not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return expansions[:3]
        try:
            from app.services.llm_client import get_llm_client
            client = get_llm_client()
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "system", "content": _EXPANSION_SYSTEM},
                          {"role": "user", "content": query}],
                response_format={"type": "json_object"},
                timeout=8,
            )
            raw = response.choices[0].message.content or "[]"
            parsed = json.loads(raw)
            alts = parsed if isinstance(parsed, list) else next(
                (v for v in parsed.values() if isinstance(v, list)), [])
            for alt in alts[:2]:
                if isinstance(alt, str) and alt and alt not in expansions:
                    expansions.append(alt)
        except Exception as e:
            logger.debug(f"Query expansion LLM call failed (non-fatal): {e}")
        return expansions[:4]


class _HyDEGenerator:
    def generate(self, query: str) -> str | None:
        if not settings.HYDE_ENABLED or not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return None
        try:
            from app.services.llm_client import get_llm_client
            client = get_llm_client()
            resp = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "system", "content": _HYDE_SYSTEM},
                          {"role": "user", "content": query}],
                max_tokens=settings.HYDE_MAX_TOKENS,
                timeout=8,
            )
            return (resp.choices[0].message.content or "").strip() or None
        except Exception as e:
            logger.debug(f"HyDE LLM call failed (non-fatal): {e}")
            return None


def invalidate_embedding_cache() -> None:
    """Call this after ingesting new documents."""
    with _emb_cache_lock:
        _emb_cache.clear()
    with _query_cache_lock:
        _query_cache.clear()
    logger.debug("Embedding + query caches cleared")


def _get_embedding_matrix(db: Session, namespace: str) -> tuple[np.ndarray, list[str]]:
    """Return (matrix [N×dim], chunk_ids [N]) — cached after first call."""
    cache_key = namespace
    with _emb_cache_lock:
        cached = _emb_cache.get(cache_key)
        if cached is not None:
            return cached["matrix"], cached["chunk_ids"]

    # Load from DB
    q = (
        db.query(DocumentChunk.id, DocumentChunk.embedding)
        .join(Document, DocumentChunk.document_id == Document.id)
        .filter(Document.status == "done")
        .filter(DocumentChunk.embedding.isnot(None))
    )
    if namespace != "__all__":
        q = q.filter(Document.namespace == namespace)

    rows = q.all()
    if not rows:
        return np.empty((0, settings.EMBED_DIM), dtype=np.float32), []

    chunk_ids = [r[0] for r in rows]
    matrix = np.stack([EmbeddingService.from_bytes(r[1]) for r in rows]).astype(np.float32)

    with _emb_cache_lock:
        _emb_cache[cache_key] = {"matrix": matrix, "chunk_ids": chunk_ids}
    logger.info(f"Embedding cache loaded: {len(chunk_ids)} chunks (namespace={namespace!r})")
    return matrix, chunk_ids


class RetrievalService:
    def __init__(self, db: Session):
        self.db = db
        self._emb = EmbeddingService() if settings.EMBED_ENABLED else None

    # ──────────────────────────────────────────────────────────────
    # Query expansion
    # ──────────────────────────────────────────────────────────────

    def _expand_query(self, query: str) -> list[str]:
        """Return [original] + synonym expansions. Uses prefetch result if available."""
        prefetched = _get_prefetched(query, "expansions")
        if prefetched is not None:
            logger.debug("Query expansion: using prefetched result")
            return prefetched
        return _QueryExpander().expand(query)

    # ──────────────────────────────────────────────────────────────
    # HyDE: Hypothetical Document Embedding
    # ──────────────────────────────────────────────────────────────

    def _get_query_embedding(self, query: str) -> np.ndarray | None:
        """Return the embedding vector to use for vector search.

        Uses prefetched HyDE hypothesis when available, avoiding a serial LLM call.
        Falls back to raw query embedding on any error.
        """
        if not self._emb:
            return None
        try:
            raw_emb = np.array(self._emb.embed([query])[0], dtype=np.float32)
        except Exception as e:
            logger.warning(f"Query embedding failed: {e}")
            return None
        if not np.any(raw_emb):
            return None

        if not settings.HYDE_ENABLED or not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return raw_emb

        # Try prefetch first, fall back to synchronous generation
        hypothesis = _get_prefetched(query, "hyde_text")
        if hypothesis is None:
            hypothesis = _HyDEGenerator().generate(query)
        if not hypothesis:
            return raw_emb

        try:
            hyp_emb = np.array(self._emb.embed([hypothesis])[0], dtype=np.float32)
            if not np.any(hyp_emb):
                return raw_emb
            averaged = (raw_emb + hyp_emb) / 2.0
            logger.debug(f"HyDE: hypothesis ({len(hypothesis)} chars), averaged embeddings")
            return averaged
        except Exception as e:
            logger.debug(f"HyDE embedding failed (non-fatal): {e}")
            return raw_emb

    # ──────────────────────────────────────────────────────────────
    # LLM-based reranker
    # ──────────────────────────────────────────────────────────────

    def _rerank(
        self,
        query: str,
        candidates: list[tuple[DocumentChunk, float]],
        top_k: int,
    ) -> list[tuple[DocumentChunk, float]]:
        """Score each candidate's relevance to the query via LLM and reorder.

        Uses listwise scoring: LLM returns indices ordered by relevance.
        Gracefully falls back to RRF order if LLM not available or fails.
        """
        if not settings.RERANK_ENABLED:
            return candidates[:top_k]
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return candidates[:top_k]
        if len(candidates) <= top_k:
            return candidates

        try:
            from app.services.llm_client import get_llm_client
            client = get_llm_client()

            # Build passage list (truncate to 300 chars each to keep prompt manageable)
            passages = []
            for i, (chunk, _) in enumerate(candidates):
                text = chunk.chunk_text[:300].replace("\n", " ")
                passages.append(f"[{i}] {text}")
            passages_text = "\n\n".join(passages)

            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a relevance scoring assistant for energetic materials research.\n"
                            f"Rerank the following text passages by relevance to the query: \"{query}\"\n"
                            f"Return a JSON object: {{\"ranking\": [i, j, k, ...]}} where the list "
                            f"contains passage indices (0-based) ordered from MOST to LEAST relevant.\n"
                            f"Include all {len(candidates)} indices. Return ONLY JSON."
                        ),
                    },
                    {"role": "user", "content": passages_text},
                ],
                response_format={"type": "json_object"},
                timeout=15,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            ranking: list[int] = parsed.get("ranking", [])

            # Validate: must be a permutation of 0..N-1
            valid = [i for i in ranking if isinstance(i, int) and 0 <= i < len(candidates)]
            seen = set()
            deduped = []
            for i in valid:
                if i not in seen:
                    deduped.append(i)
                    seen.add(i)
            # Append any missing indices at the end
            for i in range(len(candidates)):
                if i not in seen:
                    deduped.append(i)

            reranked = [candidates[i] for i in deduped]
            logger.debug(f"Reranker reordered {len(candidates)} candidates → top {top_k}")
            return reranked[:top_k]

        except Exception as e:
            logger.debug(f"Reranker LLM call failed (non-fatal): {e}")
            return candidates[:top_k]

    # ──────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────

    def query(
        self, query: str, namespace: str = "default", top_k: int = 10
    ) -> RetrievalQueryResponse:
        import time

        # "default" namespace with no data → search ALL namespaces as fallback
        # This handles the common case where data was ingested under a non-default namespace
        if namespace == "default":
            from app.models.orm.document import Document as _Doc
            exists = self.db.query(_Doc).filter(_Doc.namespace == "default", _Doc.status == "done").first()
            if not exists:
                namespace = "__all__"

        # ── Query result TTL cache ─────────────────────────────────
        cache_key = (query.strip().lower(), namespace, top_k)
        now = time.monotonic()
        with _query_cache_lock:
            cached = _query_cache.get(cache_key)
        if cached is not None:
            ts, cached_results, cached_evidence, cached_count = cached
            if now - ts < _QUERY_CACHE_TTL:
                logger.debug(f"Query cache hit: {query!r} (namespace={namespace!r})")
                # Still log a RetrievalRun for analytics (mark as cached)
                run = RetrievalRun(query=query, namespace=namespace, top_k=top_k,
                                   result_count=cached_count)
                self.db.add(run)
                self.db.add(RetrievalStep(
                    run_id=run.id,
                    step_type="cache_hit",
                    action=f"cache:{query[:80]}",
                    result_json=f'{{"count":{cached_count},"cached":true}}',
                ))
                self.db.commit()
                return RetrievalQueryResponse(
                    query=query,
                    namespace=namespace,
                    results=cached_results,
                    evidence_text=cached_evidence,
                    run_id=run.id,
                )
            else:
                # Expired entry — remove it
                with _query_cache_lock:
                    _query_cache.pop(cache_key, None)

        run = RetrievalRun(query=query, namespace=namespace, top_k=top_k)
        self.db.add(run)
        self.db.flush()

        # Step 1: expand query into synonyms / alternate phrasings
        queries = self._expand_query(query)
        method_parts: list[str] = []

        # Step 2: keyword search across all query expansions (merge by chunk_id)
        kw_all: dict[str, tuple[DocumentChunk, float]] = {}
        for q in queries:
            for chunk, score in self._keyword_search(q, namespace, top_k * 3):
                if chunk.id not in kw_all or score > kw_all[chunk.id][1]:
                    kw_all[chunk.id] = (chunk, score)
        kw_ranked = sorted(kw_all.values(), key=lambda x: x[1], reverse=True)

        # Step 2b: document-level summary search (broad cross-document recall)
        doc_ranked = self._doc_summary_search(query, namespace, top_k * 2)

        # Step 3: chunk-level vector search (original query only — embeddings are semantic)
        vec_ranked = self._vector_search(query, namespace, top_k * 3)

        # Step 4: 3-way RRF fusion: keyword + chunk-vector + doc-summary → RERANK_TOP_K
        rerank_k = max(top_k, settings.RERANK_TOP_K)
        if vec_ranked:
            merged = self._rrf_merge(kw_ranked, vec_ranked, rerank_k)
            method_parts.append("hybrid_rrf")
        else:
            merged = kw_ranked[:rerank_k]
            method_parts.append("keyword_only")

        # Interleave doc-summary results: promote chunks from highly-matching docs
        if doc_ranked:
            merged = self._rrf_merge_3way(merged, doc_ranked, rerank_k)
            method_parts.append("doc_summary")

        if len(queries) > 1:
            method_parts.append(f"expanded({len(queries)})")

        # Step 5: LLM reranker → final top_k
        if len(merged) > top_k:
            merged = self._rerank(query, merged, top_k)
            method_parts.append("reranked")
        else:
            merged = merged[:top_k]

        method = "+".join(method_parts)

        # Fetch parent chunks for hierarchical context expansion
        parent_ids = [c.parent_chunk_id for c, _ in merged if c.parent_chunk_id]
        parent_map: dict[str, DocumentChunk] = {}
        if parent_ids:
            parents = self.db.query(DocumentChunk).filter(DocumentChunk.id.in_(parent_ids)).all()
            parent_map = {p.id: p for p in parents}

        # Batch-fetch document titles for source attribution
        doc_ids = list({c.document_id for c, _ in merged})
        doc_title_map: dict[str, str] = {}
        if doc_ids:
            docs = self.db.query(Document.id, Document.title).filter(Document.id.in_(doc_ids)).all()
            doc_title_map = {d.id: d.title for d in docs}

        results = []
        for c, score in merged:
            if c.parent_chunk_id and c.parent_chunk_id in parent_map:
                context = parent_map[c.parent_chunk_id].chunk_text
            else:
                context = ""
            results.append(RetrievalResult(
                document_id=c.document_id,
                document_title=doc_title_map.get(c.document_id, ""),
                chunk_id=c.id,
                section_path=c.section_path,
                page_start=c.page_start,
                page_end=c.page_end,
                text=c.chunk_text,
                context=context,
                score=score,
            ))

        # evidence_text uses full parent context when available (richer context for LLM)
        evidence_parts = [
            f"[{i}] {r.section_path}\n{r.context or r.text}\n"
            for i, r in enumerate(results, 1)
        ]

        # BFS section tree expansion: add parent + sibling section context
        matched_chunk_objs = [c for c, _ in merged]
        exclude_ids = {r.chunk_id for r in results} | {c.parent_chunk_id for c in matched_chunk_objs if c.parent_chunk_id}
        extra_sections = self._bfs_expand_sections(matched_chunk_objs, exclude_ids)
        if extra_sections:
            evidence_parts.append("\n--- Adjacent Section Context (structural expansion) ---")
            for sec_path, sec_text in extra_sections:
                evidence_parts.append(f"[§{sec_path}]\n{sec_text}\n")

        # KG graph expansion: pull related-entity chunks from knowledge graph neighbors
        graph_extras = self._graph_expand_results(matched_chunk_objs, namespace, exclude_ids)
        if graph_extras:
            evidence_parts.append("\n--- Knowledge Graph Related Context ---")
            for entity_label, extra_text in graph_extras:
                evidence_parts.append(f"[KG:{entity_label}]\n{extra_text}\n")

        evidence_text = "\n".join(evidence_parts)

        run.result_count = len(results)
        self.db.add(RetrievalStep(
            run_id=run.id,
            step_type="search",
            action=f"{method}:{query[:80]}",
            result_json=f'{{"count":{len(results)}}}',
        ))
        self.db.commit()

        # Store in TTL cache for repeated queries
        with _query_cache_lock:
            _query_cache[cache_key] = (now, results, evidence_text, len(results))

        return RetrievalQueryResponse(
            query=query,
            namespace=namespace,
            results=results,
            evidence_text=evidence_text,
            run_id=run.id,
        )

    # ──────────────────────────────────────────────────────────────
    # Knowledge graph guided expansion
    # ──────────────────────────────────────────────────────────────

    def _graph_expand_results(
        self,
        matched_chunks: list[DocumentChunk],
        namespace: str,
        exclude_chunk_ids: set[str],
    ) -> list[tuple[str, str]]:
        """KG-guided context expansion.

        1. Extract entities mentioned in top retrieved chunks.
        2. Find their strong KG neighbors (CO_OCCURS_WITH / COMPARED_BY_*).
        3. For each new neighbor entity, pull a representative chunk.

        Returns list of (entity_label, text_snippet) to append to evidence_text.
        """
        if not settings.GRAPH_EXPANSION_ENABLED:
            return []

        from app.pipeline.graph_builder import _entities_in_text

        # Step 1: entity mentions in top-5 chunks
        found_entities: set[str] = set()
        for chunk in matched_chunks[:5]:
            found_entities |= _entities_in_text(chunk.chunk_text)
        if not found_entities:
            return []

        # Step 2: look up graph nodes for found entities
        entity_nodes = (
            self.db.query(MemoryGraphNode)
            .filter(MemoryGraphNode.label.in_(found_entities))
            .all()
        )
        if not entity_nodes:
            return []

        source_ids = [n.id for n in entity_nodes]
        label_by_id: dict[str, str] = {n.id: n.label for n in entity_nodes}

        # Step 3: outgoing edges above weight threshold
        edges = (
            self.db.query(MemoryGraphEdge)
            .filter(
                MemoryGraphEdge.source_id.in_(source_ids),
                MemoryGraphEdge.weight >= settings.GRAPH_EXPANSION_MIN_WEIGHT,
            )
            .order_by(MemoryGraphEdge.weight.desc())
            .limit(30)
            .all()
        )

        # Collect top-N new neighbor labels (not already in found_entities)
        neighbor_labels: list[str] = []
        seen: set[str] = set(found_entities)
        for edge in edges:
            # Resolve target label (may not be in label_by_id if it wasn't in found_entities)
            label = label_by_id.get(edge.target_id)
            if label is None:
                node = self.db.get(MemoryGraphNode, edge.target_id)
                if node:
                    label = node.label
                    label_by_id[edge.target_id] = label
            if label and label not in seen:
                neighbor_labels.append(label)
                seen.add(label)
            if len(neighbor_labels) >= settings.GRAPH_EXPANSION_MAX_ENTITIES:
                break

        if not neighbor_labels:
            return []

        # Step 4: fetch a representative chunk for each neighbor
        extras: list[tuple[str, str]] = []
        for label in neighbor_labels:
            q = (
                self.db.query(DocumentChunk)
                .join(Document, DocumentChunk.document_id == Document.id)
                .filter(
                    Document.status == "done",
                    DocumentChunk.chunk_type != "parent",
                    DocumentChunk.chunk_text.ilike(f"%{label}%"),
                    DocumentChunk.id.notin_(exclude_chunk_ids),
                )
            )
            if namespace != "__all__":
                q = q.filter(Document.namespace == namespace)
            chunk = q.first()
            if chunk:
                extras.append((label, chunk.chunk_text[:600]))
                exclude_chunk_ids.add(chunk.id)

        if extras:
            logger.debug(
                f"Graph expansion: {len(found_entities)} source entities → "
                f"{len(neighbor_labels)} neighbors → {len(extras)} extra chunks"
            )
        return extras

    # ──────────────────────────────────────────────────────────────
    # Document tree BFS expansion
    # ──────────────────────────────────────────────────────────────

    def _bfs_expand_sections(
        self,
        matched_chunks: list[DocumentChunk],
        exclude_ids: set[str],
    ) -> list[tuple[str, str]]:
        """BFS on the section tree: pull parent + sibling sections as extra context.

        Returns list of (section_path, text) for chunks NOT already in results.
        Capped at TREE_EXPANSION_MAX_SECTIONS entries.
        """
        if not settings.TREE_EXPANSION_ENABLED:
            return []

        from app.models.orm.document import DocumentSection

        # Collect unique section_ids from matched chunks
        section_ids = {c.section_id for c in matched_chunks if c.section_id}
        if not section_ids:
            return []

        # Load these sections (1 DB call)
        sections = (
            self.db.query(DocumentSection)
            .filter(DocumentSection.id.in_(section_ids))
            .all()
        )

        # BFS: parent + siblings (1 hop)
        expand_ids: set[str] = set()
        for sec in sections:
            if sec.parent_id:
                expand_ids.add(sec.parent_id)          # parent
                # siblings: other children of the same parent
                sibling_ids = [
                    s.id for s in self.db.query(DocumentSection.id)
                    .filter_by(parent_id=sec.parent_id)
                    .all()
                ]
                for sid in sibling_ids:
                    if sid not in section_ids:          # skip already-matched
                        expand_ids.add(sid)

        if not expand_ids:
            return []

        # Prefer parent-type chunks (full section text); fall back to leaf chunks
        expanded: list[DocumentChunk] = (
            self.db.query(DocumentChunk)
            .filter(
                DocumentChunk.section_id.in_(expand_ids),
                DocumentChunk.chunk_type == "parent",
                DocumentChunk.id.notin_(exclude_ids),
            )
            .limit(settings.TREE_EXPANSION_MAX_SECTIONS)
            .all()
        )
        if not expanded:
            expanded = (
                self.db.query(DocumentChunk)
                .filter(
                    DocumentChunk.section_id.in_(expand_ids),
                    DocumentChunk.chunk_type != "parent",
                    DocumentChunk.id.notin_(exclude_ids),
                )
                .limit(settings.TREE_EXPANSION_MAX_SECTIONS)
                .all()
            )

        return [(c.section_path, c.chunk_text[:800]) for c in expanded]

    # ──────────────────────────────────────────────────────────────
    # Keyword search (unchanged from v1)
    # ──────────────────────────────────────────────────────────────

    def _keyword_search(
        self, query: str, namespace: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        terms = [t.strip() for t in query.split() if len(t.strip()) > 1]
        if not terms:
            terms = [query.strip()]

        conditions = [DocumentChunk.chunk_text.ilike(f"%{term}%") for term in terms]
        q = (
            self.db.query(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .filter(Document.status == "done")
            .filter(DocumentChunk.chunk_type != "parent")  # exclude parent context chunks
        )
        if namespace != "__all__":
            q = q.filter(Document.namespace == namespace)
        chunks = q.filter(or_(*conditions)).limit(limit).all()

        def _score(c: DocumentChunk) -> float:
            text = c.chunk_text.lower()
            return sum(1.0 for t in terms if t.lower() in text) / len(terms)

        return sorted([(c, _score(c)) for c in chunks], key=lambda x: x[1], reverse=True)

    # ──────────────────────────────────────────────────────────────
    # Vector search
    # ──────────────────────────────────────────────────────────────

    def _vector_search(
        self, query: str, namespace: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        query_emb = self._get_query_embedding(query)
        if query_emb is None:
            return []

        if settings.is_postgres:
            return self._vector_search_pgvector(query_emb, namespace, limit)
        else:
            return self._vector_search_numpy(query_emb, namespace, limit)

    def _vector_search_pgvector(
        self, query_emb: np.ndarray, namespace: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        """PostgreSQL pgvector ANN search via HNSW index (sub-millisecond)."""
        from sqlalchemy import text as sql_text

        vec_str = str(query_emb.tolist())
        ns_filter = "" if namespace == "__all__" else "AND d.namespace = :ns"
        sql = sql_text(f"""
            SELECT c.id, 1 - (c.embedding_vec <=> :qvec::vector) AS score
            FROM document_chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.status = 'done'
              AND c.chunk_type != 'parent'
              AND c.embedding_vec IS NOT NULL
              {ns_filter}
            ORDER BY c.embedding_vec <=> :qvec::vector
            LIMIT :lim
        """)
        params: dict = {"qvec": vec_str, "lim": limit}
        if namespace != "__all__":
            params["ns"] = namespace

        rows = self.db.execute(sql, params).fetchall()
        if not rows:
            return []

        ids = [r[0] for r in rows]
        scores = {r[0]: float(r[1]) for r in rows}
        chunks = self.db.query(DocumentChunk).filter(DocumentChunk.id.in_(ids)).all()
        chunk_map = {c.id: c for c in chunks}
        return [(chunk_map[cid], scores[cid]) for cid in ids if cid in chunk_map]

    def _vector_search_numpy(
        self, query_emb: np.ndarray, namespace: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        """SQLite fallback: cosine similarity over in-process numpy matrix."""
        matrix, chunk_ids = _get_embedding_matrix(self.db, namespace)
        if matrix.shape[0] == 0:
            return []

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-9, norms)
        matrix_normed = matrix / norms
        query_norm = query_emb / (np.linalg.norm(query_emb) + 1e-9)
        sims = matrix_normed @ query_norm

        n_take = min(limit, len(sims))
        top_indices = np.argpartition(sims, -n_take)[-n_take:]
        top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

        top_ids = [chunk_ids[i] for i in top_indices]
        top_sims = [float(sims[i]) for i in top_indices]
        sim_map = dict(zip(top_ids, top_sims))

        chunks = self.db.query(DocumentChunk).filter(DocumentChunk.id.in_(top_ids)).all()
        chunk_map = {c.id: c for c in chunks}
        return [(chunk_map[cid], sim_map[cid]) for cid in top_ids if cid in chunk_map]

    # ──────────────────────────────────────────────────────────────
    # RRF fusion
    # ──────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────
    # Document-level summary search
    # ──────────────────────────────────────────────────────────────

    def _doc_summary_search(
        self, query: str, namespace: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        """Embed query against document summary embeddings for cross-document recall.

        Returns representative chunks from the best-matching documents.
        Uses HyDE-augmented embedding when enabled for better doc-level recall.
        """
        query_emb = self._get_query_embedding(query)
        if query_emb is None:
            return []

        # Load document summaries with embeddings
        q = (
            self.db.query(Document.id, Document.summary_embedding)
            .filter(
                Document.status == "done",
                Document.summary_embedding.isnot(None),
            )
        )
        if namespace != "__all__":
            q = q.filter(Document.namespace == namespace)

        rows = q.all()
        if not rows:
            return []

        doc_ids = [r[0] for r in rows]
        matrix = np.stack([
            EmbeddingService.from_bytes(r[1]) for r in rows
        ]).astype(np.float32)

        # Cosine similarity
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-9, norms)
        q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-9)
        sims = (matrix / norms) @ q_norm

        n_take = min(limit, len(sims))
        top_idx = np.argpartition(sims, -n_take)[-n_take:]
        top_idx = top_idx[np.argsort(sims[top_idx])[::-1]]

        # For each top doc, return its best leaf chunk (for RRF compatibility)
        results: list[tuple[DocumentChunk, float]] = []
        for i in top_idx:
            doc_id = doc_ids[i]
            score = float(sims[i])
            chunk = (
                self.db.query(DocumentChunk)
                .filter(
                    DocumentChunk.document_id == doc_id,
                    DocumentChunk.chunk_type != "parent",
                )
                .first()
            )
            if chunk:
                results.append((chunk, score))

        return results

    @staticmethod
    def _rrf_merge_3way(
        primary: list[tuple[DocumentChunk, float]],
        doc_level: list[tuple[DocumentChunk, float]],
        top_k: int,
    ) -> list[tuple[DocumentChunk, float]]:
        """Merge primary (chunk-level) results with doc-level summary matches via RRF.

        Doc-level matches get a discount factor (0.5) since they represent
        document-level relevance, not chunk-level precision.
        """
        scores: dict[str, float] = {}
        chunk_map: dict[str, DocumentChunk] = {}

        for rank, (chunk, _) in enumerate(primary, 1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            chunk_map[chunk.id] = chunk

        doc_discount = 0.5
        for rank, (chunk, _) in enumerate(doc_level, 1):
            bonus = doc_discount / (_RRF_K + rank)
            scores[chunk.id] = scores.get(chunk.id, 0.0) + bonus
            chunk_map[chunk.id] = chunk

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(chunk_map[cid], score) for cid, score in ranked]

    @staticmethod
    def _rrf_merge(
        kw: list[tuple[DocumentChunk, float]],
        vec: list[tuple[DocumentChunk, float]],
        top_k: int,
    ) -> list[tuple[DocumentChunk, float]]:
        scores: dict[str, float] = {}
        chunk_map: dict[str, DocumentChunk] = {}

        for rank, (chunk, _) in enumerate(kw, 1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            chunk_map[chunk.id] = chunk

        for rank, (chunk, _) in enumerate(vec, 1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            chunk_map[chunk.id] = chunk

        # Retrieval-hit boosting: add log(1 + hit_count) * 0.05 to RRF score
        # Chunks that were repeatedly used in answers rank higher in future queries.
        for cid, chunk in chunk_map.items():
            hit_count = getattr(chunk, "retrieval_hit_count", 0) or 0
            if hit_count > 0:
                scores[cid] = scores[cid] + np.log1p(hit_count) * 0.05

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [(chunk_map[cid], score) for cid, score in ranked]

    def record_chunk_hits(self, chunk_ids: list[str]) -> None:
        """Increment retrieval_hit_count for chunks actually used in an answer.

        Call this from the agent's Answer Builder after confirming which evidence
        chunks were cited. Non-fatal: errors are logged and suppressed.
        """
        if not chunk_ids:
            return
        try:
            from sqlalchemy import text as sql_text
            for cid in chunk_ids:
                self.db.execute(
                    sql_text(
                        "UPDATE document_chunks SET retrieval_hit_count = retrieval_hit_count + 1 "
                        "WHERE id = :id"
                    ),
                    {"id": cid},
                )
            self.db.commit()
        except Exception as e:
            logger.debug(f"record_chunk_hits failed (non-fatal): {e}")
