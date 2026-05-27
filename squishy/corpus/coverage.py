"""Coverage analysis: load measurement CSVs, bin files, report cell occupancy."""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

from squishy.corpus.grid import (
    H_LABELS, M_LABELS, L_LABELS,
    KNOWN_EMPTY_HM,
    KNOWN_EMPTY_HML,
    h_bin, m_bin, l_bin,
)
from squishy.corpus.measure import FIELDNAMES, _COPY_COST_THRESHOLD_BPB


def _float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_rows(csv_paths: list[Path], min_size_bytes: int = 0) -> list[dict]:
    """Load and annotate measurement rows from one or more CSV files.

    Each returned row gets extra private keys:
      _H, _M, _L          — float values
      _h_bin, _m_bin, _l_bin — integer bin indices
      _cell               — (h_bin, m_bin, l_bin) tuple
      _size               — size_bytes as int
      _label              — "corpus/filename" string
    """
    rows = []
    for path in csv_paths:
        if not path.exists():
            continue
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                size = _float(row.get("size_bytes"))
                size = 0.0 if size is None else size
                if size < min_size_bytes:
                    continue
                h = _float(row.get("H_marginal"))
                # M-axis binning source:
                # - Calibrated files with M_norm_reliable=False (H<4): use M_target
                #   (construction intent) because measured M_greedy_norm has < 10%
                #   dynamic range and is dominated by instrument noise at low H.
                # - All other files: use measured M_greedy_norm (IID-floor corrected).
                # - Fallback to raw M_greedy for rows predating the norm column.
                m_norm_reliable = row.get("M_norm_reliable", "True")
                m_target = _float(row.get("M_target"))
                is_calibrated = bool(row.get("generator"))  # calibrated rows have generator
                if (is_calibrated and m_target is not None
                        and m_norm_reliable in ("False", False, "0", "")):
                    m = m_target
                else:
                    m_norm = _float(row.get("M_greedy_norm"))
                    m = m_norm if m_norm is not None else _float(row.get("M_greedy"))
                l = _float(row.get("L_p90"))
                if h is None or m is None:
                    continue
                row["_H"] = h
                row["_M"] = m
                row["_L"] = l
                row["_size"] = int(size) if size is not None else 0
                row["_h_bin"] = h_bin(h)
                row["_m_bin"] = m_bin(m)
                row["_l_bin"] = l_bin(l)
                row["_cell"] = (row["_h_bin"], row["_m_bin"], row["_l_bin"])
                row["_label"] = f"{row.get('corpus', '?')}/{row.get('filename', '?')}"
                row["_L_ci_rel"] = _float(row.get("L_ci_rel"))
                rows.append(row)
    return rows


def best_file_for_cell(files: list[dict]) -> dict:
    """Select the best representative for a cell.

    Prefers largest file with L_ci_rel < 0.15.
    Falls back to largest file overall if none qualify.
    """
    qualified = [f for f in files
                 if f["_L_ci_rel"] is not None and f["_L_ci_rel"] < 0.15]
    pool = qualified or files
    return max(pool, key=lambda f: f["_size"])


def cells_by_file(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Group rows by cell tuple → list of files in that cell."""
    result: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        result[row["_cell"]].append(row)
    return dict(result)


def coverage_map_lines(populated: dict[tuple, list[dict]]) -> list[str]:
    """Render an H×M grid coverage table (L collapsed into symbols per cell)."""
    lines = []
    header = f"  {'':15s}" + "".join(f"  {ml:12s}" for ml in M_LABELS)
    sep    = f"  {'':15s}" + "".join(f"  {'------------':12s}" for _ in M_LABELS)
    lines += [header, sep]

    for hi, hl in enumerate(H_LABELS):
        row_str = f"  {hl:15s}"
        for mi in range(len(M_LABELS)):
            any_l = [li for li in range(len(L_LABELS))
                     if (hi, mi, li) in populated]
            if any_l:
                parts = []
                for li in any_l:
                    n = len(populated[(hi, mi, li)])
                    tag = ["S", "M", "L"][li]
                    parts.append(f"{n}{tag}")
                cell_str = "+".join(parts)
            else:
                cell_str = "."
            row_str += f"  {cell_str:12s}"
        lines.append(row_str)

    lines.append("")
    lines.append("  Legend: S=short (<10)  M=medium (10-60)  L=long (≥60)  .=empty")
    return lines


def empty_cells(populated: dict[tuple, list[dict]]) -> list[tuple[int, int, int]]:
    """List cells with no qualifying files, excluding known-physics-empty regions."""
    result = []
    for hi in range(len(H_LABELS)):
        for mi in range(len(M_LABELS)):
            for li in range(len(L_LABELS)):
                if (hi, mi, li) not in populated:
                    if (hi, mi) not in KNOWN_EMPTY_HM:
                        if (hi, mi, li) not in KNOWN_EMPTY_HML:
                            result.append((hi, mi, li))
    return result
