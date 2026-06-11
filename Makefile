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

# Web bind port — single source of truth shared by `web-up` and the website tunnel.
WEB_PORT ?= 8580

.PHONY: web-up
web-up: ## Serve the read-only API + viewer (manyagent.web; anon/public identity) on :8580
	@if [ ! -d web/viewer/build ]; then \
	  echo "▶ no web/viewer/build/ — serving the legacy fallback (web/app/index.html)."; \
	  echo "  run 'make web-build' for the SvelteKit viewer."; \
	fi
	@uv run python -m manyagent.web.server $(WEB_PORT)

.PHONY: web-build
web-build: ## Build the SvelteKit viewer (web/viewer/) for production
	@cd web/viewer && npm install --no-audit --no-fund && npm run build

.PHONY: web-dev
web-dev: ## Run the Vite dev server (web/viewer/), proxying to a running 'make web-up'
	@cd web/viewer && npm install --no-audit --no-fund && npm run dev

# ---- Cloudflare named tunnels (infra/cloudflared/) -------------------------
# Two independent tunnels expose the local stack on the formulacode.org zone
# (Package Structure & Workflow: the cloudflare analog; configs live in infra/):
#   swarms-web → swarms.formulacode.org    → 127.0.0.1:$(WEB_PORT)       (read-only viewer + API; make web-up)
#   swarms-db  → db-swarms.formulacode.org → 127.0.0.1:$(BANK_API_PORT)  (Supabase HTTP API / Bank; make bank-up)
# One-time per machine:    make tunnel-install && make tunnel-login
# Per tunnel (idempotent): make web-tunnel-create  / make db-tunnel-create
# Serve (foreground):      make web-tunnel-run     / make db-tunnel-run
# Tear down:               make web-tunnel-delete  / make db-tunnel-delete
# NOTE: the db host is a SINGLE-level subdomain (db-swarms, not db.swarms) on
# purpose — Cloudflare's free Universal SSL covers only one level, so a 2-level
# host (db.swarms.*) fails the TLS handshake. See docs/guide/remote-access.md.
CLOUDFLARED   ?= cloudflared
TUNNEL_DIR    ?= infra/cloudflared
ZONE          ?= formulacode.org
# BANK_API_PORT must match supabase/config.toml [api] port.
BANK_API_PORT ?= 54421

WEB_TUNNEL    ?= swarms-web
WEB_HOSTNAME  ?= swarms.$(ZONE)
WEB_SERVICE   ?= http://127.0.0.1:$(WEB_PORT)

DB_TUNNEL     ?= swarms-db
DB_HOSTNAME   ?= db-swarms.$(ZONE)
DB_SERVICE    ?= http://127.0.0.1:$(BANK_API_PORT)

.PHONY: tunnel-install
tunnel-install: ## Install the cloudflared binary if it is missing
	@if command -v $(CLOUDFLARED) >/dev/null 2>&1; then \
	  echo "✓ cloudflared present: $$($(CLOUDFLARED) --version 2>/dev/null | head -1)"; \
	elif command -v brew >/dev/null 2>&1; then \
	  echo "▶ installing cloudflared via Homebrew…"; brew install cloudflared; \
	else \
	  os=$$(uname -s | tr 'A-Z' 'a-z'); arch=$$(uname -m); \
	  case "$$arch" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; armv7l) arch=arm;; esac; \
	  url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-$$os-$$arch"; \
	  mkdir -p "$$HOME/.local/bin"; \
	  echo "▶ downloading $$url"; \
	  curl -fsSL "$$url" -o "$$HOME/.local/bin/cloudflared" && chmod +x "$$HOME/.local/bin/cloudflared" \
	    && echo "✓ installed → ~/.local/bin/cloudflared (ensure ~/.local/bin is on PATH)"; \
	fi

.PHONY: tunnel-login
tunnel-login: ## Authorize cloudflared for the formulacode.org zone (one-time, opens a browser)
	@command -v $(CLOUDFLARED) >/dev/null 2>&1 || { echo "✗ cloudflared not found — run 'make tunnel-install'"; exit 1; }
	@$(CLOUDFLARED) tunnel login

.PHONY: tunnel-list
tunnel-list: ## List the Cloudflare tunnels on this account
	@command -v $(CLOUDFLARED) >/dev/null 2>&1 || { echo "✗ cloudflared not found — run 'make tunnel-install'"; exit 1; }
	@$(CLOUDFLARED) tunnel list

# Generic engine for the web-/db- wrappers below (no `## ` → hidden from `make
# help`). The wrappers set NAME/HOSTNAME/SERVICE and delegate, so the create /
# run / delete logic lives in exactly one place.
.PHONY: tunnel-create
tunnel-create:
	@command -v $(CLOUDFLARED) >/dev/null 2>&1 || { echo "✗ cloudflared not found — run 'make tunnel-install'"; exit 1; }
	@test -f "$$HOME/.cloudflared/cert.pem" || { echo "✗ not authorized — run 'make tunnel-login' (browser; pick the $(ZONE) zone)"; exit 1; }
	@mkdir -p "$(TUNNEL_DIR)"
	@if $(CLOUDFLARED) tunnel list --output json | python3 -c "import sys,json; sys.exit(0 if any(t.get('name')=='$(NAME)' for t in json.load(sys.stdin)) else 1)"; then \
	  echo "✓ tunnel '$(NAME)' exists — refreshing its config"; \
	else \
	  echo "▶ creating tunnel '$(NAME)'…"; $(CLOUDFLARED) tunnel create "$(NAME)"; \
	fi
	@uuid=$$($(CLOUDFLARED) tunnel list --output json | python3 -c "import sys,json; print(next(t['id'] for t in json.load(sys.stdin) if t.get('name')=='$(NAME)'))"); \
	  printf 'tunnel: %s\ncredentials-file: %s/.cloudflared/%s.json\n\ningress:\n  - hostname: %s\n    service: %s\n  - service: http_status:404\n' \
	    "$$uuid" "$$HOME" "$$uuid" "$(HOSTNAME)" "$(SERVICE)" > "$(TUNNEL_DIR)/$(NAME).yml"; \
	  echo "✓ wrote $(TUNNEL_DIR)/$(NAME).yml  ($(HOSTNAME) → $(SERVICE))"
	@echo "▶ routing DNS $(HOSTNAME) → $(NAME)…"
	@$(CLOUDFLARED) tunnel route dns "$(NAME)" "$(HOSTNAME)" \
	  || echo "  ⚠ 'route dns' failed (CNAME may already exist) — verify $(HOSTNAME) in the Cloudflare DNS dashboard"

.PHONY: tunnel-run
tunnel-run:
	@test -f "$(TUNNEL_DIR)/$(NAME).yml" || { echo "✗ no $(TUNNEL_DIR)/$(NAME).yml — run the matching *-tunnel-create target first"; exit 1; }
	@echo "▶ running '$(NAME)' (foreground, Ctrl-C to stop). Upstream must be live: $(SERVICE)"
	@$(CLOUDFLARED) tunnel --config "$(TUNNEL_DIR)/$(NAME).yml" run

.PHONY: tunnel-delete
tunnel-delete:
	@command -v $(CLOUDFLARED) >/dev/null 2>&1 || { echo "✗ cloudflared not found"; exit 1; }
	@$(CLOUDFLARED) tunnel cleanup "$(NAME)" 2>/dev/null || true
	@$(CLOUDFLARED) tunnel delete "$(NAME)" 2>/dev/null || echo "  (tunnel '$(NAME)' not found / already deleted)"
	@rm -f "$(TUNNEL_DIR)/$(NAME).yml"
	@echo "✓ deleted '$(NAME)'. The $(HOSTNAME) DNS record is left in place — remove it in the Cloudflare dashboard if no longer needed."

.PHONY: web-tunnel-create
web-tunnel-create: ## Create + route the website tunnel (swarms.formulacode.org -> viewer :8580)
	@$(MAKE) --no-print-directory tunnel-create NAME="$(WEB_TUNNEL)" HOSTNAME="$(WEB_HOSTNAME)" SERVICE="$(WEB_SERVICE)"

.PHONY: web-tunnel-run
web-tunnel-run: ## Run the website tunnel in the foreground (start 'make web-up' first)
	@$(MAKE) --no-print-directory tunnel-run NAME="$(WEB_TUNNEL)" SERVICE="$(WEB_SERVICE)"

.PHONY: web-tunnel-delete
web-tunnel-delete: ## Delete the website tunnel (swarms.formulacode.org)
	@$(MAKE) --no-print-directory tunnel-delete NAME="$(WEB_TUNNEL)" HOSTNAME="$(WEB_HOSTNAME)"

.PHONY: db-tunnel-create
db-tunnel-create: ## Create + route the Bank tunnel (db-swarms.formulacode.org -> Supabase API :54421)
	@$(MAKE) --no-print-directory tunnel-create NAME="$(DB_TUNNEL)" HOSTNAME="$(DB_HOSTNAME)" SERVICE="$(DB_SERVICE)"

.PHONY: db-tunnel-run
db-tunnel-run: ## Run the Bank tunnel in the foreground (start 'make bank-up' first)
	@$(MAKE) --no-print-directory tunnel-run NAME="$(DB_TUNNEL)" SERVICE="$(DB_SERVICE)"

.PHONY: db-tunnel-delete
db-tunnel-delete: ## Delete the Bank tunnel (db-swarms.formulacode.org)
	@$(MAKE) --no-print-directory tunnel-delete NAME="$(DB_TUNNEL)" HOSTNAME="$(DB_HOSTNAME)"

.PHONY: model-proxy
model-proxy: ## (placeholder, deferred) LiteLLM proxy for the MANYAGENT_LLM_* fallback endpoint
	@echo "model-proxy deferred (hosted ops); named per Package Structure & Workflow"

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
