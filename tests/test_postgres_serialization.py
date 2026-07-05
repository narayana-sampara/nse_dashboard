from __future__ import annotations

import json

import numpy as np

from nse_dashboard.infrastructure.postgres import _ranked_prediction_rows


def test_ranked_prediction_rows_normalizes_numpy_scalars_for_json() -> None:
    rows = _ranked_prediction_rows(
        {
            "sectors": [
                {
                    "name": "Technology",
                    "buys": [
                        {
                            "symbol": "TCS.NS",
                            "sector": "Technology",
                            "buy_rank": np.int64(1),
                            "price": np.float64(3900.5),
                            "predicted_5d_return_pct": 2.1,
                            "target_probability": 0.7,
                            "ranking_score": 65.0,
                            "risk_score": 18.0,
                            "indicator": {
                                "signal": "BUY",
                                "conditions": {"macd_above_zero": np.bool_(True)},
                            },
                        }
                    ],
                }
            ],
        }
    )

    assert rows[0]["sector_rank"] == 1
    assert rows[0]["indicator"]["conditions"]["macd_above_zero"] is True
    json.dumps(rows[0])
