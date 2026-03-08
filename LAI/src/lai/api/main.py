"""FastAPI application — entrypoint.

Registers all domain routers, startup/shutdown lifecycle,
middleware, and health endpoints.
"""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lai.core.config import get_settings
from lai.core.exceptions import LAIError
from lai.core.logging import get_logger, setup_logging

logger = get_logger("lai.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    setup_logging()
    settings = get_settings()
    logger.info("Starting LAI %s (%s)", settings.app_version, settings.environment)

    # Initialize infrastructure
    from lai.infra.database import init_pool
    from lai.infra.redis import init_cache

    await init_pool()
    await init_cache()

    logger.info("LAI ready on port %d", settings.api.port)
    yield

    # Shutdown
    from lai.infra.database import close_pool
    from lai.infra.redis import close_cache

    await close_cache()
    await close_pool()
    logger.info("LAI shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="LAI — Legal AI Platform",
        version=settings.app_version,
        description="German Legal AI for Wind Energy Due Diligence",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID + logging middleware
    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        start = time.perf_counter()

        response = await call_next(request)

        duration = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s %d %.1fms",
            request.method, request.url.path, response.status_code, duration,
            extra={"request_id": request_id},
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # Exception handlers
    @app.exception_handler(LAIError)
    async def lai_error_handler(request: Request, exc: LAIError):
        logger.error("LAIError: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})

    # Health endpoint
    @app.get("/health")
    async def health():
        from lai.infra.database import check_health as db_health
        from lai.infra.redis import check_health as redis_health

        return {
            "status": "ok",
            "version": settings.app_version,
            "database": await db_health(),
            "redis": await redis_health(),
        }

    # Register domain routers
    from lai.auth.routes import router as auth_router
    from lai.documents.routes import router as documents_router
    from lai.search.routes import router as search_router

    app.include_router(search_router)
    app.include_router(documents_router)
    app.include_router(auth_router)

    return app


app = create_app()
