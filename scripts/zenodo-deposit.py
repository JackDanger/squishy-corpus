#!/usr/bin/env python3
"""Mint the Squishy-2026 Zenodo DOI. OWNER-RUN at freeze.

Reads ZENODO_TOKEN from the environment (NEVER hard-code it; never commit it).
Creates a deposition, uploads the citable artifacts (core tarball + checksums +
license manifest + NOTICE + LICENSES), sets metadata, and RESERVES a DOI.
It does NOT auto-publish — review on zenodo.org, then click Publish (or pass
--publish) to mint the final DOI.

  ZENODO_TOKEN=xxxx uv run python scripts/zenodo-deposit.py [--sandbox] [--publish]
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARTIFACTS = [
    REPO / "build" / "meta" / "squishy-2026.tar",
    REPO / "build" / "meta" / "CHECKSUMS.sha256",
    REPO / "build" / "meta" / "LICENSE-MANIFEST.csv",
    REPO / "build" / "meta" / "NOTICE",
    REPO / "build" / "meta" / "squishy-scores.json",
]

META = {
    "metadata": {
        "title": "Squishy-2026: a compression benchmark corpus",
        "upload_type": "dataset",
        "description": ("The Squishy-2026 named core: a small, curated set of real, "
                        "redistributable modern files that compress differently, plus "
                        "the Squishy Score (geometric mean of per-file compression "
                        "ratio). Successor to the Silesia corpus. See NOTICE and "
                        "LICENSE-MANIFEST.csv for per-file provenance and licenses."),
        "creators": [{"name": "Danger, Jack"}],
        "keywords": ["compression", "benchmark", "corpus", "lossless"],
        "version": "Squishy-2026",
        "access_right": "open",
    }
}


def api(base, path, token, method="GET", data=None):
    req = urllib.request.Request(f"{base}/api/{path}?access_token={token}",
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    body = json.dumps(data).encode() if data is not None else None
    with urllib.request.urlopen(req, data=body, timeout=60) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox", action="store_true", help="use sandbox.zenodo.org")
    ap.add_argument("--publish", action="store_true", help="publish (mint final DOI) — irreversible")
    args = ap.parse_args()
    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        print("ERROR: set ZENODO_TOKEN in the environment (do not commit it).", file=sys.stderr)
        return 1
    base = "https://sandbox.zenodo.org" if args.sandbox else "https://zenodo.org"
    missing = [str(a) for a in ARTIFACTS if not a.exists()]
    if missing:
        print(f"ERROR: missing artifacts: {missing}", file=sys.stderr); return 1

    # Pin the exact source revision so the DOI also covers the regeneratable tier
    # (the seeded generators + PRNG that reproduce the large/pathological files
    # live only in the repo, not in the uploaded artifacts).
    import subprocess
    def _git(*a):
        try:
            return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()
        except Exception:
            return ""
    commit = _git("rev-parse", "HEAD")
    tag = _git("describe", "--tags", "--always")
    if commit:
        repo_url = "https://github.com/JackDanger/squishy-corpus"
        META["metadata"].setdefault("related_identifiers", []).append(
            {"identifier": f"{repo_url}/tree/{commit}", "relation": "isSupplementedBy",
             "resource_type": "software", "scheme": "url"})
        META["metadata"]["description"] += (
            f" Source revision (corpus generators + PRNG that reproduce the "
            f"regeneratable tier bit-for-bit): {repo_url} @ {tag or commit}.")
        print(f"pinned source revision: {tag or commit}")

    dep = api(base, "deposit/depositions", token, "POST", {})
    dep_id = dep["id"]
    bucket = dep["links"]["bucket"]
    print(f"deposition {dep_id} created; reserved DOI: {dep['metadata'].get('prereserve_doi',{}).get('doi','(on publish)')}")
    api(base, f"deposit/depositions/{dep_id}", token, "PUT", META)
    for a in ARTIFACTS:
        req = urllib.request.Request(f"{bucket}/{a.name}?access_token={token}",
                                     method="PUT", data=a.read_bytes())
        with urllib.request.urlopen(req, timeout=600) as r:
            print(f"  uploaded {a.name} ({a.stat().st_size} bytes)")
    if args.publish:
        pub = api(base, f"deposit/depositions/{dep_id}/actions/publish", token, "POST")
        print(f"PUBLISHED. DOI: {pub.get('doi')}")
    else:
        print(f"\nDraft ready. Review at {base}/deposit/{dep_id} then publish there "
              f"(or re-run with --publish) to mint the final DOI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
