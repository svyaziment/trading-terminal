"""
Synchronous PostgreSQL manager.

This module is intended for analytics, ETL and utility workloads.
For FastAPI request handling, prefer async database access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values

from app.core.config_manager import load_settings, setup_logger


logger = setup_logger("DBManager")


class SelectResult(dict):
    """SELECT result with DataFrame conversion support."""

    def to_dataframe(self) -> pd.DataFrame:
        results = self["data"]
        columns = self["columns"]
        types_df = self["types_df"]

        df = pd.DataFrame(results, columns=columns)

        for col, target_type in zip(columns, types_df):
            try:
                if "datetime64" in target_type:
                    df[col] = pd.to_datetime(df[col], utc=("UTC" in target_type))
                elif target_type in {"int64", "int32", "int16"}:
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif target_type in {"float64", "float32"}:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif target_type == "bool":
                    df[col] = df[col].astype(bool)
                elif target_type == "string":
                    df[col] = df[col].astype(str)
            except (ValueError, TypeError) as exc:
                if "Cannot convert non-finite values" not in str(exc):
                    logger.warning(
                        "Conversion error for column %s type %s: %s",
                        col,
                        target_type,
                        exc,
                    )
                continue

        return df


class DBManager:
    """PostgreSQL manager with connection pooling."""

    PG_TYPE_OID_TO_NAME = {
        20: {"pg": "int8", "df": "int64"},
        21: {"pg": "int2", "df": "int16"},
        23: {"pg": "int4", "df": "int32"},
        1700: {"pg": "numeric", "df": "float64"},
        701: {"pg": "float8", "df": "float64"},
        700: {"pg": "float4", "df": "float32"},
        25: {"pg": "text", "df": "string"},
        1043: {"pg": "varchar", "df": "string"},
        1042: {"pg": "bpchar", "df": "string"},
        1082: {"pg": "date", "df": "datetime64[ns]"},
        1114: {"pg": "timestamp", "df": "datetime64[ns]"},
        1184: {"pg": "timestamptz", "df": "datetime64[ns, UTC]"},
        1186: {"pg": "interval", "df": "timedelta64[ns]"},
        16: {"pg": "bool", "df": "bool"},
    }

    _connection_pool: Optional[pool.ThreadedConnectionPool] = None

    def __init__(self) -> None:
        self.settings = load_settings()
        self._last_result: Optional[Dict[str, Any]] = None

        self.conn_params = {
            "host": self.settings.db.host,
            "database": self.settings.db.database,
            "user": self.settings.db.user,
            "password": self.settings.db.password,
            "port": self.settings.db.port,
            "client_encoding": "utf8",
            "options": "-c client_encoding=UTF8 -c lc_messages=C",
            "connect_timeout": 10,
        }

        if DBManager._connection_pool is None:
            try:
                DBManager._connection_pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=20,
                    **self.conn_params,
                )
                logger.info("PostgreSQL connection pool created")
            except Exception as exc:
                logger.error("Cannot create PostgreSQL connection pool: %s", exc)
                raise

    def _get_conn(self):
        if DBManager._connection_pool is None:
            raise RuntimeError("Connection pool is not initialized")
        return DBManager._connection_pool.getconn()

    def _release_conn(self, conn) -> None:
        if DBManager._connection_pool is not None:
            DBManager._connection_pool.putconn(conn)

    def close_pool(self) -> None:
        if DBManager._connection_pool is not None:
            DBManager._connection_pool.closeall()
            DBManager._connection_pool = None
            logger.info("PostgreSQL connection pool closed")

    def select(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> SelectResult:
        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                if print_query:
                    logger.info("SQL QUERY:\n%s", query)
                    if params:
                        logger.info("PARAMS: %s", params)

                cursor.execute(query, params)

                if cursor.description is None:
                    self._last_result = {
                        "data": [[cursor.rowcount]],
                        "columns": ["affected_rows"],
                        "types": ["int8"],
                        "types_df": ["int64"],
                    }
                    return SelectResult(self._last_result)

                results = cursor.fetchall()
                col_names = [desc[0] for desc in cursor.description]

                col_types = []
                col_types_df = []

                for desc in cursor.description:
                    type_info = self.PG_TYPE_OID_TO_NAME.get(
                        desc[1],
                        {"pg": "text", "df": "string"},
                    )
                    col_types.append(type_info["pg"])
                    col_types_df.append(type_info["df"])

                self._last_result = {
                    "data": results,
                    "columns": col_names,
                    "types": col_types,
                    "types_df": col_types_df,
                }

                return SelectResult(self._last_result)

        except Exception as exc:
            logger.error("SELECT error: %s", exc)
            raise
        finally:
            self._release_conn(conn)

    def execute(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> int:
        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                if print_query:
                    logger.info("SQL QUERY:\n%s", query)
                    if params:
                        logger.info("PARAMS: %s", params)

                cursor.execute(query, params)
                conn.commit()
                return cursor.rowcount

        except Exception as exc:
            conn.rollback()
            logger.error("EXECUTE error: %s", exc)
            raise
        finally:
            self._release_conn(conn)

    def create_table(
        self,
        table_name: str,
        columns: Dict[str, str],
        drop_if_exists: bool = False,
    ) -> None:
        if drop_if_exists:
            self.drop_table(table_name)

        columns = dict(columns)

        if "created_at" not in columns:
            columns["created_at"] = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"

        column_defs = ", ".join(
            [f"{column} {dtype}" for column, dtype in columns.items()]
        )

        query = f"CREATE TABLE IF NOT EXISTS {table_name} ({column_defs})"
        self.execute(query)
        logger.info("Table created: %s", table_name)

    def drop_table(self, table_name: str) -> None:
        query = f"DROP TABLE IF EXISTS {table_name} CASCADE"
        self.execute(query)
        logger.info("Table dropped: %s", table_name)

    def insert(
        self,
        table_name: str,
        data: Union[Dict[str, Any], pd.DataFrame, List[Any]],
        columns: Optional[List[str]] = None,
    ) -> None:
        if isinstance(data, pd.DataFrame):
            self.insert_with_schema(table_name, data)
            return

        if isinstance(data, dict):
            cols = list(data.keys())
            values = [tuple(data.values())]
        elif isinstance(data, list) and columns:
            cols = columns
            values = data
        else:
            raise ValueError("Unsupported data format")

        full_table_name = (
            table_name if "." in table_name else f"trading.{table_name}"
        )

        query = f"INSERT INTO {full_table_name} ({', '.join(cols)}) VALUES %s"

        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                execute_values(cursor, query, values)
                conn.commit()

            logger.info(
                "%s rows inserted into %s",
                len(values),
                full_table_name,
            )
        except Exception as exc:
            conn.rollback()
            logger.error("Insert error into %s: %s", full_table_name, exc)
            raise
        finally:
            self._release_conn(conn)

    def insert_with_schema(self, table_name: str, df: pd.DataFrame) -> None:
        if "." not in table_name:
            full_table_name = f"trading.{table_name}"
        else:
            full_table_name = table_name

        df = df.copy()

        if df.empty:
            logger.info("DataFrame is empty, nothing to insert into %s", full_table_name)
            return

        for col in df.columns:
            if pd.api.types.is_float_dtype(df[col]):
                df[col] = df[col].astype(float)
            elif pd.api.types.is_integer_dtype(df[col]):
                df[col] = df[col].astype(int)
            elif pd.api.types.is_bool_dtype(df[col]):
                df[col] = df[col].astype(bool)
            elif df[col].dtype == "object":
                df[col] = df[col].apply(
                    lambda x: x.item() if hasattr(x, "item") else x
                )

        cols = ", ".join(df.columns)
        values = [tuple(row) for row in df.itertuples(index=False, name=None)]

        query = f"INSERT INTO {full_table_name} ({cols}) VALUES %s"

        conn = self._get_conn()

        try:
            with conn.cursor() as cursor:
                execute_values(cursor, query, values)
                conn.commit()

            logger.info(
                "%s rows inserted into %s",
                len(values),
                full_table_name,
            )
        except Exception as exc:
            conn.rollback()
            logger.error("Insert error into %s: %s", full_table_name, exc)
            raise
        finally:
            self._release_conn(conn)

    def get_column_types_for_postgres(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, str]:
        pandas_to_postgresql = {
            "int64": "BIGINT",
            "float64": "DOUBLE PRECISION",
            "bool": "BOOLEAN",
            "object": "TEXT",
            "datetime64[ns]": "TIMESTAMP",
            "timedelta64[ns]": "INTERVAL",
            "int32": "INTEGER",
            "uint32": "INTEGER",
            "int16": "SMALLINT",
            "uint16": "SMALLINT",
            "int8": "SMALLINT",
            "uint8": "SMALLINT",
            "float32": "REAL",
            "string": "TEXT",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "time": "TIME",
        }

        return {
            col_name: pandas_to_postgresql.get(str(dtype), "TEXT")
            for col_name, dtype in df.dtypes.items()
        }

    def execute_from_file(
        self,
        file_path: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> int:
        query = Path(file_path).read_text(encoding="utf-8")
        return self.execute(query, params=params, print_query=print_query)

    def select_from_file(
        self,
        file_path: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
        print_query: bool = False,
    ) -> SelectResult:
        query = Path(file_path).read_text(encoding="utf-8")
        return self.select(query, params=params, print_query=print_query)
