from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, set):
        return [json_ready(item) for item in sorted(value, key=str)]

    item = getattr(value, "item", None)
    if callable(item):
        return json_ready(item())

    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()

    return str(value)
