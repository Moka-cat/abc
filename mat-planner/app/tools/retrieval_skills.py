"""Retrieval Skills — 知识库检索工具集，供规划 Agent 和 MCP 调用。

注册的工具（9 个）：
  search_memory          — 混合检索（关键词 + 向量 + RRF + Reranker）
  get_entity_card        — 实体完整属性卡片（密度、爆速、燃速等，含来源证据）
  trace_evidence         — 属性数值溯源到原文引用位置（章节 + 页码 + 原文）
  find_tables            — 按关键词在文档中搜索数据表格（含 caption 匹配）
  compare_values         — 跨实体属性比较，返回均值/极值/排名
  get_source_snippet     — 按 chunk_id 获取完整原文段落
  find_related_entities  — 知识图谱近邻查询（CO_OCCURS_WITH / COMPARED_BY_*）
  get_formulation        — 查找包含某成分的复合推进剂/混合炸药配方
  agentic_search         — 深度导航检索：LLM 读章节大纲，精准定位跨节证据
"""
from __future__ import annotations

import json
import statistics

from sqlalchemy.orm import Session

from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry


# ─────────────────────────────────────────────────────────────────────────────
# 1. search_memory
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class SearchMemoryTool(BaseTool):
    name = "search_memory"
    description = (
        "Search the energetic materials knowledge base using hybrid retrieval "
        "(keyword + vector + RRF fusion + optional reranking). "
        "Use as first tool for any question about properties, synthesis, "
        "performance, or applications of energetic materials."
    )
    parameters = {
        "query": {"type": "string", "description": "Natural language question or keyword phrase."},
        "namespace": {"type": "string", "description": "Document collection to search.", "default": "default"},
        "top_k": {"type": "integer", "description": "Number of evidence chunks to return.", "default": 10},
    }
    required = ["query"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, query: str, namespace: str = "default", top_k: int = 10, **kwargs) -> ToolResult:
        try:
            from app.services.retrieval import RetrievalService
            result = RetrievalService(self.db).query(query, namespace, top_k)
            return ToolResult(success=True, data={
                "query": result.query,
                "results": [r.model_dump() for r in result.results],
                "evidence_text": result.evidence_text,
                "run_id": result.run_id,
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. get_entity_card
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class GetEntityCardTool(BaseTool):
    name = "get_entity_card"
    description = (
        "Retrieve a structured entity card for a named energetic material, "
        "propellant ingredient, oxidiser, or related compound. "
        "Returns all known property values extracted from ingested documents, "
        "along with source evidence and aliases. "
        "Use for questions like 'tell me everything about HMX' or 'RDX properties'."
    )
    parameters = {
        "name": {
            "type": "string",
            "description": "Canonical name or common alias (e.g. 'RDX', 'HMX', 'HTPB', 'ammonium perchlorate'). Case-insensitive.",
        },
    }
    required = ["name"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, name: str, **kwargs) -> ToolResult:
        from app.services.entity import EntityService
        card = EntityService(self.db).get_entity_card(name)
        if not card:
            return ToolResult(success=False, data=None, error=f"Entity not found: {name}")
        return ToolResult(success=True, data=card.model_dump())


# ─────────────────────────────────────────────────────────────────────────────
# 3. trace_evidence
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class TraceEvidenceTool(BaseTool):
    name = "trace_evidence"
    description = (
        "Trace a property value back to its exact source quote in the original document. "
        "Returns the matched sentence, surrounding context, section path, and page number. "
        "Use to verify or cite a specific value found via get_entity_card."
    )
    parameters = {
        "evidence_id": {
            "type": "string",
            "description": "UUID of the evidence record (from get_entity_card results).",
        },
    }
    required = ["evidence_id"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, evidence_id: str, **kwargs) -> ToolResult:
        from app.services.entity import EntityService
        ev = EntityService(self.db).get_evidence(evidence_id)
        if not ev:
            return ToolResult(success=False, data=None, error=f"Evidence not found: {evidence_id}")

        data = ev.model_dump()
        parts = []
        if ev.section_path:
            parts.append(f"§{ev.section_path}")
        if ev.page is not None:
            parts.append(f"p.{ev.page}")
        location = ", ".join(parts) if parts else "unknown location"

        if ev.highlighted:
            data["citation"] = f"> {ev.highlighted}\n> — {location}"
        elif ev.quote:
            data["citation"] = f'> "{ev.quote}"\n> — {location}'
        else:
            data["citation"] = f"[No direct quote available — {location}]"

        return ToolResult(success=True, data=data)


# ─────────────────────────────────────────────────────────────────────────────
# 4. find_tables
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class FindTablesTool(BaseTool):
    name = "find_tables"
    description = (
        "Find data tables in ingested documents relevant to a keyword query. "
        "Use when the user asks for tabular data, comparison tables, or structured "
        "experimental results (e.g. 'burning rate vs pressure table')."
    )
    parameters = {
        "query": {"type": "string", "description": "Keywords to match against table content and captions."},
        "namespace": {"type": "string", "description": "Document collection to search.", "default": "default"},
        "top_k": {"type": "integer", "description": "Maximum number of tables to return.", "default": 5},
    }
    required = ["query"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, query: str, namespace: str = "default", top_k: int = 5, **kwargs) -> ToolResult:
        from app.models.orm.asset import DocumentAsset
        from app.models.orm.document import Document

        terms = query.lower().split()
        assets = (
            self.db.query(DocumentAsset)
            .join(Document, DocumentAsset.document_id == Document.id)
            .filter(Document.namespace == namespace, DocumentAsset.asset_type == "table")
            .all()
        )

        def score(a: DocumentAsset) -> float:
            text = (a.content + " " + (a.caption or "")).lower()
            return sum(1 for t in terms if t in text) / max(len(terms), 1)

        ranked = sorted(assets, key=score, reverse=True)[:top_k]
        return ToolResult(success=True, data={
            "tables": [
                {
                    "id": a.id,
                    "document_id": a.document_id,
                    "section_path": a.section_path,
                    "page": a.page,
                    "content": a.content,
                    "caption": a.caption,
                    "score": score(a),
                }
                for a in ranked
            ]
        })


# ─────────────────────────────────────────────────────────────────────────────
# 5. compare_values
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CompareValuesTool(BaseTool):
    name = "compare_values"
    description = (
        "Compare a numeric property across multiple energetic materials and return "
        "ranked statistics (mean, min, max, spread). "
        "Use for questions like 'compare the density of RDX, HMX, and PETN' or "
        "'which explosive has the highest detonation velocity?'."
    )
    parameters = {
        "property_name": {
            "type": "string",
            "description": (
                "Property to compare: density, detonation_velocity, burning_rate, "
                "detonation_pressure, heat_of_explosion, melting_point, oxygen_balance, impact_sensitivity."
            ),
        },
        "entity_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Entities to compare. Leave empty for all entities with this property.",
            "default": [],
        },
        "namespace": {
            "type": "string",
            "description": "Restrict to entities from this namespace; '__all__' searches all.",
            "default": "default",
        },
    }
    required = ["property_name"]

    def __init__(self, db: Session):
        self.db = db

    def _resolve_entity_ids(self, entity_names: list[str]) -> dict[str, str]:
        from app.models.orm.domain import DomainEntity, EntityAlias
        name_to_id: dict[str, str] = {}
        for name in entity_names:
            ent = self.db.query(DomainEntity).filter(DomainEntity.canonical_name.ilike(name)).first()
            if ent:
                name_to_id[name] = ent.id
                continue
            alias_row = self.db.query(EntityAlias).filter(EntityAlias.alias.ilike(name)).first()
            if alias_row:
                name_to_id[name] = alias_row.entity_id
        return name_to_id

    def run(
        self,
        property_name: str,
        entity_names: list[str] | None = None,
        namespace: str = "default",
        **kwargs,
    ) -> ToolResult:
        from app.models.orm.domain import DomainEntity, PropertyValue

        if entity_names:
            name_to_id = self._resolve_entity_ids(entity_names)
            if not name_to_id:
                return ToolResult(
                    success=False, data=None,
                    error=f"None of the requested entities were found: {entity_names}",
                )
            q = (
                self.db.query(PropertyValue)
                .join(DomainEntity, PropertyValue.entity_id == DomainEntity.id)
                .filter(
                    PropertyValue.property_name.ilike(property_name),
                    PropertyValue.entity_id.in_(list(name_to_id.values())),
                )
            )
        else:
            q = (
                self.db.query(PropertyValue)
                .join(DomainEntity, PropertyValue.entity_id == DomainEntity.id)
                .filter(PropertyValue.property_name.ilike(property_name))
            )

        if namespace and namespace != "__all__":
            q = q.filter(DomainEntity.namespace == namespace)

        pvs = q.all()
        if not pvs:
            return ToolResult(success=True, data={
                "property": property_name,
                "entities_queried": entity_names,
                "rows": [],
                "stats_by_entity": {},
                "summary": "No data found for this property.",
            })

        all_ids = list({pv.entity_id for pv in pvs})
        id_to_name = {
            e.id: e.canonical_name
            for e in self.db.query(DomainEntity).filter(DomainEntity.id.in_(all_ids)).all()
        }

        rows: list[dict] = []
        entity_values: dict[str, list[float]] = {}
        entity_units: dict[str, str] = {}

        for pv in pvs:
            ename = id_to_name.get(pv.entity_id, "unknown")
            rows.append({
                "entity": ename,
                "property": pv.property_name,
                "value_text": pv.value_text,
                "value_numeric": pv.value_numeric,
                "unit": pv.unit,
                "condition": json.loads(pv.condition_json) if pv.condition_json else None,
                "source_document_id": pv.source_document_id,
            })
            if pv.value_numeric is not None:
                entity_values.setdefault(ename, []).append(pv.value_numeric)
                if pv.unit:
                    entity_units[ename] = pv.unit

        stats: dict[str, dict] = {}
        for ename, vals in entity_values.items():
            mean = statistics.mean(vals)
            vmin, vmax = min(vals), max(vals)
            stats[ename] = {
                "n": len(vals),
                "mean": round(mean, 6),
                "min": round(vmin, 6),
                "max": round(vmax, 6),
                "spread_pct": round((vmax - vmin) / mean * 100, 2) if mean else 0,
                "unit": entity_units.get(ename, ""),
                "stdev": round(statistics.stdev(vals), 6) if len(vals) > 1 else None,
            }

        ranking = [name for name, _ in sorted(stats.items(), key=lambda kv: kv[1]["mean"], reverse=True)]

        return ToolResult(success=True, data={
            "property": property_name,
            "entities_queried": entity_names,
            "rows": rows,
            "stats_by_entity": stats,
            "ranking_high_to_low": ranking,
            "unit": list(entity_units.values())[0] if entity_units else None,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 6. get_source_snippet
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class GetSourceSnippetTool(BaseTool):
    name = "get_source_snippet"
    description = (
        "Get the full original text of a document chunk by its ID. "
        "Use when you have a chunk_id and want to read the complete source passage."
    )
    parameters = {
        "chunk_id": {"type": "string", "description": "UUID of the document chunk."},
    }
    required = ["chunk_id"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, chunk_id: str, **kwargs) -> ToolResult:
        from app.models.orm.document import DocumentChunk
        chunk = self.db.get(DocumentChunk, chunk_id)
        if not chunk:
            return ToolResult(success=False, data=None, error=f"Chunk not found: {chunk_id}")
        return ToolResult(success=True, data={
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "section_path": chunk.section_path,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "text": chunk.chunk_text,
        })


# ─────────────────────────────────────────────────────────────────────────────
# 7. find_related_entities
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class FindRelatedEntitiesTool(BaseTool):
    name = "find_related_entities"
    description = (
        "Find entities related to a given entity via the knowledge graph. "
        "Useful for 'what compounds are similar to RDX?' or "
        "'what ingredients are commonly studied with HTPB?'. "
        "Relation types: CO_OCCURS_WITH, COMPARED_BY_density, COMPARED_BY_detonation_velocity, etc."
    )
    parameters = {
        "entity_name": {"type": "string", "description": "Canonical entity name, e.g. 'RDX'."},
        "relation": {
            "type": "string",
            "description": "Edge type: CO_OCCURS_WITH or COMPARED_BY_{property}. Omit for all.",
            "default": "",
        },
        "limit": {"type": "integer", "description": "Max results.", "default": 10},
    }
    required = ["entity_name"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, entity_name: str, relation: str | None = None, limit: int = 10, **kwargs) -> ToolResult:
        from app.models.orm.graph import MemoryGraphNode, MemoryGraphEdge

        node = (
            self.db.query(MemoryGraphNode)
            .filter_by(ref_table="domain_entities")
            .filter(MemoryGraphNode.label == entity_name)
            .first()
        )
        if not node:
            nodes = self.db.query(MemoryGraphNode).filter_by(ref_table="domain_entities").all()
            node = next((n for n in nodes if n.label.lower() == entity_name.lower()), None)
        if not node:
            return ToolResult(success=False, data={}, error=f"Entity '{entity_name}' not found in knowledge graph")

        q = self.db.query(MemoryGraphEdge).filter(MemoryGraphEdge.source_id == node.id)
        if relation:
            if relation.startswith("COMPARED_BY_"):
                q = q.filter(MemoryGraphEdge.edge_type.like("COMPARED_BY_%"))
            else:
                q = q.filter(MemoryGraphEdge.edge_type == relation)
        edges = q.order_by(MemoryGraphEdge.weight.desc()).limit(limit).all()

        target_ids = [e.target_id for e in edges]
        label_map = {
            n.id: n.label
            for n in (
                self.db.query(MemoryGraphNode).filter(MemoryGraphNode.id.in_(target_ids)).all()
                if target_ids else []
            )
        }

        return ToolResult(success=True, data={
            "entity": entity_name,
            "related": [
                {"entity": label_map.get(e.target_id, e.target_id), "relation": e.edge_type, "weight": round(e.weight, 4)}
                for e in edges if e.target_id in label_map
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# 8. get_formulation
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class GetFormulationTool(BaseTool):
    name = "get_formulation"
    description = (
        "Find composite propellant or explosive formulations that contain a given ingredient. "
        "Use for questions like 'which propellants use RDX?' or 'show me AP-HTPB compositions'."
    )
    parameters = {
        "entity_name": {
            "type": "string",
            "description": "Ingredient to search for within formulations (e.g. 'AP', 'RDX', 'HTPB').",
        },
        "top_k": {"type": "integer", "description": "Max number of formulations to return.", "default": 10},
    }
    required = ["entity_name"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, entity_name: str, top_k: int = 10, **kwargs) -> ToolResult:
        try:
            from app.models.orm.formulation import Formulation, FormulationComponent

            comp_rows = (
                self.db.query(FormulationComponent)
                .filter(FormulationComponent.component_name == entity_name)
                .limit(top_k * 2)
                .all()
            )
            if not comp_rows:
                comp_rows = (
                    self.db.query(FormulationComponent)
                    .filter(FormulationComponent.component_name.ilike(f"%{entity_name}%"))
                    .limit(top_k * 2)
                    .all()
                )

            seen: set[str] = set()
            results = []
            for comp in comp_rows:
                fid = comp.formulation_id
                if fid in seen or len(results) >= top_k:
                    continue
                seen.add(fid)
                form = self.db.get(Formulation, fid)
                if not form:
                    continue
                all_comps = self.db.query(FormulationComponent).filter_by(formulation_id=fid).all()
                results.append({
                    "formulation_id": form.id,
                    "formulation_type": form.formulation_type,
                    "name": form.name,
                    "components": [
                        {"component": c.component_name, "mass_fraction_pct": c.mass_fraction, "role": c.role}
                        for c in sorted(all_comps, key=lambda x: -(x.mass_fraction or 0))
                    ],
                    "description": form.description,
                    "document_id": form.document_id,
                })

            return ToolResult(success=True, data={"entity": entity_name, "formulations": results})
        except Exception as e:
            return ToolResult(success=False, error=str(e), data={})


# ─────────────────────────────────────────────────────────────────────────────
# 9. agentic_search
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class AgenticSearchTool(BaseTool):
    name = "agentic_search"
    description = (
        "Deep navigational search: the LLM reads section outlines to navigate the document "
        "tree before fetching content. More accurate than search_memory for complex, "
        "multi-section questions where flat retrieval may miss adjacent context. "
        "Use when search_memory returns incomplete or fragmented evidence."
    )
    parameters = {
        "query": {"type": "string", "description": "Research question requiring deep document navigation."},
        "namespace": {"type": "string", "description": "Document collection to search.", "default": "default"},
    }
    required = ["query"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, query: str, namespace: str = "default", **kwargs) -> ToolResult:
        try:
            from app.services.agentic_retrieval import AgenticRetrievalService
            result = AgenticRetrievalService(self.db).retrieve(query, namespace)
            trace = [
                {
                    "step": t.step,
                    "phase": t.phase,
                    "doc_id": t.doc_id,
                    "observation": t.observation,
                    "decision": t.decision,
                    "elapsed_ms": t.elapsed_ms,
                }
                for t in result.decision_trace
            ]
            return ToolResult(success=True, data={
                "evidence_text": result.evidence_text,
                "referenced_chunks": result.referenced_chunks,
                "stop_reason": result.stop_reason,
                "decision_trace": trace,
                "doc_count": len(result.doc_trees),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))
