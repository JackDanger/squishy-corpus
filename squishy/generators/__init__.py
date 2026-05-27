"""Generators package: each submodule produces a category of raw input files.

Public contract: every submodule exposes ``run(cfg: BuildConfig) -> int``
where 0 means success and 1 means failure.
"""
