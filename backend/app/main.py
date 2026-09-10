"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import documents, health
from app.core.config import get_settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Application startup complete | environment=%s", settings.ENVIRONMENT)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Intelligent Document Extraction, Validation & API Platform - "
    "upload invoices and financial statements, get back structured, evidence-grounded JSON.",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.CORS_ORIGINS != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning("Controlled error | code=%s | path=%s | message=%s", exc.code, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning("Request validation error | path=%s | detail=%s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "The request could not be validated.", "detail": exc.errors()}},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception | path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred while processing the request."}},
    )


app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(documents.router, prefix=settings.API_PREFIX)


@app.get("/")
def root():
    return {
        "name": settings.APP_NAME,
        "docs": "/docs",
        "health": f"{settings.API_PREFIX}/health",
    }
