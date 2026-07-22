from app.broker.data_loader import DataLoader


def main() -> None:
    print("IMPORT_OK")

    loader = DataLoader()

    try:
        df = loader.fetch_candles_by_figi(
            figi="BBG004730ZJ9",
            ticker="VTBR",
            days=5,
            interval_str="30min",
        )

        print("ROWS=" + str(len(df)))

        if not df.empty:
            print("FIRST_TIME=" + str(df.iloc[0].get("time")))
            print("LAST_TIME=" + str(df.iloc[-1].get("time")))

    except Exception as exc:
        print("ROWS=0")
        print("ERROR_MESSAGE=" + str(exc))


if __name__ == "__main__":
    main()
