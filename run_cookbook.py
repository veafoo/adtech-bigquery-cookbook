#!/usr/bin/env python3
"""Run every cookbook recipe on BigQuery and freeze or check its results.

A recipe is one file in recipes/ made of blocks:
  -- @fixture          optional CTE list shared by every query of the file
  -- @query <name>     one query (naive, fixed, or a proof query)

The fixture is prepended to each query as `WITH <fixture>`, so a query can
name the fixture tables as if they existed. `--print` shows the exact SQL sent.

Usage:
  python3 run_cookbook.py --freeze            # run all, write expected/*.json
  python3 run_cookbook.py                     # run all, compare to expected/
  python3 run_cookbook.py --only 01           # one recipe (prefix match); --only 14,15 for several
  COOKBOOK_SYNTH_DATASET=<project>.<dataset> python3 run_cookbook.py --only 14,15
  python3 run_cookbook.py --print 01 fixed    # print the composed SQL
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECIPES = ROOT / "recipes"
EXPECTED = ROOT / "expected"
MAX_BYTES = 2_000_000_000  # refuse any query that would scan more than 2 GB

BLOCK_RE = re.compile(r"^-- @(fixture|query)\s*(\S*)\s*$", re.M)
BYTES_RE = re.compile(r"process (\d+) bytes")

# Recipes on the synthetic data (synth/) name their tables `@@SYNTH@@.<table>`; the placeholder
# becomes the dataset in COOKBOOK_SYNTH_DATASET (<project>.<dataset>, loaded by synth/load.sh).
# Without that variable those recipes are skipped, never failed.
SYNTH_PLACEHOLDER = "@@SYNTH@@"
SYNTH_ENV = "COOKBOOK_SYNTH_DATASET"
SYNTH_DATASET_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def split_blocks(text):
    """Return (fixture or None, {query_name: sql}) from a recipe file."""
    fixture, queries = None, {}
    marks = list(BLOCK_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if m.group(1) == "fixture":
            fixture = body
        else:
            if not m.group(2):
                raise ValueError("-- @query needs a name")
            if m.group(2) in queries:
                raise ValueError(f"duplicate query name {m.group(2)}")
            queries[m.group(2)] = body
    return fixture, queries


def _strip_leading_comments(sql):
    lines = sql.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    return "\n".join(lines)


def compose(fixture, sql):
    """Prepend the fixture CTEs to a query, merging with its own WITH."""
    # leading comments are dropped: bq reads an argument starting with "--" as a flag
    body = _strip_leading_comments(sql.rstrip().rstrip(";"))
    if not fixture:
        return body
    if re.match(r"(?i)with\b", body):
        return f"WITH {fixture},\n{body[4:].lstrip()}"
    return f"WITH {fixture}\n{body}"


def parse_dry_run(output):
    m = BYTES_RE.search(output)
    if not m:
        raise RuntimeError(f"dry run output not understood: {output.strip()[:200]}")
    return int(m.group(1))


def _bq(args, sql):
    r = subprocess.run(["bq", "query", "--use_legacy_sql=false", "--quiet", *args, sql],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip()[:500])
    return r.stdout


def run_query(sql):
    nbytes = parse_dry_run(_bq(["--dry_run"], sql))
    if nbytes > MAX_BYTES:
        raise RuntimeError(f"refused: {nbytes} bytes > MAX_BYTES {MAX_BYTES}")
    out = _bq(["--format=json", "--max_rows=10000"], sql).strip()
    return {"bytes": nbytes, "rows": json.loads(out) if out else []}


def needs_synth(text):
    return SYNTH_PLACEHOLDER in text


def synth_dataset():
    """The dataset from the environment, or None. A malformed value is an error, not a skip."""
    value = os.environ.get(SYNTH_ENV, "").strip()
    if not value:
        return None
    if not SYNTH_DATASET_RE.match(value):
        raise ValueError(f"{SYNTH_ENV}={value!r} is not a <project>.<dataset> name")
    return value


def apply_synth(text, dataset):
    return text.replace(SYNTH_PLACEHOLDER, dataset)


def recipe_files(only=None):
    files = sorted(RECIPES.glob("*.sql"))
    prefixes = tuple(p for p in (only or "").split(",") if p)
    return [f for f in files if not prefixes or f.name.startswith(prefixes)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--print", nargs=2, metavar=("RECIPE", "QUERY"))
    a = ap.parse_args(argv)

    if a.print:
        f = recipe_files(a.print[0])[0]
        text = f.read_text()
        if needs_synth(text) and synth_dataset():
            text = apply_synth(text, synth_dataset())
        fixture, queries = split_blocks(text)
        print(compose(fixture, queries[a.print[1]]))
        return 0

    EXPECTED.mkdir(exist_ok=True)
    failures = 0
    dataset = synth_dataset()
    for f in recipe_files(a.only):
        text = f.read_text()
        if needs_synth(text):
            if not dataset:
                print(f"SKIP {f.stem}: set {SYNTH_ENV}=<project>.<dataset> (load it with synth/load.sh)")
                continue
            text = apply_synth(text, dataset)
        fixture, queries = split_blocks(text)
        results = {}
        for name, sql in queries.items():
            try:
                results[name] = run_query(compose(fixture, sql))
            except RuntimeError as e:
                print(f"ERROR {f.stem}:{name}: {e}")
                failures += 1
        target = EXPECTED / f"{f.stem}.json"
        if a.freeze:
            target.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
            print(f"frozen {f.stem}: " + ", ".join(f"{k}={len(v['rows'])} rows/{v['bytes']} B"
                                                   for k, v in results.items()))
        elif not target.exists():
            print(f"MISSING {target.name}")
            failures += 1
        else:
            want = json.loads(target.read_text())
            for name in sorted(set(want) | set(results)):
                if want.get(name) != results.get(name):
                    print(f"DIFF {f.stem}:{name}")
                    failures += 1
            print(f"checked {f.stem}")
    print("FAIL" if failures else "OK", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
