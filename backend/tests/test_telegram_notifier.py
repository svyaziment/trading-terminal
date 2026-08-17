import logging

from app.core.config_manager import TelegramConfig
from app.notifications.telegram_notifier import TelegramNotifier


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _config() -> TelegramConfig:
    return TelegramConfig(token="secret-token", chat_id="-100123")


def test_sends_markdown_position_message_without_exposing_token_in_payload() -> None:
    calls = []

    def sender(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    notifier = TelegramNotifier(_config(), request_sender=sender)

    delivered = notifier.notify_position_open(
        ticker="SBER",
        price=321.45,
        size_lots=2,
        lot_size=10,
        reason="market/base",
    )

    assert delivered is True
    assert calls[0][1]["json"]["parse_mode"] == "Markdown"
    assert calls[0][1]["json"]["chat_id"] == "-100123"
    assert "BUY" in calls[0][1]["json"]["text"]
    assert "secret-token" not in calls[0][1]["json"]["text"]


def test_rate_limit_keeps_attempts_at_least_one_second_apart() -> None:
    clock = FakeClock()
    notifier = TelegramNotifier(
        _config(),
        request_sender=lambda *args, **kwargs: FakeResponse(),
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    assert notifier.send_message("first") is True
    assert notifier.send_message("second") is True

    assert clock.sleeps == [1.0]


def test_delivery_error_is_logged_and_does_not_escape(caplog) -> None:
    def failing_sender(*args, **kwargs):
        raise TimeoutError("Telegram unavailable")

    notifier = TelegramNotifier(_config(), request_sender=failing_sender)

    with caplog.at_level(logging.WARNING):
        delivered = notifier.notify_critical(
            event="GAME OVER",
            details="Equity reached zero",
        )

    assert delivered is False
    assert "delivery failed" in caplog.text


def test_missing_credentials_disable_delivery() -> None:
    called = False

    def sender(*args, **kwargs):
        nonlocal called
        called = True
        return FakeResponse()

    notifier = TelegramNotifier(TelegramConfig(), request_sender=sender)

    assert notifier.send_message("ignored") is False
    assert called is False
