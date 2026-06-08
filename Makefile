# Squishy — the 2026 compression corpus + citable Squishy Score.
#
# This Makefile drives the canonical pipeline only. The corpus is real,
# acquired data (see scripts/scale-acquire-*.py and build/meta/LICENSE-MANIFEST.csv);
# nothing here is synthetic. End-to-end reproduction + verification is `make all`.

S3_BUCKET ?= squishy-corpus
AWS_VAULT ?=

.PHONY: help all properties edition board calculate site deploy publish coverage validate audit pii \
        baseline check test freeze

help:
	@echo "Squishy targets:"
	@echo "  make all                 — reproduce + verify end-to-end (scripts/run-all.sh)"
	@echo "  make properties          — measure intrinsic byte properties of the core"
	@echo "  make edition             — regenerate build/meta/edition.json (single source of truth)"
	@echo "  make board               — reference panel over the local core members"
	@echo "  make calculate CMD=\"zstd -19 -c\" [VERIFY='--verify --decompress \"zstd -dc\"']"
	@echo "                           — stream the FULL edition and compute the Squishy Score"
	@echo "  make site                — render the explorer + coverage map (build/site)"
	@echo "  make deploy              — build the site, push to S3, invalidate the CDN (live)"
	@echo "  make publish [ARGS=…]    — stream the corpus into S3, idempotently (--plan/--check/--force)"
	@echo "  make coverage            — print the 3-axis coverage summary"
	@echo "  make baseline / check    — write / diff the golden baseline"
	@echo "  make validate audit pii  — core validation, distribution audit, PII scan"
	@echo "  make freeze              — print the OWNER-only freeze + DOI commands"

# Full clean-room reproduction + verification against build/meta/baseline.json.
all:
	bash scripts/run-all.sh

properties:
	uv run python scripts/file-properties.py

edition:
	uv run python scripts/build-edition-manifest.py

board:
	uv run python scripts/board-live.py

# Stream the published corpus and compute a Squishy Score for any codec:
#   make calculate CMD="zstd -19 -c"
#   make calculate CMD="xz -9 -c" VERIFY="--verify --decompress 'xz -dc'"
calculate:
	uv run python scripts/squishy-calculate.py --cmd "$(CMD)" $(VERIFY)

site:
	uv run --with pyarrow --with pandas python scripts/build-provenance.py

# Build the site fresh, then push to S3 (origin only — never direct S3) and
# invalidate CloudFront so squishy.jackdanger.com updates immediately.
# Prefix the whole thing with your creds if you use aws-vault:
#   aws-vault exec personal -- make deploy
deploy: site
	bash scripts/deploy-site.sh

# Stream every edition member into S3, idempotently (skip-if-already-present-by-sha;
# acquire + verify + upload the rest, one file at a time so the 17 GB corpus never
# lands on disk all at once). Needs creds — prefix with your aws-vault:
#   aws-vault exec personal -- make publish
#   make publish ARGS=--plan          # offline preview, no AWS
#   aws-vault exec personal -- make publish ARGS=--check
publish:
	uv run --with pyarrow python scripts/publish-corpus.py $(ARGS)

coverage:
	uv run python scripts/coverage-summary.py

baseline:
	uv run python scripts/build-baseline.py

check:
	uv run python scripts/check-baseline.py

validate:
	uv run python scripts/validate-core.py

audit:
	uv run python scripts/audit-distribution.py --prefix draft

pii:
	uv run python scripts/pii-scan.py

test:
	uv run pytest -q -m "not slow"

freeze:
	@echo "OWNER (irreversible, after counsel sign-off):"
	@echo "  $(AWS_VAULT) bash scripts/freeze.sh $(S3_BUCKET) --confirm"
	@echo "  ZENODO_TOKEN=<fresh> uv run python scripts/zenodo-deposit.py"
