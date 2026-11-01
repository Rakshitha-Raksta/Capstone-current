"""
Download the OPEN-ACCESS MIMIC-IV Clinical Database Demo v2.2 (ODbL v1.0) from
PhysioNet into data/mimic_iv/demo/ and verify every file against PhysioNet's
published SHA256SUMS.txt.

Only this open demo is supported. Credentialed MIMIC-IV / MIMIC-IV-Note must be
obtained by the user through PhysioNet's credentialing process; this script
will not fetch anything else.

    python scripts/download_mimic_demo.py            # hosp tables ClearClaim reads + licence/readme
    python scripts/download_mimic_demo.py --all-hosp # every hosp table in the demo

The files are git-ignored. Do not redistribute them without honouring the
ODbL licence (attribution, share-alike) shown in LICENSE.txt.
"""
import argparse
import hashlib
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://physionet.org/files/mimic-iv-demo/2.2/"
OUT = os.path.join(ROOT, "data", "mimic_iv", "demo")
NEEDED = ["LICENSE.txt", "README.txt", "demo_subject_id.csv"] + [
    f"hosp/{t}.csv.gz" for t in ("patients", "admissions", "diagnoses_icd", "procedures_icd", "drgcodes",
                                 "d_icd_diagnoses", "d_icd_procedures", "transfers", "prescriptions", "services")]


def fetch(rel: str, attempts: int = 4) -> bytes:
    url = BASE + rel
    assert url.startswith(BASE), url
    req = urllib.request.Request(url, headers={"User-Agent": "clearclaim-capstone/0.3 (open demo download)"})
    last = None
    for i in range(attempts):  # PhysioNet transfers occasionally drop; retry a bounded number of times
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception as e:
            last = e
            print(f"  retry {i + 1}/{attempts} for {rel}: {type(e).__name__}")
    raise last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-hosp", action="store_true")
    a = ap.parse_args()
    sums = {}
    for line in fetch("SHA256SUMS.txt").decode().splitlines():
        h, name = line.split(maxsplit=1)
        sums[name.strip()] = h
    wanted = NEEDED + ([n for n in sums if n.startswith("hosp/")] if a.all_hosp else [])
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"{h} {n}\n" for n, h in sorted(sums.items())))
    ok = True
    for rel in sorted(set(wanted)):
        if rel not in sums:
            print(f"SKIP {rel}: not listed in SHA256SUMS.txt")
            continue
        dest = os.path.join(OUT, rel.replace("/", os.sep))
        if os.path.exists(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == sums[rel]:
            print(f"OK   {rel} (already present)")
            continue
        data = fetch(rel)
        digest = hashlib.sha256(data).hexdigest()
        if digest != sums[rel]:
            print(f"FAIL {rel}: sha256 {digest} != published {sums[rel]}")
            ok = False
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        print(f"OK   {rel} ({len(data):,} bytes, sha256 verified)")
    print(f"\nFiles in {OUT}. Licence: ODbL v1.0 (see LICENSE.txt).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
