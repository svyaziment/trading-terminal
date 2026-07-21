import asyncio
import os
import re
import sys

import asyncpg


TABLES_TO_CHECK = [
    "instruments",
    "candles_aggregated",
    "signals",
    "top_stocks_by_volume",
]


def mask_url(value: str) -> str:
    if not value:
        return value

    return re.sub(
        r"(://[^:/@]+:)([^@]+)(@)",
        r"\1***\3",
        value,
    )


def get_dsn() -> str:
    url = os.getenv("MARKET_DATA_DATABASE_URL", "").strip()

    if not url:
        return ""

    return url.replace("postgresql+asyncpg://", "postgresql://")


def get_schema() -> str:
    schema = os.getenv("MARKET_DATA_SCHEMA", "trading").strip()

    if not schema:
        schema = "trading"

    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", schema):
        raise ValueError("Invalid MARKET_DATA_SCHEMA name")

    return schema


def validate_table_name(table_name: str) -> None:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
        raise ValueError(f"Invalid table name: {table_name}")


async def fetch_count(connection, schema: str, table_name: str) -> int:
    validate_table_name(table_name)

    query = f'select count(*) from "{schema}"."{table_name}"'
    return await connection.fetchval(query)


async def main() -> None:
    result = {
        "status": "failed",
        "error": "",
        "schema": "",
        "instruments_count": "",
        "candles_count": "",
        "signals_count": "",
        "top_stocks_count": "",
    }

    try:
        dsn = get_dsn()
        schema = get_schema()
        result["schema"] = schema

        if not dsn:
            result["error"] = "MARKET_DATA_DATABASE_URL is empty. Fill .env and rerun."
        else:
            connection = await asyncpg.connect(dsn=dsn, timeout=15)

            try:
                result["instruments_count"] = str(
                    await fetch_count(connection, schema, "instruments")
                )
                result["candles_count"] = str(
                    await fetch_count(connection, schema, "candles_aggregated")
                )
                result["signals_count"] = str(
                    await fetch_count(connection, schema, "signals")
                )
                result["top_stocks_count"] = str(
                    await fetch_count(connection, schema, "top_stocks_by_volume")
                )

                result["status"] = "success"
            finally:
                await connection.close()

    except Exception as exc:
        result["status"] = "failed"
        result["error"] = mask_url(str(exc).replace("\n", " "))

    print("EXTERNAL_DB_STATUS=" + result["status"])
    print("EXTERNAL_DB_SCHEMA=" + result["schema"])
    print("INSTRUMENTS_COUNT=" + result["instruments_count"])
    print("CANDLES_COUNT=" + result["candles_count"])
    print("SIGNALS_COUNT=" + result["signals_count"])
    print("TOP_STOCKS_COUNT=" + result["top_stocks_count"])
    print("ERROR_MESSAGE=" + result["error"])

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
