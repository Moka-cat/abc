from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.schemas.entity import EntityCardResponse, EvidenceResponse
from app.services.entity import EntityService

router = APIRouter()


@router.get("", response_model=list[EntityCardResponse])
def list_entities(
    namespace: str = "default",
    entity_type: str | None = None,
    db: Session = Depends(get_db),
):
    service = EntityService(db)
    return service.list_entities(namespace, entity_type)


@router.get("/{name}", response_model=EntityCardResponse)
def get_entity(name: str, db: Session = Depends(get_db)):
    service = EntityService(db)
    card = service.get_entity_card(name)
    if not card:
        raise HTTPException(status_code=404, detail=f"Entity not found: {name}")
    return card
