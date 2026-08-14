UV ?= uv

.PHONY: install test test-all lint fmt typecheck

install:
	$(UV) sync

## Быстрый набор для цикла red → green: без I/O, должен укладываться в секунду
test:
	$(UV) run pytest -m "not integration and not e2e"

test-all:
	$(UV) run pytest

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

fmt:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:
	$(UV) run mypy app
