"""
Read-only audit of the trading schema and the T-Bank token presence.

Part of the backend/app/db/check_* diagnostics family.
- No writes. No market calls. No secrets printed (token is reported as bool only).
- Invoked as: python -m app.db.db_audit_raw
- Prints a single JSON object to stdout.
"""
import json
import os

from app.db.db_manager import DBManager


def main() -> None:
    out = {"token_set": bool(os.getenv("TINVEST_TOKEN", "").strip())}
    try:
        db = DBManager()

        def q(sql):
            return db.select(sql).to_dataframe()

        freshness = {}
        for tbl, col in [
            ("candles_30min_raw", "created_at"),
            ("candles_30min_raw", "timestamp"),
            ("indicators", "timestamp"),
            ("signals", "timestamp"),
        ]:
            try:
                df = q(
                    'SELECT max("%s") AS m, count(*) AS c FROM trading.%s' % (col, tbl)
                )
                freshness["%s__max_%s" % (tbl, col)] = (
                    None if df.empty else str(df.iloc[0]["m"])
                )
                freshness["%s__count" % tbl] = (
                    None if df.empty else int(df.iloc[0]["c"])
                )
            except Exception as exc:
                freshness["%s__max_%s_err" % (tbl, col)] = str(exc)
        out["freshness"] = freshness

        cons = q(
            "SELECT tc.constraint_name, tc.constraint_type "
            "FROM information_schema.table_constraints tc "
            "WHERE tc.table_schema='trading' "
            "AND tc.table_name='candles_30min_raw'"
        )
        out["raw_constraints"] = [] if cons.empty else cons.to_dict("records")

        idx = q(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='trading' AND tablename='candles_30min_raw'"
        )
        out["raw_indexes"] = [] if idx.empty else idx.to_dict("records")

        out["status"] = "ok"
        try:
            db.close_pool()
        except Exception:
            pass
    except Exception as exc:
        out["status"] = "failed"
        out["error"] = str(exc)

    print(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
