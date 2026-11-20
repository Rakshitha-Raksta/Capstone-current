"""
Issue identity and material-evidence fingerprint. Pure functions, shared by the review-routing stage
(to decide whether a recorded decision still applies) and by issue memory (to link repeated detections).

issue_key
    finding code + the evidence that names WHICH thing is wrong. It is the same grouping key the
    routing stage already uses (analysis._identity), so one review task <-> one issue. Two bill lines
    with the same code have different line numbers and therefore different issues; the cited rules
    are not part of it, so a change in which rules fire does not split one issue into two.

evidence_fingerprint
    hash of what a reviewer would base a decision on: severity, expected / recorded values, amount,
    cited rules and the item / code / document the issue is about. Free-text messages are excluded
    (they restate these values). A different fingerprint = materially new evidence.
"""
import hashlib
import json

# evidence keys that identify the object an issue is about (same tuple as stages/analysis.IDENTITY_KEYS)
IDENTITY_KEYS = ("line_no", "seq_num", "item", "document", "phrase", "field")
# evidence keys that are material to a decision but do not change which object the issue is about
MATERIAL_KEYS = ("item_code", "icd_code", "duplicate_of", "rule_category", "rate_card_item", "record")


def identity_of(code: str, evidence: dict) -> dict:
    ev = evidence or {}
    return {k: str(ev[k]) for k in IDENTITY_KEYS if ev.get(k) not in (None, "")}


def issue_key(code: str, evidence: dict) -> str:
    ev = evidence or {}
    ident = [code] + [str(ev.get(k, "")) for k in IDENTITY_KEYS]
    return hashlib.sha1("|".join(ident).encode()).hexdigest()[:16] + ":" + code


def _amount(v):
    try:
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return str(v)


def _rules(ev: dict):
    rules = {ev.get("rule_id") or ""}
    for r in ev.get("rules") or []:
        rules.add((r or {}).get("rule_id") or "")
    return sorted(x for x in rules if x)


def evidence_fingerprint(task: dict) -> str:
    """Fingerprint of a routed task dict (review_routing output) or an equivalent dict with
    code / severity / evidence (where evidence carries expected, actual, amount, rule_id, rules, ...)."""
    ev = task.get("evidence") or {}
    body = dict(
        code=task["code"], severity=task.get("severity"),
        identity=identity_of(task["code"], ev),
        expected=None if ev.get("expected") is None else str(ev.get("expected")),
        actual=None if ev.get("actual") is None else str(ev.get("actual")),
        amount=_amount(ev.get("amount")), rules=_rules(ev),
        material={k: str(ev[k]) for k in MATERIAL_KEYS if ev.get(k) not in (None, "")},
    )
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint_diff(old: dict, new: dict) -> dict:
    """Which material fields differ between two evidence dicts (for the reopen history)."""
    out = {}
    for k in ("expected", "actual", "amount") + MATERIAL_KEYS:
        a, b = (old or {}).get(k), (new or {}).get(k)
        if (k == "amount" and _amount(a) != _amount(b)) or (k != "amount" and str(a) != str(b)):
            out[k] = dict(before=a, after=b)
    ra, rb = _rules(old or {}), _rules(new or {})
    if ra != rb:
        out["rules"] = dict(before=ra, after=rb)
    return out
