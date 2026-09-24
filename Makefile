-include .env
export

.PHONY: seed test

seed:
	uv run python seed/seed.py

test:
	uv run pytest -q
