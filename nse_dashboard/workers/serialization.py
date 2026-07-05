from __future__ import annotations

from typing import Any

import pandas as pd


def serialize_histories(histories: dict[str, pd.DataFrame]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for symbol, frame in histories.items():
        clean = frame.dropna(subset=["Close", "Volume"])
        serialized[symbol] = [
            {
                "date": index.isoformat(),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
            for index, row in clean.iterrows()
        ]
    return serialized


def deserialize_histories(payload: dict[str, Any]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    for symbol, records in payload.items():
        frame = pd.DataFrame.from_records(records)
        if frame.empty:
            histories[symbol] = pd.DataFrame(columns=["Close", "Volume"])
            continue
        frame.index = pd.to_datetime(frame.pop("date"))
        histories[symbol] = frame.rename(columns={"close": "Close", "volume": "Volume"})
    return histories
