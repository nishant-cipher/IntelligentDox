"""All persistence for processed documents goes through this repository.

No other module should issue raw SQLAlchemy queries against `DocumentRecord`.
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import DocumentRecord


class DocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, **fields) -> DocumentRecord:
        record = DocumentRecord(**fields)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get_latest_by_name(self, document_name: str) -> Optional[DocumentRecord]:
        stmt = (
            select(DocumentRecord)
            .where(DocumentRecord.document_name == document_name)
            .order_by(DocumentRecord.created_at.desc())
        )
        return self.db.execute(stmt).scalars().first()

    def list_all(
        self,
        document_type: Optional[str] = None,
        processing_status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[DocumentRecord]:
        stmt = select(DocumentRecord).order_by(DocumentRecord.created_at.desc())
        if document_type:
            stmt = stmt.where(DocumentRecord.document_type == document_type)
        if processing_status:
            stmt = stmt.where(DocumentRecord.processing_status == processing_status)
        if search:
            stmt = stmt.where(DocumentRecord.document_name.ilike(f"%{search}%"))
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.execute(stmt).scalars().all())

    def count_all(self) -> int:
        return len(self.db.execute(select(DocumentRecord)).scalars().all())

    def latest_per_document(self) -> List[DocumentRecord]:
        """One row per distinct document_name, the most recently created version."""
        all_records = self.list_all(limit=10_000)
        seen = set()
        latest: List[DocumentRecord] = []
        for record in all_records:  # already newest-first
            if record.document_name in seen:
                continue
            seen.add(record.document_name)
            latest.append(record)
        return latest
