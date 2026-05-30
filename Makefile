# squishy/Makefile
# Squishy — a fixture corpus for compression / decompression libraries.
#
# Builds: sources -> raw -> pathological -> modern -> individual -> bundles
#         -> dict -> negative -> manifest -> verify -> publish -> invalidate
#
# All artifacts are reproducible (deterministic timestamps, single-threaded
# compressors where parallelism injects nondeterminism). The 'immutable'
# cache header on S3 depends on this — see the REPRODUCIBILITY block below.
#
# Published to: s3://jackdanger.com/squishy/  (served via CloudFront).
#
# Usage:
#   make doctor               # check toolchain
#   make all                  # build everything locally
#   make verify               # sha256sum -c
#   make plan-publish         # dry-run S3 diff
#   make publish              # idempotent upload (skips files where sha matches)
#   make invalidate           # CloudFront invalidation of index files only
#   make stream-publish       # low-disk build → upload → delete loop
#   make negative-publish     # build+publish negative fixtures, dict, and meta
#   make storage-reduce       # fix S3 metadata/storage-class on existing objects

SHELL        := /usr/bin/env bash
.ONESHELL:
.DELETE_ON_ERROR:
.SHELLFLAGS  := -eu -o pipefail -c
MAKEFLAGS    += --warn-undefined-variables --no-builtin-rules

# ─── Configuration ─────────────────────────────────────────────────────────
# All Configuration overridable via env or command-line. Forks can publish
# to their own bucket without editing the Makefile:
#   S3_BUCKET=mybucket S3_PREFIX=corpus make stream-publish
S3_BUCKET        ?= jackdanger.com
S3_PREFIX        ?= squishy
CLOUDFRONT_DIST  ?= E337PUI5JFO3S1
# AWS_VAULT wraps every AWS CLI call. Override empty when not on the
# maintainer's laptop:
#   make AWS_VAULT= stream-publish    # CI (creds via configure-aws-credentials)
# --duration=12h holds the session token long enough for a multi-hour publish.
AWS_VAULT        ?= aws-vault exec --duration=12h personal --
AWS              := $(AWS_VAULT) aws

BUILD            := build
RAW              := $(BUILD)/raw
INDIV            := $(BUILD)/individual
BUNDLE           := $(BUILD)/bundles
NEG              := $(BUILD)/negative
DICT             := $(BUILD)/dict
META             := $(BUILD)/meta

# Cache policy
CC_IMMUTABLE     := public, max-age=31536000, immutable
CC_INDEX         := public, max-age=300, must-revalidate

# ─── REPRODUCIBILITY ───────────────────────────────────────────────────────
# Every compressor in this Makefile is invoked with flags that suppress
# nondeterminism (timestamps, hostnames, parallel-scheduling differences).
# If you add a new compressor, audit it for:
#   - embedded timestamps                  (gzip mtime, zip mtime, 7z creation)
#   - embedded filenames                    (gzip FNAME, brotli filename)
#   - parallel-scheduling block boundaries  (xz -T>1, pigz, pbzip2)
#   - tool-version-dependent encoder choices
#
# tools.lock (built by `make doctor`) pins every binary's version. Bytes are
# stable only against that lockfile — when tools change, regenerate.

export SOURCE_DATE_EPOCH := 0
export TZ                := UTC
export LC_ALL            := C

# ─── Toolchain detection (mac / linux) ─────────────────────────────────────
# GNU tar is required for --sort/--mtime; macOS ships BSD tar as `tar`,
# so we prefer `gtar` (homebrew gnu-tar) when present.
GTAR    := $(shell command -v gtar 2>/dev/null || command -v tar)
SHASUM  := $(shell command -v sha256sum 2>/dev/null || echo 'shasum -a 256')
STAT_SZ := $(shell if stat --version >/dev/null 2>&1; then echo 'stat -c %s'; else echo 'stat -f %z'; fi)

# Compressors (presence is verified by `make doctor`; missing tools produce
# warnings and skip their rules rather than failing the whole build).
GZIP    := $(shell command -v gzip   2>/dev/null)
BZIP2   := $(shell command -v bzip2  2>/dev/null)
XZ      := $(shell command -v xz     2>/dev/null)
ZSTD    := $(shell command -v zstd   2>/dev/null)
LZ4     := $(shell command -v lz4    2>/dev/null)
BROTLI  := $(shell command -v brotli 2>/dev/null)
LZIP    := $(shell command -v lzip   2>/dev/null)
LZOP    := $(shell command -v lzop   2>/dev/null)
LZMA    := $(shell command -v lzma   2>/dev/null)
ZPAQ    := $(shell command -v zpaq   2>/dev/null)
P7Z     := $(shell command -v 7z     2>/dev/null || command -v 7zz 2>/dev/null)
ZIP     := $(shell command -v zip    2>/dev/null)
CPIO    := $(shell command -v cpio   2>/dev/null)
AR      := $(shell command -v ar     2>/dev/null)
MKSQFS  := $(shell command -v mksquashfs 2>/dev/null)
COMPRES := $(shell command -v compress 2>/dev/null)
PYTHON  := $(shell command -v python3 2>/dev/null)
CURL    := $(shell command -v curl   2>/dev/null)

# Deterministic-encoder flag sets
GZIP_FLAGS    := -n -k -c          # -n: no name/timestamp
BZIP2_FLAGS   := -k -c
XZ_FLAGS      := -k -c -T1         # -T1: single-thread, deterministic output
ZSTD_FLAGS    := -k -T1 -q -f --no-progress
LZ4_FLAGS     := -k -c -q
BROTLI_FLAGS  := -k -c
LZIP_FLAGS    := -k -c
LZOP_FLAGS    := -k -c -n          # -n: no name
LZMA_FLAGS    := -k -c
ZPAQ_FLAGS    := add
TAR_FLAGS     := --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner --format=ustar
ZIP_FLAGS     := -X -q             # -X: no extra fields (no uid/gid/mtime)
P7Z_FLAGS     := -mtm=off -mtc=off -mta=off -bd -bb0
MKSQFS_FLAGS  := -all-time 0 -mkfs-time 0 -no-exports -no-recovery -no-progress

# ─── File lists ────────────────────────────────────────────────────────────
SILESIA_NAMES := dickens mozilla mr nci ooffice osdb reymont samba sao webster x-ray xml
SILESIA_RAW   := $(addprefix $(RAW)/silesia/,$(SILESIA_NAMES))

# Modern set replaces parts of Squash Corpus that have redistribution risk
# (raspbian/vmlinux/blender binaries → GPL source-availability / non-free
# firmware). We keep license-clean items and generate synthetic modern files.
MODERN_FETCH  := jquery-2.1.4.min.js bootstrap-3.3.6.min.css eff.html
MODERN_GEN    := sample.json sample.ndjson sample.sqlite sample.parquet sample.protobuf sample.log random-1M
MODERN_NAMES  := $(MODERN_FETCH) $(MODERN_GEN)
MODERN_RAW    := $(addprefix $(RAW)/modern/,$(MODERN_NAMES))

# Pathological inputs. Sub-window-size + window-boundary + entropy extremes.
PATHO_TINY    := empty-0B one-1B tiny-13B small-256B page-4095B short-65535B
PATHO_ENTROPY := zeros-1M zeros-10M zeros-100M urandom-1M urandom-10M urandom-100M \
                 repeat-A-1M alternating-1M ascii-1M onebyte-per-page-1M \
                 phrase-repeated-10M pi-digits-10M sparse-geometric-10M \
                 already-compressed-blob
PATHO_WINDOW  := window-zstd-128M-minus1 window-zstd-128M window-zstd-128M-plus1 \
                 window-brotli-16M-minus1 window-brotli-16M window-brotli-16M-plus1 \
                 window-deflate-32K-minus1 window-deflate-32K window-deflate-32K-plus1
PATHO_NAMES   := $(PATHO_TINY) $(PATHO_ENTROPY) $(PATHO_WINDOW)
PATHO_RAW     := $(addprefix $(RAW)/pathological/,$(PATHO_NAMES))

ALL_RAW       := $(SILESIA_RAW) $(MODERN_RAW) $(PATHO_RAW)
ALL_SETS      := silesia modern pathological

# ─── Compression matrix (individual files) ─────────────────────────────────
# Each codec has: a default-level extension and a set of explicit levels.
# Pattern rules below build individual/<set>/<file>.<ext> from raw/<set>/<file>.

CODECS_DEFAULT := gz bz2 xz zst lz4 br lzma
CODECS_OPT     := lz lzo zpaq Z
CODECS_ALL     := $(CODECS_DEFAULT) $(CODECS_OPT)

LEVELS_GZ      := 1 6 9
LEVELS_XZ      := 0 6 9
LEVELS_ZST     := 1 3 9 19 22
LEVELS_BR      := 1 6 11

# ─── Source URLs (pinned by sha256 in tools.lock; verified on fetch) ───────
SILESIA_TAR_URL := https://wanos.co/assets/silesia.tar
# jquery / bootstrap / eff.html: clean licenses, fetched from canonical hosts
JQUERY_URL      := https://code.jquery.com/jquery-2.1.4.min.js
BOOTSTRAP_URL   := https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/3.3.6/css/bootstrap.min.css
EFF_HTML_URL    := https://www.eff.org/

# ─── Top-level targets ─────────────────────────────────────────────────────
.PHONY: all squishy sources raw pathological modern individual bundles dict \
        negative manifest verify publish invalidate plan-publish doctor clean \
        help stream-plan stream-publish stream-publish-dryrun negative-publish \
        storage-reduce agent-docs \
        calibrated-bundle calibrated-html calibrated-publish calibrated-invalidate \
        v4-calibrate v4-bench v4-test

help:
	@echo "Targets:"
	@echo "  doctor         — check toolchain, write build/tools.lock"
	@echo "  sources        — fetch upstream archives (silesia.tar, jquery, bootstrap, eff)"
	@echo "  raw            — materialize build/raw/{silesia,modern,pathological}/"
	@echo "  pathological   — synthetic raw inputs (sub-window, window-boundary, entropy)"
	@echo "  modern         — fetch+generate modern set (json, ndjson, sqlite, parquet, ...)"
	@echo "  individual     — compress each raw file with each codec at multiple levels"
	@echo "  bundles        — combined archives (tar/zip/7z/squashfs + multi-frame variants)"
	@echo "  dict           — zstd dictionary-trained fixtures"
	@echo "  negative       — corrupted / pathological-decoder fixtures + bombs"
	@echo "  manifest       — index.txt, manifest.json, CHECKSUMS.sha256, versions.txt"
	@echo "  verify         — sha256sum -c CHECKSUMS.sha256"
	@echo "  plan-publish   — dry-run diff of local build vs S3"
	@echo "  publish        — idempotent upload to s3://$(S3_BUCKET)/$(S3_PREFIX)/"
	@echo "  invalidate     — CloudFront invalidation for index files"
	@echo "  stream-plan    — enumerate streaming publish plan to build/meta/plan.tsv"
	@echo "  stream-publish — low-disk build → upload → delete loop (use instead of publish when disk is tight)"
	@echo "  stream-publish-dryrun — dry-run of stream-publish (first 30 lines)"
	@echo "  negative-publish — build+publish negative fixtures, dict, and meta"
	@echo "  storage-reduce — rewrite S3 objects with correct metadata/storage-class"
	@echo "  agent-docs     — regenerate AGENTS.md, agent.json, smoke.zip, _INDEX files"
	@echo "  all            — sources → ... → manifest (full local build, includes agent docs)"
	@echo "  squishy        — all + verify + publish + invalidate (the full bake)"
	@echo "  clean          — rm -rf $(BUILD)/"
	@echo ""
	@echo "Calibrated corpus v4:"
	@echo "  calibrated-bundle   — generate + bench + curate + bundle → build/bundle/"
	@echo "  calibrated-html     — (re)build build/bundle/index.html from scripts/bundle-index.html"
	@echo "  calibrated-publish  — sync build/bundle/ → s3://\$$(S3_BUCKET)/\$$(CALIBRATED_S3_PREFIX)/"
	@echo "  calibrated-invalidate — CloudFront invalidation for calibrated index files"
	@echo ""
	@echo "v4 corpus pipeline:"
	@echo "  v4-calibrate   — generate ~84 synthetic files at 4 MB, measure H and S"
	@echo "  v4-bench       — benchmark calibration files, compute Kendall-τ per H×S cell"
	@echo "  v4-test        — run test suite"

all: doctor sources raw pathological modern individual bundles dict negative manifest

squishy: all verify publish invalidate

clean:
	rm -rf $(BUILD)

# ─── doctor: tool detection and version pinning ────────────────────────────
doctor:
	@mkdir -p $(BUILD)
	@echo "# squishy/tools.lock — tool versions used to produce this corpus" > $(BUILD)/tools.lock
	@echo "# generated $$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> $(BUILD)/tools.lock
	@for tool in gzip bzip2 xz zstd lz4 brotli lzip lzop lzma zpaq 7z 7zz zip cpio ar mksquashfs compress gtar tar python3 curl aws; do \
	  path=$$(command -v $$tool 2>/dev/null || true); \
	  if [[ -n "$$path" ]]; then \
	    ver=$$($$path --version 2>&1 | head -1 || echo unknown); \
	    printf "%-12s %s %s\n" "$$tool" "$$path" "$$ver" >> $(BUILD)/tools.lock; \
	  else \
	    printf "%-12s MISSING\n" "$$tool" >> $(BUILD)/tools.lock; \
	  fi; \
	done
	@echo "wrote $(BUILD)/tools.lock"
	@grep MISSING $(BUILD)/tools.lock || true
	@if [[ "$(GTAR)" != *gtar ]] && [[ "$$(uname)" == "Darwin" ]]; then \
	  echo "WARNING: GNU tar not found. Install with: brew install gnu-tar"; \
	fi
	@if [[ -z "$(P7Z)" ]];     then echo "HINT: brew install sevenzip   (or p7zip)"; fi
	@if [[ -z "$(BROTLI)" ]];  then echo "HINT: brew install brotli"; fi
	@if [[ -z "$(ZPAQ)" ]];    then echo "HINT: brew install zpaq"; fi
	@if [[ -z "$(LZIP)" ]];    then echo "HINT: brew install lzip"; fi
	@if [[ -z "$(LZOP)" ]];    then echo "HINT: brew install lzop"; fi
	@if [[ -z "$(MKSQFS)" ]];  then echo "HINT: brew install squashfs"; fi

# ─── sources: fetch upstream archives ──────────────────────────────────────
sources: $(BUILD)/sources/silesia.tar $(BUILD)/sources/jquery.min.js \
         $(BUILD)/sources/bootstrap.min.css $(BUILD)/sources/eff.html

$(BUILD)/sources/silesia.tar:
	@mkdir -p $(dir $@)
	@if [[ ! -f $@ ]]; then \
	  echo "fetch $(SILESIA_TAR_URL)"; \
	  $(CURL) -fL --retry 3 -o $@.tmp $(SILESIA_TAR_URL); \
	  mv $@.tmp $@; \
	fi
	@$(SHASUM) $@ > $@.sha256

$(BUILD)/sources/jquery.min.js:
	@mkdir -p $(dir $@)
	@$(CURL) -fL --retry 3 -o $@.tmp $(JQUERY_URL) && mv $@.tmp $@
	@$(SHASUM) $@ > $@.sha256

$(BUILD)/sources/bootstrap.min.css:
	@mkdir -p $(dir $@)
	@$(CURL) -fL --retry 3 -o $@.tmp $(BOOTSTRAP_URL) && mv $@.tmp $@
	@$(SHASUM) $@ > $@.sha256

$(BUILD)/sources/eff.html:
	@mkdir -p $(dir $@)
	@$(CURL) -fL --retry 3 -A "squishy-builder/1.0" -o $@.tmp $(EFF_HTML_URL) && mv $@.tmp $@
	@$(SHASUM) $@ > $@.sha256

# ─── raw: extract Silesia, copy modern fetches, generate pathological ──────
raw: $(ALL_RAW)

# Silesia extraction: one tar invocation produces all 12 files. Use a stamp
# file so make 3.81 (Apple's bundled gmake) doesn't run the recipe 12 times.
# wanos.co/silesia.tar wraps the files in a top-level silesia/ directory; we
# strip that so files land directly under $(RAW)/silesia/.
$(BUILD)/.silesia-extracted: $(BUILD)/sources/silesia.tar
	@mkdir -p $(RAW)/silesia
	@$(GTAR) -xf $< -C $(RAW)/silesia --strip-components=1
	@touch $(SILESIA_RAW) $@

$(SILESIA_RAW): $(BUILD)/.silesia-extracted ;

$(RAW)/modern/jquery-2.1.4.min.js: $(BUILD)/sources/jquery.min.js
	@mkdir -p $(dir $@) && cp $< $@
$(RAW)/modern/bootstrap-3.3.6.min.css: $(BUILD)/sources/bootstrap.min.css
	@mkdir -p $(dir $@) && cp $< $@
$(RAW)/modern/eff.html: $(BUILD)/sources/eff.html
	@mkdir -p $(dir $@) && cp $< $@

# Modern synthetic files: one script run produces all of them.
MODERN_GEN_TARGETS := $(addprefix $(RAW)/modern/,$(MODERN_GEN))
$(BUILD)/.modern-generated: scripts/gen-modern.py
	@mkdir -p $(RAW)/modern
	@$(PYTHON) scripts/gen-modern.py $(RAW)/modern
	@touch $(MODERN_GEN_TARGETS) $@

$(MODERN_GEN_TARGETS): $(BUILD)/.modern-generated ;

# Pathological: all generated by one script from a fixed seed.
pathological: $(PATHO_RAW)
$(BUILD)/.pathological-generated: scripts/gen-pathological.py
	@mkdir -p $(RAW)/pathological
	@$(PYTHON) scripts/gen-pathological.py $(RAW)/pathological
	@touch $(PATHO_RAW) $@

$(PATHO_RAW): $(BUILD)/.pathological-generated ;

modern: $(MODERN_RAW)

# ─── individual: <set>/<file>.<codec>[.l<level>] ───────────────────────────
# Default-level outputs for every codec we have a binary for.
INDIV_GZ      := $(if $(GZIP),  $(patsubst $(RAW)/%,$(INDIV)/%.gz,  $(ALL_RAW)))
INDIV_BZ2     := $(if $(BZIP2), $(patsubst $(RAW)/%,$(INDIV)/%.bz2, $(ALL_RAW)))
INDIV_XZ      := $(if $(XZ),    $(patsubst $(RAW)/%,$(INDIV)/%.xz,  $(ALL_RAW)))
INDIV_ZST     := $(if $(ZSTD),  $(patsubst $(RAW)/%,$(INDIV)/%.zst, $(ALL_RAW)))
INDIV_LZ4     := $(if $(LZ4),   $(patsubst $(RAW)/%,$(INDIV)/%.lz4, $(ALL_RAW)))
INDIV_BR      := $(if $(BROTLI),$(patsubst $(RAW)/%,$(INDIV)/%.br,  $(ALL_RAW)))
INDIV_LZMA    := $(if $(LZMA),  $(patsubst $(RAW)/%,$(INDIV)/%.lzma,$(ALL_RAW)))
INDIV_LZ      := $(if $(LZIP),  $(patsubst $(RAW)/%,$(INDIV)/%.lz,  $(ALL_RAW)))
INDIV_LZO     := $(if $(LZOP),  $(patsubst $(RAW)/%,$(INDIV)/%.lzo, $(ALL_RAW)))
INDIV_ZPAQ    := $(if $(ZPAQ),  $(patsubst $(RAW)/%,$(INDIV)/%.zpaq,$(ALL_RAW)))
INDIV_7Z      := $(if $(P7Z),   $(patsubst $(RAW)/%,$(INDIV)/%.7z,  $(ALL_RAW)))
INDIV_ZIP     := $(if $(ZIP),   $(patsubst $(RAW)/%,$(INDIV)/%.zip, $(ALL_RAW)))

# Multi-level outputs for the codecs that justify it
INDIV_ZST_LEVELS := $(foreach l,$(LEVELS_ZST),$(patsubst $(RAW)/%,$(INDIV)/%.zst.l$(l),$(ALL_RAW)))
INDIV_GZ_LEVELS  := $(foreach l,$(LEVELS_GZ), $(patsubst $(RAW)/%,$(INDIV)/%.gz.l$(l), $(ALL_RAW)))
INDIV_XZ_LEVELS  := $(foreach l,$(LEVELS_XZ), $(patsubst $(RAW)/%,$(INDIV)/%.xz.l$(l), $(ALL_RAW)))
INDIV_BR_LEVELS  := $(foreach l,$(LEVELS_BR), $(patsubst $(RAW)/%,$(INDIV)/%.br.l$(l), $(ALL_RAW)))

# zip with internal-codec variants (store / deflate / bzip2 / lzma / zstd)
# zip.zstd dropped: 7zz returns E_NOTIMPL for zstd as a zip internal codec
# (zstd-in-zip isn't in any open-source 7-Zip release we tested).
INDIV_ZIP_VAR := $(if $(ZIP),  $(foreach v,store deflate,$(patsubst $(RAW)/%,$(INDIV)/%.zip.$(v),$(ALL_RAW)))) \
                 $(if $(P7Z),  $(foreach v,bzip2 lzma,     $(patsubst $(RAW)/%,$(INDIV)/%.zip.$(v),$(ALL_RAW))))

ALL_INDIV := $(INDIV_GZ) $(INDIV_BZ2) $(INDIV_XZ) $(INDIV_ZST) $(INDIV_LZ4) $(INDIV_BR) \
             $(INDIV_LZMA) $(INDIV_LZ) $(INDIV_LZO) $(INDIV_ZPAQ) $(INDIV_7Z) $(INDIV_ZIP) \
             $(INDIV_ZST_LEVELS) $(INDIV_GZ_LEVELS) $(INDIV_XZ_LEVELS) $(INDIV_BR_LEVELS) \
             $(INDIV_ZIP_VAR)

individual: $(ALL_INDIV)

# Pattern rules: default level
$(INDIV)/%.gz:   $(RAW)/% ; @mkdir -p $(dir $@) && $(GZIP)   $(GZIP_FLAGS)   $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.bz2:  $(RAW)/% ; @mkdir -p $(dir $@) && $(BZIP2)  $(BZIP2_FLAGS)  $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.xz:   $(RAW)/% ; @mkdir -p $(dir $@) && $(XZ)     $(XZ_FLAGS) -9e $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.zst:  $(RAW)/% ; @mkdir -p $(dir $@) && $(ZSTD)   $(ZSTD_FLAGS) -19 $< -o $@.tmp && mv $@.tmp $@
$(INDIV)/%.lz4:  $(RAW)/% ; @mkdir -p $(dir $@) && $(LZ4)    $(LZ4_FLAGS) -9  $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.br:   $(RAW)/% ; @mkdir -p $(dir $@) && $(BROTLI) $(BROTLI_FLAGS) -q 11 $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.lzma: $(RAW)/% ; @mkdir -p $(dir $@) && $(LZMA)   $(LZMA_FLAGS)  $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.lz:   $(RAW)/% ; @mkdir -p $(dir $@) && $(LZIP)   $(LZIP_FLAGS) -9 $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.lzo:  $(RAW)/% ; @mkdir -p $(dir $@) && $(LZOP)   $(LZOP_FLAGS) -9 $< > $@.tmp && mv $@.tmp $@
$(INDIV)/%.zpaq: $(RAW)/% ; @mkdir -p $(dir $@) && rm -f $@ && $(ZPAQ) $(ZPAQ_FLAGS) $@ $< -m5 > /dev/null

$(INDIV)/%.7z:   $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp.7z
	@cd $(dir $<) && $(P7Z) a $(P7Z_FLAGS) -mx=9 -y $(CURDIR)/$@.tmp.7z $(notdir $<) > /dev/null
	@mv $@.tmp.7z $@

$(INDIV)/%.zip:  $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp
	@cd $(dir $<) && $(ZIP) $(ZIP_FLAGS) -9 $(CURDIR)/$@.tmp $(notdir $<) > /dev/null
	@mv $@.tmp $@

# Per-level rules (one per (codec, level) tuple — generated via foreach)
define ZST_LEVEL_RULE
$$(INDIV)/%.zst.l$(1): $$(RAW)/% ; @mkdir -p $$(dir $$@) && $$(ZSTD) $$(ZSTD_FLAGS) -$(1) $$< -o $$@.tmp && mv $$@.tmp $$@
endef
$(foreach l,$(LEVELS_ZST),$(eval $(call ZST_LEVEL_RULE,$(l))))

define GZ_LEVEL_RULE
$$(INDIV)/%.gz.l$(1): $$(RAW)/% ; @mkdir -p $$(dir $$@) && $$(GZIP) $$(GZIP_FLAGS) -$(1) $$< > $$@.tmp && mv $$@.tmp $$@
endef
$(foreach l,$(LEVELS_GZ),$(eval $(call GZ_LEVEL_RULE,$(l))))

define XZ_LEVEL_RULE
$$(INDIV)/%.xz.l$(1): $$(RAW)/% ; @mkdir -p $$(dir $$@) && $$(XZ) $$(XZ_FLAGS) -$(1) $$< > $$@.tmp && mv $$@.tmp $$@
endef
$(foreach l,$(LEVELS_XZ),$(eval $(call XZ_LEVEL_RULE,$(l))))

define BR_LEVEL_RULE
$$(INDIV)/%.br.l$(1): $$(RAW)/% ; @mkdir -p $$(dir $$@) && $$(BROTLI) $$(BROTLI_FLAGS) -q $(1) $$< > $$@.tmp && mv $$@.tmp $$@
endef
$(foreach l,$(LEVELS_BR),$(eval $(call BR_LEVEL_RULE,$(l))))

# zip internal-codec variants
$(INDIV)/%.zip.store: $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp
	@cd $(dir $<) && $(ZIP) $(ZIP_FLAGS) -0 $(CURDIR)/$@.tmp $(notdir $<) > /dev/null
	@mv $@.tmp $@

$(INDIV)/%.zip.deflate: $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp
	@cd $(dir $<) && $(ZIP) $(ZIP_FLAGS) -Z deflate -9 $(CURDIR)/$@.tmp $(notdir $<) > /dev/null
	@mv $@.tmp $@

$(INDIV)/%.zip.bzip2: $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp.zip
	@cd $(dir $<) && $(P7Z) a $(P7Z_FLAGS) -tzip -mm=bzip2 -mx=9 $(CURDIR)/$@.tmp.zip $(notdir $<) > /dev/null
	@mv $@.tmp.zip $@

# zip with lzma/zstd internal codec: 7z handles this and writes a real .zip
$(INDIV)/%.zip.lzma: $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp.zip
	@cd $(dir $<) && $(P7Z) a $(P7Z_FLAGS) -tzip -mm=LZMA -mx=9 $(CURDIR)/$@.tmp.zip $(notdir $<) > /dev/null
	@mv $@.tmp.zip $@

$(INDIV)/%.zip.zstd: $(RAW)/%
	@mkdir -p $(dir $@) && rm -f $@.tmp.zip
	@cd $(dir $<) && $(P7Z) a $(P7Z_FLAGS) -tzip -mm=zstd -mx=9 $(CURDIR)/$@.tmp.zip $(notdir $<) > /dev/null
	@mv $@.tmp.zip $@

# ─── bundles: combined archives across composition × codec ─────────────────
# Composition × codec matrix per set. For each set:
#   orderings: alpha, random (random uses deterministic seed)
#   tar codecs: gz bz2 xz zst lz4 br lzma lz lzo zpaq
#   container variants: zip{store,deflate,bzip2,lzma,zstd}, 7z{lzma2,ppmd,bzip2,deflate}
#   filesystem images: squashfs{gzip,xz,lzo,lz4,zstd}
#   non-tar concatenation: concat-{gz,xz,zst}, concat-zst-skipframes, concat-zst-dict
#
# Solid-archive ordering matters (7z/squashfs) — we also build size-desc variants
# for those two formats specifically.

ORDERINGS         := alpha random
SOLID_ORDERINGS   := alpha size-desc
TAR_CODECS        := gz bz2 xz zst lz4 br lzma
SQUASHFS_CODECS   := gzip xz lz4 zstd
SEVENZ_CODECS     := lzma2 ppmd bzip2 deflate
ZIP_INTERNALS     := store deflate bzip2 lzma
CONCAT_CODECS     := gz xz zst

define BUNDLE_TAR_RAW
$$(BUNDLE)/$(1)/$(1).$(2).tar: $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/order-files.sh $(2) $$(RAW)/$(1) > $$@.list
	@$$(GTAR) $$(TAR_FLAGS) -C $$(RAW) -T $$@.list -cf $$@.tmp
	@mv $$@.tmp $$@ && rm -f $$@.list
endef
$(foreach s,$(ALL_SETS),$(foreach o,$(ORDERINGS),$(eval $(call BUNDLE_TAR_RAW,$(s),$(o)))))

define BUNDLE_TAR_CODEC
$$(BUNDLE)/$(1)/$(1).$(2).tar.$(3): $$(BUNDLE)/$(1)/$(1).$(2).tar
	@scripts/compress-one.sh $(3) $$< $$@
endef
$(foreach s,$(ALL_SETS),$(foreach o,$(ORDERINGS),$(foreach c,$(TAR_CODECS),$(eval $(call BUNDLE_TAR_CODEC,$(s),$(o),$(c))))))

# Combined "everything" bundle: silesia + modern + pathological.
# order-files.sh emits paths like "raw/silesia/dickens" (relative to RAW's
# parent). So tar's -C must point one level up from RAW.
$(BUNDLE)/combined/everything.alpha.tar: $(ALL_RAW)
	@mkdir -p $(dir $@)
	@scripts/order-files.sh alpha $(RAW) > $@.list
	@$(GTAR) $(TAR_FLAGS) -C $(dir $(RAW)) -T $@.list -cf $@.tmp
	@mv $@.tmp $@ && rm -f $@.list

# everything.alpha.tar is large (~1 GB) and is only a step on the way to the
# compressed variants. Mark it intermediate so make removes it after build.
.INTERMEDIATE: $(BUNDLE)/combined/everything.alpha.tar

define COMBINED_CODEC
$$(BUNDLE)/combined/everything.alpha.tar.$(1): $$(BUNDLE)/combined/everything.alpha.tar
	@scripts/compress-one.sh $(1) $$< $$@
endef
$(foreach c,$(TAR_CODECS),$(eval $(call COMBINED_CODEC,$(c))))

# Zip / 7z / squashfs containers (per set, alpha + size-desc for solids)
define BUNDLE_ZIP_VAR
$$(BUNDLE)/$(1)/$(1).alpha.zip.$(2): $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/build-zip.sh $(2) $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(foreach v,$(ZIP_INTERNALS),$(eval $(call BUNDLE_ZIP_VAR,$(s),$(v)))))

define BUNDLE_7Z_VAR
$$(BUNDLE)/$(1)/$(1).$(3).7z.$(2): $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/build-7z.sh $(2) $(3) $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(foreach o,$(SOLID_ORDERINGS),$(foreach c,$(SEVENZ_CODECS),$(eval $(call BUNDLE_7Z_VAR,$(s),$(c),$(o))))))

define BUNDLE_SQFS_VAR
$$(BUNDLE)/$(1)/$(1).$(3).squashfs.$(2): $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/build-squashfs.sh $(2) $(3) $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(foreach o,$(SOLID_ORDERINGS),$(foreach c,$(SQUASHFS_CODECS),$(eval $(call BUNDLE_SQFS_VAR,$(s),$(c),$(o))))))

# cpio / pax / ar (one ordering each — they're not solid)
define BUNDLE_CONTAINER
$$(BUNDLE)/$(1)/$(1).alpha.$(2): $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/build-container.sh $(2) $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(foreach c,cpio pax ar,$(eval $(call BUNDLE_CONTAINER,$(s),$(c)))))

# Multi-frame concatenation (no tar) — exercises decoder restart-state
define BUNDLE_CONCAT
$$(BUNDLE)/$(1)/$(1).alpha.concat-$(2): $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@scripts/build-concat.sh $(2) $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(foreach c,$(CONCAT_CODECS),$(eval $(call BUNDLE_CONCAT,$(s),$(c)))))

# zstd-specific: skippable-frame interleave + dictionary
define BUNDLE_ZSTD_SKIP
$$(BUNDLE)/$(1)/$(1).alpha.concat-zst-skipframes: $$(filter $$(RAW)/$(1)/%,$$(ALL_RAW))
	@mkdir -p $$(dir $$@)
	@$$(PYTHON) scripts/build-zst-skipframes.py $$(RAW)/$(1) $$@
endef
$(foreach s,$(ALL_SETS),$(eval $(call BUNDLE_ZSTD_SKIP,$(s))))

# Mixed-member: gzip + zstd-skippable + gzip — universal-decoder hostile
$(BUNDLE)/mixed-member/silesia-mixed.bin: $(SILESIA_RAW)
	@mkdir -p $(dir $@)
	@$(PYTHON) scripts/build-mixed-member.py $(RAW)/silesia $@

BUNDLES_LIST := $(foreach s,$(ALL_SETS), \
                  $(foreach o,$(ORDERINGS), $(BUNDLE)/$(s)/$(s).$(o).tar) \
                  $(foreach o,$(ORDERINGS),$(foreach c,$(TAR_CODECS), $(BUNDLE)/$(s)/$(s).$(o).tar.$(c))) \
                  $(foreach v,$(ZIP_INTERNALS), $(BUNDLE)/$(s)/$(s).alpha.zip.$(v)) \
                  $(foreach o,$(SOLID_ORDERINGS),$(foreach c,$(SEVENZ_CODECS), $(BUNDLE)/$(s)/$(s).$(o).7z.$(c))) \
                  $(foreach o,$(SOLID_ORDERINGS),$(foreach c,$(SQUASHFS_CODECS), $(BUNDLE)/$(s)/$(s).$(o).squashfs.$(c))) \
                  $(foreach c,cpio pax ar, $(BUNDLE)/$(s)/$(s).alpha.$(c)) \
                  $(foreach c,$(CONCAT_CODECS), $(BUNDLE)/$(s)/$(s).alpha.concat-$(c)) \
                  $(BUNDLE)/$(s)/$(s).alpha.concat-zst-skipframes) \
                $(BUNDLE)/combined/everything.alpha.tar \
                $(foreach c,$(TAR_CODECS), $(BUNDLE)/combined/everything.alpha.tar.$(c)) \
                $(BUNDLE)/mixed-member/silesia-mixed.bin

bundles: $(BUNDLES_LIST)

# ─── dict: zstd dictionary training and application ────────────────────────
# Where dicts matter: many small similar files. We use small JSON-line samples.
$(DICT)/json-samples.zdict: $(RAW)/modern/sample.ndjson
	@mkdir -p $(DICT) $(DICT)/json-samples
	@$(PYTHON) scripts/split-ndjson.py $< $(DICT)/json-samples 1024
	@$(ZSTD) --train $(DICT)/json-samples/* -o $@ -q

$(DICT)/json-samples.tar.zst: $(DICT)/json-samples.zdict
	@$(GTAR) $(TAR_FLAGS) -C $(DICT) -cf $(DICT)/json-samples.tar json-samples
	@$(ZSTD) $(ZSTD_FLAGS) -19 -D $< $(DICT)/json-samples.tar -o $@
	@rm -f $(DICT)/json-samples.tar

$(DICT)/json-samples.no-dict.tar.zst: $(DICT)/json-samples.zdict
	@$(GTAR) $(TAR_FLAGS) -C $(DICT) -cf $(DICT)/json-samples.tar json-samples
	@$(ZSTD) $(ZSTD_FLAGS) -19 $(DICT)/json-samples.tar -o $@
	@rm -f $(DICT)/json-samples.tar

# Dict applied to non-matching content (worst case)
$(DICT)/wrong-dict-silesia-dickens.zst: $(DICT)/json-samples.zdict $(RAW)/silesia/dickens
	@$(ZSTD) $(ZSTD_FLAGS) -19 -D $< $(RAW)/silesia/dickens -o $@

DICT_LIST := $(DICT)/json-samples.zdict $(DICT)/json-samples.tar.zst \
             $(DICT)/json-samples.no-dict.tar.zst $(DICT)/wrong-dict-silesia-dickens.zst
dict: $(DICT_LIST)

# ─── negative: corrupted fixtures + CVE-class + bombs ──────────────────────
negative: $(BUILD)/.negative.stamp

$(BUILD)/.negative.stamp: scripts/gen-negative.py $(ALL_INDIV) $(BUNDLES_LIST)
	@mkdir -p $(NEG)
	@$(PYTHON) scripts/gen-negative.py --indiv $(INDIV) --bundle $(BUNDLE) --out $(NEG)
	@touch $@

# ─── manifest: index.txt, manifest.json, CHECKSUMS, versions, ratios ───────
manifest: $(META)/index.txt $(META)/manifest.json $(META)/CHECKSUMS.sha256 \
          $(META)/versions.txt $(META)/expected-ratio.json $(META)/README.txt \
          $(META)/index.html $(META)/listing.html

# index.html + listing.html derive from manifest.json + versions.txt
$(META)/index.html $(META)/listing.html: scripts/build-html.py $(META)/manifest.json $(META)/versions.txt
	@$(PYTHON) scripts/build-html.py --meta $(META)

$(META)/versions.txt: doctor
	@mkdir -p $(META)
	@cp $(BUILD)/tools.lock $@

$(META)/README.txt: scripts/render-readme.sh
	@mkdir -p $(META) && scripts/render-readme.sh > $@

$(META)/index.txt $(META)/manifest.json $(META)/CHECKSUMS.sha256 $(META)/expected-ratio.json: \
        scripts/build-manifest.py scripts/gen-agent-docs.py \
        $(ALL_RAW) $(ALL_INDIV) $(BUNDLES_LIST) $(DICT_LIST) $(BUILD)/.negative.stamp
	@mkdir -p $(META)
	@$(PYTHON) scripts/build-manifest.py \
	  --build $(BUILD) --meta $(META) --bucket $(S3_BUCKET) --prefix $(S3_PREFIX)
	@$(PYTHON) scripts/gen-agent-docs.py \
	  --meta $(META) --build $(BUILD) --bucket $(S3_BUCKET) --prefix $(S3_PREFIX)

# ─── verify ────────────────────────────────────────────────────────────────
verify: $(META)/CHECKSUMS.sha256
	cd $(BUILD) && $(SHASUM) -c $(META:$(BUILD)/%=%)/CHECKSUMS.sha256

# ─── publish: idempotent S3 upload via single aws-vault session ────────────
# build-manifest.py writes $(META)/publish.tsv pre-sorted by size DESCENDING
# (so each successful upload frees the maximum local bytes — critical on
# tight disk). publish.sh reads it on stdin and HEAD-checks each before
# uploading, then deletes the local file after verification (set
# CORPUS_KEEP_LOCAL=1 to disable).
publish: manifest
	@$(AWS_VAULT) bash scripts/publish.sh \
	  $(S3_BUCKET) < $(META)/publish.tsv

plan-publish: manifest
	@$(AWS_VAULT) bash scripts/publish.sh \
	  $(S3_BUCKET) --dry-run < $(META)/publish.tsv

# ─── stream-publish: low-disk build → upload → delete loop ─────────────────
# Peak local disk = raw inputs + ONE in-flight artifact (~1-2 GB). Use this
# instead of `publish` when disk is tight or when republishing from scratch.
#
# Plan is enumerated by plan-publish.py from the Makefile's variable lists,
# so it doesn't require artifacts to already exist. Each line:
#   make <local_path> → upload → verify → rm local
#
# After artifacts are streamed, run `manifest html negative-publish` to ship
# the index files + negative fixtures + dict (small).
stream-plan:
	@mkdir -p $(META)
	@$(PYTHON) scripts/plan-publish.py --build $(BUILD) --prefix $(S3_PREFIX) > $(META)/plan.tsv
	@wc -l $(META)/plan.tsv

stream-publish: stream-plan raw
	@$(AWS_VAULT) bash scripts/stream-publish.sh $(S3_BUCKET) < $(META)/plan.tsv

stream-publish-dryrun: stream-plan
	@bash scripts/stream-publish.sh $(S3_BUCKET) --dry-run < $(META)/plan.tsv | head -30

# Negative + dict + meta (small, can be built+published in one shot)
negative-publish: dict negative manifest
	@$(AWS_VAULT) bash scripts/publish.sh $(S3_BUCKET) < $(META)/publish.tsv

agent-docs: $(META)/manifest.json
	@$(PYTHON) scripts/gen-agent-docs.py \
	  --meta $(META) --build $(BUILD) --bucket $(S3_BUCKET) --prefix $(S3_PREFIX)

# One-shot: walk every existing object under /squishy/ and rewrite it in
# place with correct content-type + cache-control + ONEZONE_IA + ACL.
# Idempotent (skips objects already in the target state). Use after editing
# storage-class policy or to fix legacy uploads.
#
# DO NOT use `aws s3 cp --metadata-directive COPY` for this — it drops the
# Content-Type on the new object (defaults to binary/octet-stream).
storage-reduce:
	@$(AWS_VAULT) bash scripts/fix-s3-metadata.sh $(S3_BUCKET) $(S3_PREFIX)

# ─── invalidate: CloudFront only for the small index files ─────────────────
# Fails harmlessly when CLOUDFRONT_DIST is unset or the endpoint doesn't
# support CloudFront (e.g., the fake-AWS local emulator).
invalidate:
	@if [[ -z "$(CLOUDFRONT_DIST)" ]]; then echo "skip invalidate: CLOUDFRONT_DIST empty"; exit 0; fi
	@$(AWS) cloudfront create-invalidation \
	  --distribution-id $(CLOUDFRONT_DIST) \
	  --paths \
	    /$(S3_PREFIX)/index.txt \
	    /$(S3_PREFIX)/manifest.json \
	    /$(S3_PREFIX)/CHECKSUMS.sha256 \
	    /$(S3_PREFIX)/versions.txt \
	    /$(S3_PREFIX)/expected-ratio.json \
	    /$(S3_PREFIX)/README.txt \
	    /$(S3_PREFIX)/AGENTS.md \
	    /$(S3_PREFIX)/agent.json \
	    /$(S3_PREFIX)/manifest.safe.json \
	    /$(S3_PREFIX)/manifest.safe.txt \
	    /$(S3_PREFIX)/decode-expectations.json \
	    /$(S3_PREFIX)/smoke.zip \
	    /$(S3_PREFIX)/robots.txt \
	    /$(S3_PREFIX)/llms.txt

# ─── Calibrated corpus v4 bundle ────────────────────────────────────────────
# S3 destination for the calibrated bundle (separate from legacy squishy/ prefix)
CALIBRATED_S3_PREFIX ?= squishy/calibrated

# Step 1: generate + bench calibrated files (run-bench.py handles both)
$(BUILD)/bench/calibrated-measurements.csv: scripts/run-bench.py \
        squishy/generators/calibrated.py \
        squishy/corpus/metrics.py squishy/corpus/measure.py
	@mkdir -p $(BUILD)/bench $(BUILD)/raw/calibrated
	@uv run scripts/run-bench.py

# Step 2: select one representative file per cell, symlink into curated/
$(BUILD)/.curated-selected: scripts/select-curated.py \
        $(BUILD)/bench/calibrated-measurements.csv \
        $(BUILD)/bench/corpus-measurements.csv
	@uv run scripts/select-curated.py
	@touch $@

# Step 3: hardlink files into build/bundle/, write manifest.csv + ground-truth.json
$(BUILD)/bundle/manifest.csv: scripts/build-corpus-bundle.py \
        $(BUILD)/.curated-selected
	@uv run scripts/build-corpus-bundle.py

# Step 4: generate index.html from template + live manifest counts
$(BUILD)/bundle/index.html: scripts/build-bundle-html.py scripts/bundle-index.html \
        $(BUILD)/bundle/manifest.csv
	@uv run scripts/build-bundle-html.py

.PHONY: calibrated-bundle calibrated-html calibrated-publish calibrated-invalidate

calibrated-bundle: $(BUILD)/bundle/manifest.csv $(BUILD)/bundle/index.html

calibrated-html: $(BUILD)/bundle/index.html

# Publish: binary files with immutable cache; index files with short TTL.
# Separate cp commands for index files avoid the "include after exclude" trap.
calibrated-publish: calibrated-bundle
	@$(AWS) s3 sync $(BUILD)/bundle/ \
	  s3://$(S3_BUCKET)/$(CALIBRATED_S3_PREFIX)/ \
	  --storage-class ONEZONE_IA \
	  --follow-symlinks \
	  --exclude "index.html" \
	  --exclude "manifest.csv" \
	  --exclude "ground-truth.json" \
	  --exclude "score.py" \
	  --cache-control "$(CC_IMMUTABLE)"
	@for f in index.html manifest.csv ground-truth.json score.py; do \
	  ct=application/octet-stream; \
	  [[ "$$f" == *.html ]] && ct="text/html; charset=utf-8"; \
	  [[ "$$f" == *.csv  ]] && ct="text/csv; charset=utf-8"; \
	  [[ "$$f" == *.json ]] && ct="application/json"; \
	  [[ "$$f" == *.py   ]] && ct="text/x-python; charset=utf-8"; \
	  $(AWS) s3 cp $(BUILD)/bundle/$$f \
	    s3://$(S3_BUCKET)/$(CALIBRATED_S3_PREFIX)/$$f \
	    --storage-class ONEZONE_IA \
	    --cache-control "$(CC_INDEX)" \
	    --content-type "$$ct"; \
	done

calibrated-invalidate:
	@if [[ -z "$(CLOUDFRONT_DIST)" ]]; then echo "skip invalidate: CLOUDFRONT_DIST empty"; exit 0; fi
	@$(AWS) cloudfront create-invalidation \
	  --distribution-id $(CLOUDFRONT_DIST) \
	  --paths \
	    /$(CALIBRATED_S3_PREFIX)/index.html \
	    /$(CALIBRATED_S3_PREFIX)/manifest.csv \
	    /$(CALIBRATED_S3_PREFIX)/ground-truth.json \
	    /$(CALIBRATED_S3_PREFIX)/score.py

# ─── v4 corpus: calibration sweep + benchmark ─────────────────────────────────

V4_CAL_DIR := build/raw/synthetic/calibration
V4_BENCH_DIR := build/bench

# Run calibration sweep: generate ~84 synthetic files at 4 MB, measure H and S
v4-calibrate:
	@mkdir -p $(V4_CAL_DIR)
	@uv run scripts/gen-synthetic.py --calibrate-only --out build/raw/synthetic

# Run v4 benchmark: compress calibration files, compute Kendall-τ
v4-bench: v4-calibrate
	@mkdir -p $(V4_BENCH_DIR)
	@uv run scripts/bench-v4.py --input $(V4_CAL_DIR) --out-dir $(V4_BENCH_DIR)

V4_BAL_DIR := build/raw/synthetic/balanced
V4_BAL_BENCH_DIR := build/bench/balanced

# Generate balanced corpus: 5 files per reachable (H_bin, S_bin) cell
v4-balanced: v4-calibrate
	@mkdir -p $(V4_BAL_DIR)
	@uv run scripts/gen-balanced.py

# Benchmark balanced corpus and compute Kendall-τ
v4-bench-balanced: v4-balanced
	@mkdir -p $(V4_BAL_BENCH_DIR)
	@uv run scripts/bench-v4.py --input $(V4_BAL_DIR) --out-dir $(V4_BAL_BENCH_DIR)

# Full v4 pipeline: calibrate → balanced → benchmark
v4: v4-bench-balanced

# Run test suite
v4-test:
	@uv run pytest tests/ -q

# ─── Squishy Score (the 2026 corpus + citable score) ─────────────────────────
.PHONY: score board site validate audit pii freeze
score board:
	@uv run squishy board
site:
	@uv run python scripts/build-site.py
validate:
	@uv run python scripts/validate-core.py
audit:
	@uv run python scripts/audit-distribution.py --prefix draft
pii:
	@uv run python scripts/pii-scan.py
freeze:
	@echo "OWNER (irreversible, after counsel sign-off):"
	@echo "  $(AWS_VAULT) bash scripts/freeze.sh $(S3_BUCKET) --confirm"
	@echo "  ZENODO_TOKEN=<fresh> uv run python scripts/zenodo-deposit.py"

# Stream the published corpus and compute a Squishy Score for any codec:
#   make calculate CMD="zstd -19 -c"
#   make calculate CMD="xz -9 -c" VERIFY="--verify --decompress 'xz -dc'"
calculate:
	uv run python scripts/squishy-calculate.py --cmd "$(CMD)" $(VERIFY)
