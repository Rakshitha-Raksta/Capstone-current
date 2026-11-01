"""
Timing metrics for one workflow run, computed only from events the orchestrator reported.

All inputs are measured with time.perf_counter() inside the orchestrator:
  stage interval   = [start_ms, end_ms] of the stage function call (ms since the run started)
  wall_ms          = run start -> end of the last wave (scheduling, hashing, stage calls, DB staging)
  end_to_end_ms    = run start -> after outcome application and the database commit

Definitions (also in docs/research/EVALUATION_PROTOCOL.md):
  stage_sum_ms         sum of durations of stages that actually executed (not reused / not re-run)
  busy_ms              length of the union of executed-stage intervals
  overhead_ms          wall_ms - busy_ms: time inside the run but outside every stage function
  max_concurrency      most executed-stage intervals open at the same instant
  overlapping_pairs    executed-stage pairs whose intervals intersect (touching ends do not count)
  critical_path_ms     longest dependency path, weighting each stage by its measured duration
                       (0 for stages that did not execute in this run)
"""
from typing import Dict, List

from app.clearclaim import dag


def intervals(events: List[dict]) -> Dict[str, dict]:
    out = {}
    for e in events:
        if e.get("type") == "stage_finished" and e.get("start_ms") is not None and e.get("end_ms") is not None:
            out[e["stage"]] = dict(start=float(e["start_ms"]), end=float(e["end_ms"]),
                                   duration=float(e["duration_ms"]), status=e["status"])
    return out


def max_concurrency(iv: Dict[str, dict]) -> int:
    pts = []
    for x in iv.values():
        pts.append((x["start"], 1))
        pts.append((x["end"], -1))
    pts.sort(key=lambda p: (p[0], p[1]))           # an end at time t closes before a start at t opens
    cur = best = 0
    for _, d in pts:
        cur += d
        best = max(best, cur)
    return best


def overlapping_pairs(iv: Dict[str, dict]) -> List[List[str]]:
    keys = [s for s in dag.STAGE_ORDER if s in iv]
    out = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if iv[a]["start"] < iv[b]["end"] and iv[b]["start"] < iv[a]["end"]:
                out.append([a, b])
    return out


def union_length(iv: Dict[str, dict]) -> float:
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted((x["start"], x["end"]) for x in iv.values()):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def critical_path(durations: Dict[str, float]) -> dict:
    """Longest path through the real dependency graph, weighted by measured stage durations."""
    best, prev = {}, {}
    for s in dag.STAGE_ORDER:
        w = durations.get(s, 0.0)
        deps = dag.STAGES[s]["deps"]
        if deps:
            d = max(deps, key=lambda x: best[x])
            best[s], prev[s] = best[d] + w, d
        else:
            best[s], prev[s] = w, None
    # end at a sink (a stage nothing depends on) so the path always runs to the end of the workflow;
    # with non-negative weights the longest path to a sink is the longest path overall
    sinks = [s for s in dag.STAGE_ORDER if not any(s in spec["deps"] for spec in dag.STAGES.values())]
    end = max(sinks, key=lambda s: best[s])
    path = [end]
    while prev[path[-1]] is not None:
        path.append(prev[path[-1]])
    return dict(ms=round(best[end], 4), path=list(reversed(path)))


def run_metrics(events: List[dict]) -> dict:
    iv = intervals(events)
    executed = {s: x for s, x in iv.items() if x["status"] == "SUCCEEDED"}
    failed = [s for s, x in iv.items() if x["status"] == "FAILED"]
    kinds = {}
    for e in events:
        if e.get("stage"):
            kinds[e["stage"]] = e["type"]
    end = next((e for e in reversed(events) if e.get("type") == "run_finished"), None)
    ran = {s: x for s, x in iv.items()}                          # executed or failed: the function was called
    busy = union_length(ran)
    wall = float(end["wall_ms"]) if end and end.get("wall_ms") is not None else None
    cp = critical_path({s: x["duration"] for s, x in ran.items()})
    return dict(
        status=end["status"] if end else "UNFINISHED",
        outcome_status=end.get("outcome_status") if end else None,
        wall_ms=wall,
        end_to_end_ms=float(end["commit_ms"]) if end and end.get("commit_ms") is not None else None,
        stage_sum_ms=round(sum(x["duration"] for x in ran.values()), 4),
        busy_ms=round(busy, 4),
        overhead_ms=round(wall - busy, 4) if wall is not None else None,
        critical_path_ms=cp["ms"], critical_path=cp["path"],
        max_concurrency=max_concurrency(ran),
        overlapping_pairs=overlapping_pairs(ran),
        stages_executed=len(executed), stages_failed=len(failed),
        stages_reused=sum(1 for v in kinds.values() if v == "stage_reused"),
        stages_not_rerun=sum(1 for v in kinds.values() if v == "stage_not_rerun"),
        stages_skipped=sum(1 for v in kinds.values() if v == "stage_skipped"),
    )
