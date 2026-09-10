"""SQLAlchemy ORM model for processed documents."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(16), nullable=False)

    file_type: Mapped[str] = mapped_column(String(128), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=True)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    is_readable: Mapped[bool] = mapped_column(Boolean, default=True)

    overall_confidence: Mapped[float] = mapped_column(nullable=True)

    file_validation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    extracted_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processing_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
