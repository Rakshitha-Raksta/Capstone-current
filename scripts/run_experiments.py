"""
Run the controlled experiments from the command line and write
results/experiments/<name>/summary.json plus raw per-case JSON.

    python scripts/run_experiments.py --name repro         # 3 seeds x 200 encounters, all arms
    python scripts/run_experiments.py --seeds 1 --n 50     # quick run
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))

from app.experiments.harness import run_experiment, ARMS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="main")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--error-rate", type=float, default=0.35)
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    ap.add_argument("--mimic-root", default=None,
                    help="Run on real MIMIC-format encounters (e.g. data/mimic_iv/demo); admin layer is synthetic")
    ap.add_argument("--overwrite", action="store_true",
                    help="Allow writing into a results folder that already holds a summary.json")
    a = ap.parse_args()
    out = os.path.join(ROOT, "results", "experiments", a.name)
    if os.path.exists(os.path.join(out, "summary.json")) and not a.overwrite:
        sys.exit(f"{out} already contains recorded results; choose another --name or pass --overwrite.")
    res = run_experiment(out, seeds=a.seeds, n_encounters=a.n, error_rate=a.error_rate, arms=a.arms,
                         log=lambda m: print(m, flush=True),
                         mimic_root=os.path.join(ROOT, a.mimic_root) if a.mimic_root and not os.path.isabs(a.mimic_root) else a.mimic_root)
    print(json.dumps(res["aggregate"], indent=2))
    print(f"elapsed {res['elapsed_s']} s -> {out}/summary.json")


if __name__ == "__main__":
    main()
