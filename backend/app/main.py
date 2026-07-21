from fastapi import FastAPI

from app.api.market_data import register_routes


def create_app() -> FastAPI:
    application = FastAPI(
        title="Trading Terminal API",
        version="0.1.0",
        description="Backend API for AI-assisted trading terminal",
    )

    register_routes(application)

    @application.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "service": "backend",
            "version": "0.1.0",
        }

    return application


app = create_app()

_required_route = "/api/instruments"
_current_routes = {getattr(route, "path", "") for route in app.routes}

if _required_route not in _current_routes:
    raise RuntimeError(
        "Market data routes were not registered. "
        f"Routes: {sorted(_current_routes)}"
    )
