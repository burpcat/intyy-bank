-include .env
export

VAR := $(or $(KVFCU_VAR_DIR),var)

.PHONY: seed reset test

$(VAR)/seed.db: seed/seed.py
	uv run python seed/seed.py

seed:
	uv run python seed/seed.py

# Works with or without test mode. Also clears all sessions (seed.db has none).
reset: $(VAR)/seed.db
	cp $(VAR)/seed.db $(VAR)/live.db

test:
	uv run pytest -q
