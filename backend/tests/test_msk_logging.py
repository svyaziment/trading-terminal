import logging
from datetime import datetime, timezone

from app.core.msk_logging import MSK, MskFormatter, install_msk_log_timestamps, msk_converter


def test_msk_converter_is_three_hours_ahead_of_utc() -> None:
    utc = datetime(2026, 8, 20, 17, 4, 25, tzinfo=timezone.utc)
    converted = msk_converter(utc.timestamp())
    assert (converted.tm_hour, converted.tm_min, converted.tm_sec) == (20, 4, 25)


def test_msk_formatter_asctime_uses_msk() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    record.created = datetime(2026, 8, 20, 17, 4, 25, tzinfo=timezone.utc).timestamp()
    record.msecs = 113
    formatted = MskFormatter().formatTime(record)
    assert formatted.startswith("2026-08-20 20:04:25")


def test_install_patches_standard_formatter() -> None:
    install_msk_log_timestamps()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    record.created = datetime(2026, 8, 20, 17, 4, 25, tzinfo=timezone.utc).timestamp()
    formatted = logging.Formatter("%(asctime)s").format(record)
    expected = datetime.fromtimestamp(record.created, timezone.utc).astimezone(MSK)
    assert formatted.startswith(expected.strftime("%Y-%m-%d %H:%M:%S"))
