.PHONY: test build run seed

test:
	ruff check backend tests
	pytest -q

build:
	cd frontend-user && npm ci && npm run build
	cd frontend-admin && npm ci && npm run build

run:
	python -m uvicorn backend.app.main:app --reload --port 10000

seed:
	python -m backend.app.cli seed-demo
