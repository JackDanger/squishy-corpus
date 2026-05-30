"""Command-line interface for the squishy corpus builder.

Usage:
    python -m squishy doctor
    python -m squishy build [target] [--force] [--clean]
    python -m squishy verify
    python -m squishy publish [--dry-run]
    python -m squishy invalidate [--dist DIST_ID]

Common options (accepted by every subcommand):
    --build DIR       build root directory (default: build)
    --bucket BUCKET   S3 bucket name (default: jackdanger.com)
    --prefix PREFIX   key prefix under bucket (default: squishy)
    --workers N       parallel worker count (default: 4)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from squishy.core.config import BuildConfig

# ── argument helpers ─────────────────────────────────────────────────────────

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--build",   default="build",          metavar="DIR")
    parser.add_argument("--bucket",  default="jackdanger.com",  metavar="BUCKET")
    parser.add_argument("--prefix",  default="squishy",         metavar="PREFIX")
    parser.add_argument("--workers", default=4, type=int,       metavar="N")


BUILD_TARGETS = [
    "sources",      # fetch upstream archives
    "raw",          # run all generators → build/raw/
    "individual",   # per-file compression → build/individual/
    "bundles",      # archive builders → build/bundles/
    "dict",         # zstd dictionaries → build/dict/
    "negative",     # negative fixtures → build/negative/
    "profile",      # statistical profiles → build/meta/profile.json
    "manifest",     # manifest.json, CHECKSUMS, etc.
    "stats",        # baselines.json, stats.json
    "all",          # all of the above in order
]

# ── parser ───────────────────────────────────────────────────────────────────

def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squishy",
        description="Squishy corpus builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # doctor
    p = sub.add_parser("doctor", help="check toolchain, write tools.lock")
    _add_common(p)

    # build
    p = sub.add_parser("build", help="build corpus artifacts")
    _add_common(p)
    p.add_argument(
        "target", nargs="?", default="all",
        choices=BUILD_TARGETS, metavar="TARGET",
        help=f"step to build (default: all). One of: {', '.join(BUILD_TARGETS)}",
    )
    p.add_argument("--force", action="store_true",
                   help="ignore stamps, re-run even if up to date")
    p.add_argument("--clean", action="store_true",
                   help="wipe the build cache before running")

    # verify
    p = sub.add_parser("verify", help="verify sha256 checksums")
    _add_common(p)

    # publish
    p = sub.add_parser("publish", help="upload artifacts to S3")
    _add_common(p)
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be uploaded without uploading")

    # invalidate
    p = sub.add_parser("invalidate", help="send CloudFront invalidation")
    _add_common(p)
    p.add_argument("--dist", default="E337PUI5JFO3S1", metavar="DIST_ID",
                   help="CloudFront distribution ID")

    return parser


# ── command handlers ─────────────────────────────────────────────────────────

def _cmd_doctor(args: argparse.Namespace, cfg: BuildConfig) -> int:
    from squishy.core.tools import run_doctor
    return run_doctor(cfg)


def _cmd_build(args: argparse.Namespace, cfg: BuildConfig) -> int:
    from squishy.pipeline import run_build
    return run_build(
        args.target, cfg,
        force=args.force,
        clean=args.clean,
    )


def _cmd_verify(args: argparse.Namespace, cfg: BuildConfig) -> int:
    from squishy.meta.manifest import run_verify
    return run_verify(cfg)


def _cmd_publish(args: argparse.Namespace, cfg: BuildConfig) -> int:
    from squishy.meta.publish import run_publish
    return run_publish(cfg, dry_run=args.dry_run)


def _cmd_invalidate(args: argparse.Namespace, cfg: BuildConfig) -> int:
    from squishy.meta.publish import run_invalidate
    return run_invalidate(cfg, dist_id=args.dist)


# ── entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    # `squishy bench|board|score ...` → delegate to the Squishy Score runner
    # (separate from the corpus build pipeline below).
    _argv = sys.argv[1:] if argv is None else argv
    if _argv and _argv[0] in ("bench", "board", "score"):
        from squishy import score
        sub = "board" if _argv[0] == "score" else _argv[0]
        return score.main([sub, *_argv[1:]])

    parser = _make_parser()
    args = parser.parse_args(argv)
    cfg = BuildConfig.from_args(args)

    dispatch = {
        "doctor":     _cmd_doctor,
        "build":      _cmd_build,
        "verify":     _cmd_verify,
        "publish":    _cmd_publish,
        "invalidate": _cmd_invalidate,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
