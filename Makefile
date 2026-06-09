# Squishy — the 2026 compression corpus + citable Squishy Score.
#
# This Makefile drives the canonical pipeline only. The corpus is real,
# acquired data (see scripts/scale-acquire-*.py and build/meta/LICENSE-MANIFEST.csv);
# nothing here is synthetic. End-to-end reproduction + verification is `make all`.

S3_BUCKET ?= squishy-corpus
AWS_VAULT ?=

.PHONY: help all properties edition board calculate map site deploy publish mint release coverage validate audit pii \
        baseline check test freeze

help:
	@echo "Squishy targets:"
	@echo "  make all                 — reproduce + verify end-to-end (scripts/run-all.sh)"
	@echo "  make properties          — measure intrinsic byte properties of the core"
	@echo "  make edition             — regenerate build/meta/edition.json (single source of truth)"
	@echo "  make board               — reference panel over the local core members"
	@echo "  make calculate CMD=\"zstd -19 -c\" [VERIFY='--verify --decompress \"zstd -dc\"']"
	@echo "                           — stream the FULL edition and compute the Squishy Score"
	@echo "  make map                 — render the static coverage map (build/meta/coverage-map.svg)"
	@echo "  make site                — render the explorer + coverage map (build/site)"
	@echo "  make deploy              — build the site, push to S3, invalidate the CDN (live)"
	@echo "  make publish [ARGS=…]    — stream the corpus into S3 working prefix (--plan/--check/--force)"
	@echo "  make mint                — seed the source-of-record (source/) for MINTED members"
	@echo "  make release EDITION=v1.0 — freeze into EDITION/: minted from source/, upstream re-fetched"
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

# Static coverage map (build/meta/coverage-map.svg) — the flat, citable companion
# to the live 3D explorer; the README hero image. Zero deps, re-derives bit-for-bit.
map:
	uv run python scripts/coverage-map.py

site: map
	uv run --with pyarrow --with pandas python scripts/build-provenance.py

# Build the site fresh, then push to S3 (origin only — never direct S3) and
# invalidate CloudFront so squishy.jackdanger.com updates immediately.
# Prefix the whole thing with your creds if you use aws-vault:
#   aws-vault exec personal -- make deploy
deploy: site
	bash scripts/deploy-site.sh

# Stream every edition member into S3, idempotently. Members are partitioned by
# provenance: UPSTREAM (third-party, re-fetched + sha-verified) vs MINTED (our own
# canonical copy in source/, because re-fetching could differ). One file at a time so
# the ~17 GB corpus never lands on disk at once. Needs creds — prefix with aws-vault:
#   make publish ARGS=--plan                          # offline preview, no AWS
#   aws-vault exec personal -- make publish ARGS=--check
#   aws-vault exec personal -- make mint              # seed source-of-record (run before publish/release)
#   aws-vault exec personal -- make publish           # populate the working (draft) corpus
#   aws-vault exec personal -- make release EDITION=v1.0   # freeze the edition
publish:
	uv run --with pyarrow python scripts/publish-corpus.py $(ARGS)

mint:
	uv run --with pyarrow python scripts/publish-corpus.py --mint $(ARGS)

release:
	@test -n "$(EDITION)" || { echo "usage: make release EDITION=v1.0"; exit 2; }
	uv run --with pyarrow python scripts/publish-corpus.py --release "$(EDITION)" $(ARGS)

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
