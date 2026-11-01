"""
Generate the reproducible SYNTHETIC demo dataset, or the synthetic
insurance/admin layer for a real MIMIC-IV directory.

Synthetic demo (default; no downloads needed):
    python scripts/generate_synthetic_data.py
    python scripts/generate_synthetic_data.py --seed 7 --n-encounters 500 --out data/synthetic/demo_seed7

Admin layer for the real MIMIC-IV Clinical Database Demo (after downloading it,
see the MIMIC-IV section of README.md):
    python scripts/generate_synthetic_data.py --mimic-root data/mimic_iv/demo \
        --out data/synthetic/admin_for_mimic_demo
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.datasets.synthetic import generate_synthetic_dataset, generate_admin_layer_for_mimic  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "synthetic", "demo")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-encounters", type=int, default=200)
    ap.add_argument("--error-rate", type=float, default=0.35,
                    help="Fraction of encounters that receive 1-2 injected data errors")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mimic-root", default=None,
                    help="Real MIMIC-format directory (containing hosp/). Generates ONLY the admin layer.")
    ap.add_argument("--source-label", default="MIMIC_IV_DEMO_2.2")
    ap.add_argument("--max-encounters", type=int, default=None)
    args = ap.parse_args()

    if args.mimic_root:
        out = args.out or os.path.join(ROOT, "data", "synthetic", "admin_for_mimic_demo")
        m = generate_admin_layer_for_mimic(args.mimic_root, out, args.seed, args.error_rate,
                                           args.source_label, args.max_encounters)
    else:
        out = args.out or DEFAULT_OUT
        m = generate_synthetic_dataset(out, args.seed, args.n_encounters, args.error_rate)

    print(f"Mode: {m['mode']}  ->  {out}")
    for rel, info in m["files"].items():
        print(f"  {rel:40s} {info['rows']:>7} rows  sha256={info['sha256'][:12]}")


if __name__ == "__main__":
    main()
