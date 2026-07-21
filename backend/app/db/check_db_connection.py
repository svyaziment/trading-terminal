import os
import re
import sys

from app.db.db_manager import DBManager


def mask_text(value: str) -> str:
    if not value:
        return value

    value = re.sub(
        r"(password\s*=\s*)([^,\s]+)",
        r"\1***",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"(://[^:/@]+:)([^@]+)(@)",
        r"\1***\3",
        value,
    )

    return value


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


def get_count(db: DBManager, schema: str, table_name: str) -> int:
    validate_table_name(table_name)

    query = f'select count(*) as cnt from "{schema}"."{table_name}"'
    result = db.select(query)

    return int(result["data"][0][0])


def main() -> None:
    status = "failed"
    error_message = ""
    schema = ""
    ok_value = ""
    instruments_count = ""
    candles_count = ""
    signals_count = ""
    top_stocks_count = ""

    try:
        schema = get_schema()
        db = DBManager()

        try:
            ok_result = db.select("select 1 as ok")
            ok_value = str(ok_result["data"][0][0])

            instruments_count = str(get_count(db, schema, "instruments"))
            candles_count = str(get_count(db, schema, "candles_aggregated"))
            signals_count = str(get_count(db, schema, "signals"))
            top_stocks_count = str(get_count(db, schema, "top_stocks_by_volume"))

            status = "success"
        finally:
            db.close_pool()

    except Exception as exc:
        status = "failed"
        error_message = mask_text(str(exc).replace("\n", " "))

    print("DB_CHECK_STATUS=" + status)
    print("DB_SCHEMA=" + schema)
    print("DB_OK=" + ok_value)
    print("INSTRUMENTS_COUNT=" + instruments_count)
    print("CANDLES_COUNT=" + candles_count)
    print("SIGNALS_COUNT=" + signals_count)
    print("TOP_STOCKS_COUNT=" + top_stocks_count)
    print("ERROR_MESSAGE=" + error_message)

    sys.exit(0)


if __name__ == "__main__":
    main()
