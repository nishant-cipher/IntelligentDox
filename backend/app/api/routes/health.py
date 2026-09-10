"""Health check endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.document import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    ocr_status = "unavailable"
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        ocr_status = "healthy"
    except Exception:
        ocr_status = "unavailable"

    overall = "healthy" if db_status == "healthy" else "degraded"
    return {"status": overall, "database": db_status, "ocr_engine": ocr_status, "version": "1.0.0"}
