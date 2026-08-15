"""Обменники и очереди. Всё в одном месте — это отдельный критерий оценки."""

from faststream.rabbit import ExchangeType, RabbitExchange, RabbitQueue

from app.application.policy import RETRY_QUEUE_DELAYS

PAYMENTS_EXCHANGE = "payments"
DLX_EXCHANGE = "payments.dlx"

NEW_KEY = "payments.new"
DLQ_KEY = "payments.new.dlq"

payments_exchange = RabbitExchange(PAYMENTS_EXCHANGE, type=ExchangeType.DIRECT, durable=True)
dlx_exchange = RabbitExchange(DLX_EXCHANGE, type=ExchangeType.DIRECT, durable=True)

payments_new = RabbitQueue(
    NEW_KEY,
    durable=True,
    routing_key=NEW_KEY,
    arguments={
        # страховочная сетка: непойманное исключение должно попасть в DLQ,
        # а не исчезнуть
        "x-dead-letter-exchange": DLX_EXCHANGE,
        "x-dead-letter-routing-key": DLQ_KEY,
    },
)


def retry_key(attempt: int) -> str:
    return f"{NEW_KEY}.retry.{attempt}"


def retry_queue(attempt: int) -> RabbitQueue:
    return RabbitQueue(
        retry_key(attempt),
        durable=True,
        routing_key=retry_key(attempt),
        arguments={
            "x-message-ttl": RETRY_QUEUE_DELAYS[attempt - 1] * 1000,
            "x-dead-letter-exchange": PAYMENTS_EXCHANGE,
            # без явного ключа брокер сохранит имя retry-очереди, сообщение
            # станет немаршрутизируемым и будет уничтожено без следа в логах
            "x-dead-letter-routing-key": NEW_KEY,
        },
    )


retry_queues = [retry_queue(attempt) for attempt in range(1, len(RETRY_QUEUE_DELAYS) + 1)]

dead_letter_queue = RabbitQueue(DLQ_KEY, durable=True, routing_key=DLQ_KEY)

ALL_EXCHANGES = [payments_exchange, dlx_exchange]

#: Объявления мало: без привязки очередь не получит ни одного сообщения,
#: а публикация уйдёт в никуда
BINDINGS = [
    (payments_new, payments_exchange),
    *[(queue, payments_exchange) for queue in retry_queues],
    (dead_letter_queue, dlx_exchange),
]
