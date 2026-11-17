PYTHON ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: help install run fake migrate migration test lint clean

help:
	@echo "install    — поставить зависимости в .venv"
	@echo "migrate    — применить миграции Alembic"
	@echo "run        — запустить приложение (http://127.0.0.1:8000)"
	@echo "fake       — запустить фейковый Keitaro (http://127.0.0.1:8001)"
	@echo "test       — прогнать тесты"
	@echo "migration  — сгенерировать миграцию: make migration m='add column'"

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

migrate:
	$(BIN)/alembic upgrade head

migration:
	$(BIN)/alembic revision --autogenerate -m "$(m)"

run:
	$(BIN)/uvicorn app.main:app --reload --port 8000

fake:
	$(BIN)/uvicorn tools.fake_keitaro.app:app --reload --port 8001

test:
	$(BIN)/python -m pytest

lint:
	$(BIN)/ruff check app tools tests

clean:
	rm -rf .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
