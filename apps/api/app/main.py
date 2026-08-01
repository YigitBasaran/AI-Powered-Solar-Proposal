"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError

logger = logging.getLogger("solarvis")

REQUEST_ID_HEADER = "X-Request-ID"


def _configure_logging(settings: Settings) -> None:
    """Set the level on `solarvis` itself, not only on the root logger.

    `basicConfig` sets the *root* level, and anything that later reconfigures
    logging takes that with it. Something does: running a migration inside
    start-up applies `alembic.ini`, whose `[logger_root]` is WARNING - so every
    `logger.info` under `solarvis` went silent after boot while `uvicorn.access`,
    which carries its own level, kept working. A container logged its banner and
    then nothing about what it was doing.

    Naming the level on the application's own logger makes it independent of
    whatever else touches root, which is where it should have been anyway.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    logging.getLogger("solarvis").setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _configure_logging(settings)

    cfg = settings.satellite_image_config
    logger.info(
        "solarVis API starting | env=%s maps=%s pvgis=%s fx=%s llm=%s",
        settings.app_env,
        settings.google_static_maps_base_url,
        # Not a mode - the endpoint, because that is the thing that varies and
        # the thing that decides whether an observation is proposal-grade.
        settings.pvgis_base_url,
        settings.fx_mode,
        settings.llm_provider,
    )
    logger.info(
        "case location | raw=%.8f,%.8f resolved=%.8f,%.8f",
        settings.case_raw_latitude,
        settings.case_raw_longitude,
        settings.case_resolved_latitude,
        settings.case_resolved_longitude,
    )
    logger.info(
        "source raster | %dx%d px @ zoom %d scale %d -> %.7f ground m/px (%.2f m span)",
        cfg.source_width_px,
        cfg.source_height_px,
        cfg.zoom,
        cfg.scale,
        cfg.ground_m_per_source_px,
        cfg.ground_span_m,
    )

    from app.db.session import init_db

    await init_db()
    yield
    logger.info("solarVis API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="solarVis AI API",
        version="0.1.0",
        description=(
            "Deterministic backend for the solarVis AI solar proposal flow. "
            "All engineering, currency and financial values are computed here; "
            "the language model never produces a number that reaches the domain."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_base_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("%s: %s (%s)", exc.code, exc.message, request_id)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload(request_id))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "The request could not be validated.",
                    "details": {"errors": exc.errors()},
                    "requestId": request_id,
                }
            },
        )

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
