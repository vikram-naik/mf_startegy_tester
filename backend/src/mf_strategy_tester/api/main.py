from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mf_strategy_tester import __version__
from mf_strategy_tester.api.routes import data, health, ingestion, strategies


def create_app() -> FastAPI:
    application = FastAPI(
        title="MF Strategy Tester API",
        version=__version__,
        description="Local research API for versioned mutual-fund strategies and backtests.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health.router, prefix="/api/v1")
    application.include_router(data.router, prefix="/api/v1")
    application.include_router(ingestion.router, prefix="/api/v1")
    application.include_router(strategies.router, prefix="/api/v1")
    return application


app = create_app()
