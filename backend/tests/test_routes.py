from app.main import app


def test_api_routes_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/health" in paths
    assert "/api/instruments" in paths
    assert "/api/candles" in paths
    assert "/api/signals" in paths
    assert "/api/top-stocks-by-volume" in paths
