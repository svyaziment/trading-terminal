from fastapi import FastAPI

from app.api.market_data import register_routes


def create_app() -> FastAPI:
    application = FastAPI(
        title="Trading Terminal API",
        version="0.1.0",
        description="Backend API for AI-assisted trading terminal",
    )

    register_routes(application)
    # task-052: background backtest endpoints (shared lock)
    from app.api.backtest_jobs import register_routes as register_backtest_routes
    register_backtest_routes(application)
    # task-079: levels backtest matrix endpoints (shared lock)
    from app.api.levels_backtest_jobs import register_routes as register_levels_backtest_routes
    register_levels_backtest_routes(application)
    # task-043: async data refresh endpoints
    from app.api.data_refresh import register_routes as register_refresh_routes
    register_refresh_routes(application)
    # task-041: async signal regeneration endpoints
    from app.api.signals_jobs import register_routes as register_jobs_routes
    register_jobs_routes(application)

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
