#!/usr/bin/env bash
# Squishy end-to-end reproduction + verification.
#
# "Verification" here means CLEAN-ROOM REPRODUCTION against a committed golden record
# (build/meta/baseline.json), not re-proving that lossless codecs are lossless. The
# pipeline regenerates every derived artifact from sources, proves the constructed
# files rebuild byte-for-byte, re-derives the reference Squishy Score over the FULL
# edition with one round-trip-verified pass, certifies every other panel codec on a
# small vector, then diffs the whole result against the baseline.
#
#   bash scripts/run-all.sh            # uses the local byte cache where present
#   SQUISHY_FRESH=1 bash scripts/run-all.sh   # ignore cached per-file results
set -uo pipefail
cd "$(dirname "$0")/.."
log(){ printf '\n\033[1m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
FAIL=0
FRESH=${SQUISHY_FRESH:+--fresh}

log "1/7  derived files reproduce byte-for-byte (freeze blocker)"
uv run --with pyarrow python scripts/verify-derived-reproducible.py || FAIL=1

log "2/7  core byte-properties"
uv run python scripts/file-properties.py >/dev/null && echo "  ok"

log "3/7  edition manifest (single source of truth)"
uv run python scripts/build-edition-manifest.py

log "4/7  reference board — fast panel over the small members"
uv run python scripts/board-live.py >/dev/null && echo "  ok (build/meta/squishy-scores.json)"

log "5/7  reference codec over the COMPLETE edition, round-trip verified"
uv run python scripts/squishy-calculate.py --cmd "zstd -19 -c" --verify --decompress "zstd -dc" --json $FRESH \
  2>/dev/null | uv run python scripts/_capture-complete.py || FAIL=1

log "6/7  panel codecs are lossless on this host (small vector)"
uv run python scripts/verify-codecs-sane.py || FAIL=1

log "7/7  diff the whole result against the golden baseline"
uv run python scripts/check-baseline.py || FAIL=1

echo
if [ "$FAIL" -eq 0 ]; then
  log "RESULT: PASS — corpus reproduced and verified against build/meta/baseline.json"
else
  log "RESULT: FAIL — see mismatches above"
fi
exit $FAIL
