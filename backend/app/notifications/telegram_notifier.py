"""Best-effort Telegram notifications for paper trading events."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from app.core.config_manager import TelegramConfig, load_settings

logger = logging.getLogger(__name__)


def _escape_markdown(value: Any) -> str:
    """Escape dynamic values for Telegram's legacy Markdown parser."""
    text = str(value).replace("\\", "\\\\")
    for character in ("_", "*", "[", "`"):
        text = text.replace(character, f"\\{character}")
    return text


class TelegramNotifier:
    """Synchronous Bot API client that never propagates delivery failures."""

    def __init__(
        self,
        config: TelegramConfig | None = None,
        *,
        min_interval_sec: float = 1.0,
        timeout_sec: float = 5.0,
        request_sender: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or load_settings().telegram
        self.min_interval_sec = max(float(min_interval_sec), 1.0)
        self.timeout_sec = timeout_sec
        self._request_sender = request_sender
        self._clock = clock
        self._sleeper = sleeper
        self._last_attempt_at: float | None = None
        self._send_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.config.token and self.config.chat_id)

    def send_message(self, text: str) -> bool:
        """Send preformatted Markdown, returning False on any Telegram error."""
        if not self.enabled:
            return False

        try:
            with self._send_lock:
                now = self._clock()
                if self._last_attempt_at is not None:
                    wait_sec = self.min_interval_sec - (now - self._last_attempt_at)
                    if wait_sec > 0:
                        self._sleeper(wait_sec)
                self._last_attempt_at = self._clock()

                response = self._request_sender(
                    f"https://api.telegram.org/bot{self.config.token}/sendMessage",
                    json={
                        "chat_id": self.config.chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning(
                "Telegram notification delivery failed (%s)",
                type(exc).__name__,
            )
            return False

    def check_connection(self) -> bool:
        """Validate Bot API credentials without sending a chat message."""
        if not self.enabled:
            return False

        try:
            response = self._request_sender(
                f"https://api.telegram.org/bot{self.config.token}/getMe",
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            payload = response.json() if hasattr(response, "json") else {}
            return bool(payload.get("ok", True))
        except Exception as exc:
            logger.warning(
                "Telegram connection check failed (%s)",
                type(exc).__name__,
            )
            return False

    def notify_position_open(
        self,
        *,
        ticker: str,
        price: float,
        size_lots: int,
        lot_size: int,
        reason: str,
    ) -> bool:
        units = size_lots * lot_size
        return self.send_message(
            "📈 *Открыта paper-позиция*\n"
            f"*Тикер:* `{_escape_markdown(ticker)}`\n"
            "*Сделка:* `BUY`\n"
            f"*Цена:* `{price:.4f} RUB`\n"
            f"*Размер:* `{size_lots} лот. / {units} шт.`\n"
            f"*Причина:* `{_escape_markdown(reason)}`"
        )

    def notify_position_close(
        self,
        *,
        ticker: str,
        price: float,
        size_lots: int,
        lot_size: int,
        pnl_rub: float,
        pnl_pct: float,
        reason: str,
    ) -> bool:
        units = size_lots * lot_size
        icon = "🛑" if reason == "stop" else "✅" if reason == "take" else "📉"
        return self.send_message(
            f"{icon} *Закрыта paper-позиция*\n"
            f"*Тикер:* `{_escape_markdown(ticker)}`\n"
            "*Сделка:* `SELL`\n"
            f"*Цена:* `{price:.4f} RUB`\n"
            f"*Размер:* `{size_lots} лот. / {units} шт.`\n"
            f"*PnL:* `{pnl_rub:+.2f} RUB ({pnl_pct:+.2f}%)`\n"
            f"*Причина:* `{_escape_markdown(reason)}`"
        )

    def notify_critical(self, *, event: str, details: str) -> bool:
        return self.send_message(
            "🚨 *КРИТИЧЕСКОЕ СОБЫТИЕ*\n"
            f"*Событие:* `{_escape_markdown(event)}`\n"
            f"*Детали:* {_escape_markdown(details)}"
        )
