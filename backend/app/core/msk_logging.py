"""Force logging asctime to MSK (UTC+3) without changing process TZ.

Container localtime is UTC. Trading timestamps are naive MSK. Log lines must
match the session clock. Do not set TZ=Europe/Moscow: that would change
datetime.now() and mix UTC/MSK in trading logic.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def msk_converter(seconds: float):
    """logging.Formatter.converter compatible with time.localtime."""
    return datetime.fromtimestamp(seconds, timezone.utc).astimezone(MSK).timetuple()


def install_msk_log_timestamps() -> None:
    """Patch all logging formatters to render asctime in MSK."""
    # A plain function on the class would bind as a method and receive `self`.
    logging.Formatter.converter = staticmethod(msk_converter)


def configure_msk_logging(
    level: int = logging.INFO,
    stream=None,
) -> None:
    """Install MSK timestamps and configure the root logger for background processes."""
    install_msk_log_timestamps()
    logging.basicConfig(
        level=level,
        stream=stream or sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


class MskFormatter(logging.Formatter):
    """Explicit MSK formatter when a named logger installs its own handler."""

    converter = staticmethod(msk_converter)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, timezone.utc).astimezone(MSK)
        if datefmt:
            return dt.strftime(datefmt)
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')},{int(record.msecs):03d}"
