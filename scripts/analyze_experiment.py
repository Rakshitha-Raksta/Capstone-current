"""
Read-only statistical analysis of a recorded experiment folder (see backend/app/experiments/analysis.py).

    python scripts/analyze_experiment.py results/experiments/<run>
    python scripts/analyze_experiment.py results/experiments/<run> --out-dir some/new/folder

Reads summary.json, raw_<arm>_seed<k>.json and data_seed<k>/ground_truth/encounter_labels.csv; never
modifies them. Writes two NEW files (UTC-timestamped; refuses to overwrite anything):
    <out-dir>/analysis_<YYYYmmddTHHMMSSZ>.json   full results
    <out-dir>/analysis_<YYYYmmddTHHMMSSZ>.md     readable tables
Default <out-dir> = <run>/analysis/.
"""
import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.experiments.analysis import BOOTSTRAP_B, BOOTSTRAP_SEED, AlignmentError, analyse, to_markdown  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--bootstrap", type=int, default=BOOTSTRAP_B)
    ap.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    a = ap.parse_args(argv)
    d = os.path.abspath(a.run_dir)
    if not os.path.exists(os.path.join(d, "summary.json")):
        print(f"{d}: no summary.json", file=sys.stderr)
        return 2
    try:
        res = analyse(d, B=a.bootstrap, seed=a.seed)
    except AlignmentError as e:                      # incomplete / inconsistent paired data: nothing is written
        print(f"ANALYSIS REFUSED: {e}", file=sys.stderr)
        return 2
    out_dir = os.path.abspath(a.out_dir or os.path.join(d, "analysis"))
    os.makedirs(out_dir, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp, mp = (os.path.join(out_dir, f"analysis_{stamp}.{ext}") for ext in ("json", "md"))
    with open(jp, "x", encoding="utf-8") as f:          # "x": never overwrite an existing file
        json.dump(res, f, indent=2, default=str)
    with open(mp, "x", encoding="utf-8") as f:
        f.write(to_markdown(res))
    print(to_markdown(res))
    print(f"wrote {jp}\nwrote {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
