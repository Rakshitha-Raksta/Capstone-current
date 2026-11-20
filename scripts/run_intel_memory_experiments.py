"""
Evaluate historical-case intelligence and persistent issue memory on the EXISTING synthetic experiment
cohorts (read only). Writes a NEW folder; never touches recorded results.

    python scripts/run_intel_memory_experiments.py                       # seeds 1-3 (main) + 4-6 (main_heldout)
    python scripts/run_intel_memory_experiments.py --out results/experiments/intel_memory_v2 --seeds main:1

See backend/app/experiments/intel_memory_eval.py for the protocol.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "experiments", "intel_memory_v1"))
    ap.add_argument("--seeds", default="main:1,main:2,main:3,main_heldout:4,main_heldout:5,main_heldout:6")
    ap.add_argument("--k-pred", type=int, default=25)
    a = ap.parse_args()
    runs = [(x.split(":")[0], int(x.split(":")[1])) for x in a.seeds.split(",")]
    from app.experiments.intel_memory_eval import run
    s = run(os.path.abspath(a.out), runs, log=lambda m: print(m, flush=True), k_pred=a.k_pred)
    print(json.dumps(s["aggregate"]["all"], indent=1))


if __name__ == "__main__":
    main()
