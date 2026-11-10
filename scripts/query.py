#!/usr/bin/env python3
"""Query the FinanceDatabase Equities database.

Generic by design: every filter value is a CLI argument, nothing is
hardcoded beyond the pilot defaults below. Market-agnostic — the same
script queries any country / sector the database offers.

Pilot defaults:
  * Equities filtered to country = Egypt
  * Equities filtered to country = United States + sector = Information Technology
    (in financedatabase >= 2.x the GICS-style sector is called
    "Information Technology"; a plain "Technology" attempt is also made
    and recorded if the primary sector fails or returns nothing)

Adapts to the installed financedatabase version:
  * current (>= 2.x): fd.Equities(), .select(country=..., sector=...),
    .show_options(selection=...)
  * legacy (1.x):     fd.Equities(), .select(...), .options(...)
  * module-level helpers (fd.select_equities / fd.show_options) are
    tried as fallbacks when present.

Outputs (under --output-dir, default results/):
  * egypt_equities.csv       — full Egypt result, or first --limit rows
  * us_sector_equities.csv   — first --limit rows of the US sector result
  * country_options.json     — country values the database offers
  * sector_options.json      — sector values the database offers
  * summary.json             — counts, samples, attempts and exact errors

A query that returns nothing or raises is recorded verbatim in
summary.json; the script still writes its outputs and exits 0 so the
workflow artifact always carries the documented outcome.
"""

import argparse
import json
import sys
import traceback
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--egypt-country", default="Egypt",
                   help="Country value for the first query (default: Egypt)")
    p.add_argument("--us-country", default="United States",
                   help="Country value for the second query (default: United States)")
    p.add_argument("--us-sector", default="Information Technology",
                   help="Sector value for the second query "
                        "(default: Information Technology)")
    p.add_argument("--fallback-sector", default="Technology",
                   help="Sector tried if --us-sector fails / is empty "
                        "(default: Technology; empty string disables)")
    p.add_argument("--limit", type=int, default=50,
                   help="Max rows saved per CSV (default: 50)")
    p.add_argument("--sample", type=int, default=5,
                   help="Sample rows recorded in summary.json (default: 5)")
    p.add_argument("--output-dir", default="results",
                   help="Output directory (default: results)")
    return p.parse_args()


def to_str_list(value):
    """Normalise an options return value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, dict):
        # show_options() with no selection returns a dict of arrays.
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def get_options(equities, fd_module, selection):
    """Return (values, method_used, errors) for one options selection."""
    errors = []
    attempts = []

    if equities is not None:
        attempts.append(("show_options(selection=...)", lambda: equities.show_options(selection=selection)))
        attempts.append(("show_options(selection positional)", lambda: equities.show_options(selection)))
        if hasattr(equities, "options"):
            attempts.append(("options(selection)", lambda: equities.options(selection)))
            attempts.append(("options(selection=...)", lambda: equities.options(selection=selection)))
    if fd_module is not None and hasattr(fd_module, "show_options"):
        attempts.append(("fd.show_options('equities', selection)",
                         lambda: fd_module.show_options("equities", selection)))
        attempts.append(("fd.show_options('equities') dict",
                         lambda: fd_module.show_options("equities")[selection]))

    for name, fn in attempts:
        try:
            result = fn()
            if isinstance(result, dict):
                result = result.get(selection)
            values = to_str_list(result)
            if values:
                return values, name, errors
            errors.append(f"{name}: returned no values")
        except Exception as exc:  # noqa: BLE001 - recorded verbatim
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return [], None, errors


def do_select(equities, fd_module, **filters):
    """Run a select, trying class then module-level APIs. Returns a frame."""
    errors = []
    if equities is not None and hasattr(equities, "select"):
        try:
            return equities.select(**filters), "Equities.select", errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Equities.select: {type(exc).__name__}: {exc}")
    if fd_module is not None:
        for name in ("select_equities", "select"):
            fn = getattr(fd_module, name, None)
            if callable(fn):
                try:
                    return fn(**filters), f"fd.{name}", errors
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"fd.{name}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors) if errors else "no select API found")


def frame_to_pandas(frame):
    """Normalise a financedatabase result to a pandas DataFrame with a
    'symbol' column (the native result is indexed by symbol)."""
    if frame is None:
        return None
    # Polars frame
    if hasattr(frame, "to_pandas") and not hasattr(frame, "index"):
        frame = frame.to_pandas()
    import pandas as pd  # local import so import errors are diagnosable

    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)
    df = frame.copy()
    if "symbol" not in [str(c).lower() for c in df.columns]:
        index_name = df.index.name or "symbol"
        df = df.reset_index().rename(columns={df.columns[0] if len(df.columns) else "index": index_name})
        if "symbol" not in df.columns and index_name in df.columns:
            df = df.rename(columns={index_name: "symbol"})
    return df


def samples_from(df, n):
    """First n rows as dicts with symbol + name (+ context columns)."""
    out = []
    if df is None:
        return out
    cols = {str(c).lower(): c for c in df.columns}
    for _, row in df.head(n).iterrows():
        item = {}
        for wanted in ("symbol", "name", "country", "sector", "industry", "exchange", "currency"):
            if wanted in cols:
                val = row[cols[wanted]]
                item[wanted] = None if val is None or (isinstance(val, float) and val != val) else str(val)
        if item:
            out.append(item)
    return out


def run_query(equities, fd_module, label, filters, args, out_dir, csv_name):
    """Execute one query, save its CSV, and return its summary block."""
    block = {"filter": filters, "status": None, "count": None,
             "method": None, "attempt_errors": [], "error": None,
             "columns": [], "samples": [], "file": None, "saved_rows": 0}
    try:
        frame, method, attempt_errors = do_select(equities, fd_module, **filters)
        block["method"] = method
        block["attempt_errors"] = attempt_errors
        df = frame_to_pandas(frame)
        block["count"] = int(len(df)) if df is not None else 0
        block["columns"] = [str(c) for c in df.columns] if df is not None else []
        block["samples"] = samples_from(df, args.sample)
        if df is not None and len(df):
            saved = df.head(args.limit)
            path = out_dir / csv_name
            saved.to_csv(path, index=False)
            block["file"] = csv_name
            block["saved_rows"] = int(len(saved))
            block["status"] = "ok"
        else:
            block["status"] = "empty"
            # Still write a header-only CSV so the outcome is explicit.
            path = out_dir / csv_name
            (df if df is not None else __import__("pandas").DataFrame()).to_csv(path, index=False)
            block["file"] = csv_name
    except Exception as exc:  # noqa: BLE001 - documented, not hidden
        block["status"] = "error"
        block["error"] = f"{type(exc).__name__}: {exc}"
        block["traceback"] = traceback.format_exc()
    return block


def main():
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "tool": "financedatabase",
        "financedatabase_version": None,
        "queries": {},
        "country_options": {"count": None, "values": [], "method": None, "errors": []},
        "sector_options": {"count": None, "values": [], "method": None, "errors": []},
        "egypt_country_available": None,
        "notes": [],
    }

    fd = None
    equities = None
    try:
        import financedatabase as fd  # noqa: F811
        summary["financedatabase_version"] = getattr(fd, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        summary["import_error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return 0

    try:
        if hasattr(fd, "Equities"):
            equities = fd.Equities()
        else:
            summary["notes"].append("fd.Equities not found; module-level select will be tried")
    except Exception as exc:  # noqa: BLE001
        summary["equities_init_error"] = f"{type(exc).__name__}: {exc}"
        summary["traceback"] = traceback.format_exc()

    # --- Options (cheap, and required to judge the Egypt filter) ---
    country_values, country_method, country_errors = get_options(equities, fd, "country")
    summary["country_options"] = {
        "count": len(country_values) if country_values else 0,
        "values": country_values,
        "method": country_method,
        "errors": country_errors,
    }
    (out_dir / "country_options.json").write_text(
        json.dumps(summary["country_options"], indent=2))

    sector_values, sector_method, sector_errors = get_options(equities, fd, "sector")
    summary["sector_options"] = {
        "count": len(sector_values) if sector_values else 0,
        "values": sector_values,
        "method": sector_method,
        "errors": sector_errors,
    }
    (out_dir / "sector_options.json").write_text(
        json.dumps(summary["sector_options"], indent=2))

    # Resolve the exact Egypt spelling from the options, if listed.
    egypt_value = args.egypt_country
    if country_values:
        match = [v for v in country_values if v.lower() == args.egypt_country.lower()]
        summary["egypt_country_available"] = bool(match)
        if match:
            egypt_value = match[0]
        else:
            summary["notes"].append(
                f"'{args.egypt_country}' is not among the {len(country_values)} "
                "country options; the select below was still attempted and its "
                "exact outcome recorded.")
    else:
        summary["notes"].append("Country options could not be obtained; "
                                 "the Egypt select was attempted directly.")

    # --- (a) Egypt equities ---
    summary["queries"]["egypt"] = run_query(
        equities, fd, "egypt", {"country": egypt_value}, args, out_dir,
        "egypt_equities.csv")

    # --- (b) US sector equities, with a recorded fallback attempt ---
    us_attempts = [args.us_sector]
    if args.fallback_sector and args.fallback_sector != args.us_sector:
        us_attempts.append(args.fallback_sector)
    us_block = None
    all_attempts = []
    for sector in us_attempts:
        block = run_query(
            equities, fd, "us_sector",
            {"country": args.us_country, "sector": sector},
            args, out_dir, "us_sector_equities.csv")
        all_attempts.append({"sector": sector, "status": block["status"],
                             "count": block["count"], "error": block["error"]})
        us_block = block
        if block["status"] == "ok" and (block["count"] or 0) > 0:
            break
    us_block["attempts"] = all_attempts
    if len(all_attempts) > 1:
        summary["notes"].append(
            f"US sector: tried { [a['sector'] for a in all_attempts] } in order; "
            f"final sector used: {us_block['filter'].get('sector')}")
    summary["queries"]["us_sector"] = us_block

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
