# Shortcuts. Each target is just the docker / python command written next to it.
.PHONY: setup up down reset ps logs test lint check format cov typecheck audit upgrade economics dagster sim sync db models

setup:          ## one-time: create .env, the local Python environment and the git hooks
	@test -f .env || cp .env.example .env
	uv sync --extra dev
	uv run pre-commit install

up:             ## build and start the whole stack
	docker compose up -d --build --remove-orphans

down:           ## stop the stack, keep data
	docker compose down

reset:          ## stop the stack and DELETE all data
	docker compose down -v

ps:             ## show service status
	docker compose ps

logs:           ## follow logs (make logs s=dagster for one service)
	docker compose logs -f $(s)

test:           ## unit tests
	uv run pytest -q

lint:
	uv run ruff check .

check:          ## run before every push: lint + format + tests (CI runs the same)
	uv run ruff check . && uv run ruff format --check . && uv run pytest -q

format:         ## auto-format the code
	uv run ruff format . && uv run ruff check --fix .

cov:            ## tests with a coverage report
	uv run pytest -q --cov --cov-report=term-missing:skip-covered

typecheck:      ## static type check (report only for now)
	uv run pyright

audit:          ## known vulnerabilities in the locked dependencies
	uv export --frozen --no-hashes --no-emit-project > requirements-locked.txt && \
	uvx pip-audit -r requirements-locked.txt --no-deps --disable-pip

upgrade:        ## upgrade Python dependencies within their pins, then review the uv.lock diff in a PR
	uv lock --upgrade && uv sync --extra dev

# ---- milestone 1: economics ----
economics:      ## build economics tables for every office into outputs/ (make economics o=solo_manhattan for one)
	uv run python -m sadhana.economics $(o)

dagster:        ## Dagster UI locally at http://localhost:3000 without Docker
	uv run dagster dev

# ---- milestone 2: simulator ----
sim:            ## synthetic patients for every office into outputs/<office>/sim (make sim o=solo_manhattan seed=7)
	uv run python -m sadhana.simulator $(o) --seed $(or $(seed),42)

# ---- milestone 3: data layer and sync ----
sync:           ## load make sim output into Postgres, one schema per office (make up first; make sync o=solo_manhattan)
	uv run python -m sadhana.sync $(o)

db:             ## open a SQL prompt on the local database
	docker compose exec postgres psql -U sadhana sadhana

# ---- milestone 5: nightly models ----
models:         ## train, test and score the models for every synced office (make sync first)
	uv run python -m sadhana.models $(o)
