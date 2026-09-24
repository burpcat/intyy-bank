-include .env
export

VAR := $(or $(KVFCU_VAR_DIR),var)

.PHONY: up seed reset test

# One command: bank app on 127.0.0.1:8081 (internal) + chaos proxy on 127.0.0.1:8080 (public).
up: $(VAR)/seed.db
	@test -f $(VAR)/live.db || cp $(VAR)/seed.db $(VAR)/live.db
	@export KVFCU_PROXY_SECRET="$${KVFCU_PROXY_SECRET:-$$(openssl rand -hex 16)}"; \
	trap 'kill 0' EXIT INT TERM; \
	uv run flask --app bank.app run --host 127.0.0.1 --port 8081 & \
	uv run python -m chaos.proxy & \
	wait

$(VAR)/seed.db: seed/seed.py
	uv run python seed/seed.py

seed:
	uv run python seed/seed.py

# Works with or without test mode. Also clears all sessions (seed.db has none).
reset: $(VAR)/seed.db
	cp $(VAR)/seed.db $(VAR)/live.db

test:
	uv run pytest -q
