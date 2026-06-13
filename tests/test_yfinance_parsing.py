import pandas as pd
from trading.data.bars import Bar
from trading.data.yfinance_source import bars_from_dataframe


def test_bars_from_dataframe_maps_columns_and_dates():
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1000, 1200],
        },
        index=pd.to_datetime(["2026-06-10", "2026-06-11"]),
    )
    out = bars_from_dataframe(df)
    assert out == [
        Bar("2026-06-10", 100.0, 102.0, 99.0, 101.0, 1000),
        Bar("2026-06-11", 101.0, 103.0, 100.5, 102.5, 1200),
    ]


def test_bars_from_dataframe_empty():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    assert bars_from_dataframe(df) == []
