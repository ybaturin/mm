# Makefile — common operations
.PHONY: test sim run backup restore up

test:
	uv run pytest -q

sim:
	uv run python -m trading.orchestrator.simulate --days 30

run:
	uv run python -m trading.run

# Portable track record: the SQLite file IS the state. Back it up before moving hosts.
backup:
	@cp $${DB_PATH:-data/trading.db} backup-$$(date +%Y%m%d-%H%M%S).db && echo "backed up"

restore:
	@test -n "$(FROM)" || (echo "usage: make restore FROM=backup-XXXX.db" && exit 1)
	@cp "$(FROM)" $${DB_PATH:-data/trading.db} && echo "restored from $(FROM)"

up:
	docker compose run --rm app
