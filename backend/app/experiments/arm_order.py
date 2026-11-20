"""
Balanced arm order across seeds (Williams Latin square). Pure functions.

Design
  * Arms are first put in a canonical order (the order of harness.ALL_ARMS), so the order arms are
    typed on the command line never changes the design.
  * For k arms, rows are a Williams design: row 0 = 0, 1, k-1, 2, k-2, ...; row r = row 0 shifted by r
    (mod k). For odd k the mirrored rows are appended (2k rows), which is needed for first-order
    carry-over balance.
  * A seed uses row  seed mod len(rows).  The order of a seed depends only on its own value and the
    arm set, never on which other seeds are in the run (so re-running one seed reproduces its order).
  * Position balance is exact when the run's seeds cover every residue mod len(rows) equally often
    (e.g. 10 consecutive seeds for 5 arms). position_balance() reports the realised counts so an
    unbalanced seed set is visible rather than assumed.

Arm order only decides which isolated database copy is processed first; every arm starts from the
same base database, so order cannot change cohorts, actions or outcomes (only timing).
"""
from collections import Counter
from typing import Dict, List, Sequence


def williams_rows(k: int) -> List[List[int]]:
    if k < 1:
        raise ValueError("need at least one arm")
    first, lo, hi, take_lo = [0], 1, k - 1, True
    while len(first) < k:
        if take_lo:
            first.append(lo)
            lo += 1
        else:
            first.append(hi)
            hi -= 1
        take_lo = not take_lo
    rows = [[(x + r) % k for x in first] for r in range(k)]
    if k % 2 == 1 and k > 1:
        rows += [list(reversed(row)) for row in rows]
    return rows


def canonical(arms: Sequence[str], universe: Sequence[str]) -> List[str]:
    unknown = [a for a in arms if a not in universe]
    if unknown:
        raise ValueError(f"unknown arm(s) {unknown}")
    if len(set(arms)) != len(arms):
        raise ValueError("duplicate arm names")
    return [a for a in universe if a in set(arms)]


def order_for_seed(seed: int, arms: Sequence[str], universe: Sequence[str]) -> List[str]:
    arms = canonical(arms, universe)
    rows = williams_rows(len(arms))
    return [arms[i] for i in rows[int(seed) % len(rows)]]


def position_balance(orders: Dict[int, List[str]]) -> dict:
    """Realised position counts per arm and whether every arm sits in every position equally often."""
    counts: Dict[str, Counter] = {}
    for order in orders.values():
        for pos, arm in enumerate(order):
            counts.setdefault(arm, Counter())[pos] += 1
    k = max((len(o) for o in orders.values()), default=0)
    table = {a: [c.get(p, 0) for p in range(k)] for a, c in counts.items()}
    flat = [x for row in table.values() for x in row]
    return dict(counts=table, balanced=bool(flat) and len(set(flat)) == 1,
                rows_in_design=len(williams_rows(k)) if k else 0)
