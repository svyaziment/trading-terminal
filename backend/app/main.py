from app.core.msk_logging import install_msk_log_timestamps
from fastapi import FastAPI

from app.api.market_data import register_routes

install_msk_log_timestamps()


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
    # task-105: strategy storage + backtest API
    from app.api.strategy_jobs import register_routes as register_strategy_routes
    register_strategy_routes(application)
    # task-125: paper trading monitoring API
    from app.api.paper_trading_jobs import register_routes as register_paper_trading_routes
    register_paper_trading_routes(application)
    # issue-65: Telegram connectivity status for live monitoring
    from app.api.notifications import register_routes as register_notification_routes
    register_notification_routes(application)
    from app.api.live_trading_jobs import register_routes as register_live_trading_routes
    register_live_trading_routes(application)
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
