# Payment Processing Service

Микросервис асинхронной обработки платежей: принимает запросы на оплату, обрабатывает их
через эмуляцию внешнего платёжного шлюза и уведомляет клиента о результате через webhook.

**Стек:** FastAPI + Pydantic v2, SQLAlchemy 2.0 (async), PostgreSQL, RabbitMQ (FastStream),
Alembic, Docker Compose.

## Статус

Проект в работе. Порядок: RFC → список тестов → слайсы (TDD, red → green → refactor).

- [ ] RFC — `docs/rfc/0001-payment-processing.md`
- [ ] Список тестов — `docs/test-list.md`
- [ ] Слайс 1. Домен
- [ ] Слайс 2. Персистентность + outbox
- [ ] Слайс 3. API
- [ ] Слайс 4. Outbox relay
- [ ] Слайс 5. Consumer
- [ ] Слайс 6. Webhook + retry
- [ ] Слайс 7. DLQ
- [ ] Слайс 8. Docker Compose + README

Раздел с запуском и примерами запросов появится в слайсе 8.
