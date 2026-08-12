import json
from sqlalchemy.orm import Session
from app.models.orm.domain import DomainEntity, EntityAlias, PropertyValue
from app.models.orm.evidence import EvidenceLink
from app.models.schemas.entity import EntityCardResponse, EntityPropertyResponse, EvidenceResponse


class EntityService:
    def __init__(self, db: Session):
        self.db = db

    def get_entity_card(self, name: str) -> EntityCardResponse | None:
        # 先查 canonical_name，再查 alias
        entity = self.db.query(DomainEntity).filter_by(canonical_name=name).first()
        if not entity:
            alias_row = self.db.query(EntityAlias).filter_by(alias=name).first()
            if alias_row:
                entity = alias_row.entity
        if not entity:
            return None

        aliases = [a.alias for a in entity.aliases]

        props = []
        for pv in entity.properties:
            ev = self.db.query(EvidenceLink).filter_by(property_value_id=pv.id).first()
            props.append(EntityPropertyResponse(
                property=pv.property_name,
                value_text=pv.value_text,
                value_numeric=pv.value_numeric,
                unit=pv.unit,
                condition=json.loads(pv.condition_json) if pv.condition_json else None,
                evidence_id=ev.id if ev else None,
            ))

        return EntityCardResponse(
            id=entity.id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            aliases=aliases,
            properties=props,
        )

    def get_evidence(self, evidence_id: str) -> EvidenceResponse | None:
        ev = self.db.query(EvidenceLink).filter_by(id=evidence_id).first()
        if not ev:
            return None

        # Enrich with sentence-level context from the parent chunk
        context_before = ""
        context_after = ""
        highlighted = ""

        if ev.chunk_id and ev.quote:
            from app.models.orm.document import DocumentChunk
            chunk = self.db.query(DocumentChunk).filter_by(id=ev.chunk_id).first()
            if chunk and chunk.chunk_text:
                text = chunk.chunk_text
                quote = ev.quote.strip()

                # Use stored span or find the quote in the chunk text
                start = ev.span_start
                if start is None or start < 0:
                    start = text.find(quote)
                end = ev.span_end
                if end is None or end <= 0 or end <= (start or 0):
                    end = (start or 0) + len(quote) if start is not None and start >= 0 else -1

                if start is not None and start >= 0 and end > start:
                    # Extract one sentence before and after
                    before_text = text[:start]
                    after_text = text[end:]

                    # Sentence boundary: last sentence-ending punctuation before the quote
                    for punct in ["。", ".", "！", "!", "？", "?"]:
                        idx = before_text.rfind(punct)
                        if idx >= 0:
                            before_text = before_text[idx + 1:].strip()
                            break
                    else:
                        before_text = before_text[-100:].strip()  # cap at 100 chars

                    # First sentence after the quote
                    for punct in ["。", ".", "！", "!", "？", "?"]:
                        idx = after_text.find(punct)
                        if idx >= 0:
                            after_text = after_text[:idx + 1].strip()
                            break
                    else:
                        after_text = after_text[:100].strip()

                    context_before = before_text
                    context_after = after_text

                    # Build highlighted markdown: …before **[quote]** after…
                    parts = []
                    if before_text:
                        parts.append(f"…{before_text}")
                    parts.append(f" **「{quote}」** ")
                    if after_text:
                        parts.append(f"{after_text}…")
                    highlighted = "".join(parts).strip()

        return EvidenceResponse(
            id=ev.id,
            document_id=ev.document_id,
            section_path=ev.section_path,
            page=ev.page,
            chunk_id=ev.chunk_id,
            quote=ev.quote,
            span_start=ev.span_start,
            span_end=ev.span_end,
            context_before=context_before,
            context_after=context_after,
            highlighted=highlighted,
        )

    def list_entities(
        self, namespace: str = "default", entity_type: str | None = None
    ) -> list[EntityCardResponse]:
        q = self.db.query(DomainEntity).filter_by(namespace=namespace)
        if entity_type:
            q = q.filter_by(entity_type=entity_type)
        entities = q.all()
        results = []
        for e in entities:
            card = self.get_entity_card(e.canonical_name)
            if card:
                results.append(card)
        return results
