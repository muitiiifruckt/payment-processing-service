import hashlib
import json
from decimal import Decimal
from typing import Any


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        # 100 и 100.00 — один и тот же запрос
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def request_hash(body: dict[str, Any]) -> str:
    """Хеш канонической формы: порядок ключей и запись суммы не должны давать 409."""
    canonical = json.dumps(
        _canonical(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
