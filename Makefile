.PHONY: install
install: ## Install the virtual environment and install the pre-commit hooks
	@echo "Creating virtual environment using uv"
	@uv sync --all-extras
	@uv pip install -e .
	@uv run pre-commit install

.PHONY: check
check: ## Run code quality tools.
	@echo "Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "Linting code: Running pre-commit"
	@uv run pre-commit run -a
	@echo "Static type checking: Running mypy"
	@uv run mypy
	@echo "Checking for obsolete dependencies: Running deptry"
	@uv run deptry src

.PHONY: test
test: ## Test the code with pytest
	@echo "Testing code: Running pytest"
	@uv run python -m pytest --cov --cov-config=pyproject.toml --cov-report=xml

.PHONY: build
build: clean-build ## Build wheel file
	@echo "Creating wheel file"
	@uvx --from build pyproject-build --installer uv

.PHONY: clean-build
clean-build: ## Clean build artifacts
	@echo "Removing build artifacts"
	@uv run python -c "import shutil; import os; shutil.rmtree('dist') if os.path.exists('dist') else None"

# Pin the Supabase CLI: bare `npx supabase` resolves "latest" and breaks when
# npm lacks that exact version. Override with `make bank-up SUPABASE=...`.
SUPABASE ?= npx --yes supabase@2.100.0

.PHONY: bank-up
bank-up: ## Start the local Bank (Supabase) instance
	@$(SUPABASE) start

.PHONY: bank-down
bank-down: ## Stop the local Bank (Supabase) instance
	@$(SUPABASE) stop

.PHONY: bank-status
bank-status: ## Show Bank (Supabase) service status and URLs
	@$(SUPABASE) status

.PHONY: bank-migrate
bank-migrate: ## Apply supabase/migrations/ to the local Bank instance
	@$(SUPABASE) migration up

.PHONY: bank-reset
bank-reset: ## Drop + re-apply all migrations from empty (local dev only)
	@$(SUPABASE) db reset

.PHONY: web-up
web-up: ## Serve the read-only API + viewer (oma.web; anon/public identity) on :8580
	@if [ ! -d web/viewer/build ]; then \
	  echo "▶ no web/viewer/build/ — serving the legacy fallback (web/app/index.html)."; \
	  echo "  run 'make web-build' for the SvelteKit viewer."; \
	fi
	@uv run python -m oma.web.server 8580

.PHONY: web-build
web-build: ## Build the SvelteKit viewer (web/viewer/) for production
	@cd web/viewer && npm install --no-audit --no-fund && npm run build

.PHONY: web-dev
web-dev: ## Run the Vite dev server (web/viewer/), proxying to a running 'make web-up'
	@cd web/viewer && npm install --no-audit --no-fund && npm run dev

.PHONY: api-tunnel
api-tunnel: ## (placeholder, deferred) Expose the read-only API via Cloudflare Tunnel
	@echo "api-tunnel deferred (hosted ops); named per Package Structure & Workflow"

.PHONY: model-proxy
model-proxy: ## (placeholder, deferred) LiteLLM proxy for the OMA_LLM_* fallback endpoint
	@echo "model-proxy deferred (hosted ops); named per Package Structure & Workflow"

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
