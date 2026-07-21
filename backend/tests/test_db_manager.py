from app.db.db_manager import DBManager, SelectResult


def test_select_result_to_dataframe() -> None:
    result = SelectResult(
        {
            "data": [
                (1, "SBER", 12.5),
                (2, "GAZP", 113.77),
            ],
            "columns": ["id", "ticker", "price"],
            "types": ["int4", "varchar", "numeric"],
            "types_df": ["int32", "string", "float64"],
        }
    )

    df = result.to_dataframe()

    assert len(df) == 2
    assert list(df.columns) == ["id", "ticker", "price"]
    assert df.loc[0, "ticker"] == "SBER"
    assert df.loc[1, "ticker"] == "GAZP"
    assert float(df.loc[0, "price"]) == 12.5


def test_db_manager_class_has_expected_methods() -> None:
    assert hasattr(DBManager, "select")
    assert hasattr(DBManager, "execute")
    assert hasattr(DBManager, "insert")
    assert hasattr(DBManager, "insert_with_schema")
    assert hasattr(DBManager, "create_table")
    assert hasattr(DBManager, "drop_table")
