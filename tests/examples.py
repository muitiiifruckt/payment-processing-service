"""Единственный источник примеров запросов: их же показывает README.

Иначе примеры в документации живут отдельной жизнью и расходятся с кодом
на первой же смене контракта.
"""

import json
from typing import Any

CREATE_REQUEST: dict[str, Any] = {
    "amount": "100.50",
    "currency": "RUB",
    "description": "Оплата заказа 42",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://localhost:8000/__sink__/webhook",
}


def as_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
