UV ?= uv

# Docker на этой машине живёт в WSL, а не в Windows. Поэтому всё, чему нужен
# контейнер, гоняется оттуда; окружение вынесено из проекта, иначе .venv
# для Windows и для Linux затирают друг друга в одной папке.
WSL_PROJECT ?= /mnt/c/Users/aayza/OneDrive/Документы/cursor_projects/work/payment_mcrsrv
WSL_RUN = wsl.exe -d Ubuntu -- bash -lc 'export PATH="$$HOME/.local/bin:$$PATH"; \
	export UV_PROJECT_ENVIRONMENT="$$HOME/.venvs/payment"; \
	cd "$(WSL_PROJECT)" &&

.PHONY: install test test-integration test-e2e test-all lint fmt typecheck

install:
	$(UV) sync

## Быстрый набор для цикла red → green: без I/O, должен укладываться в секунду
test:
	$(UV) run pytest -m "not integration and not e2e"

test-integration:
	$(WSL_RUN) uv run pytest -m integration'

test-e2e:
	$(WSL_RUN) uv run pytest -m e2e'

test-all:
	$(WSL_RUN) uv run pytest'

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

fmt:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck:
	$(UV) run mypy app
