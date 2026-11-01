"""
Authorised data corrections. Each operation changes exactly one input domain,
records before/after values, and returns that domain so the orchestrator can
perform a targeted rerun. Bill lines are soft-deleted, never removed.

Bill header rule: if the header total equalled the sum of lines before a line
edit (i.e. the bill was internally consistent), it is re-issued as the new sum
after the edit. An inconsistent header is only changed by set_bill_header_total.
"""
import datetime as dt
import hashlib

from app.clearclaim.models import (
    BillLine, ChecklistItem, ClaimCase, Correction, EncounterDiagnosis,
)
from app.clearclaim.orchestrator import Orchestrator, audit
from app.datasets import catalog as C


class CorrectionError(ValueError):
    pass


class ConflictError(CorrectionError):
    """The case changed since the client read it (duplicate submission or concurrent edit)."""


OP_DOMAINS = {
    "update_bill_line": "bill", "delete_bill_line": "bill", "add_bill_line": "bill", "set_bill_header_total": "bill",
    "update_diagnosis": "diagnoses", "delete_diagnosis": "diagnoses", "add_diagnosis": "diagnoses",
    "update_procedure": "procedures", "set_discharge_time": "encounter", "add_document": "documents",
    "complete_checklist_item": "checklist", "amend_note_add_diagnosis": "discharge_note",
}
BILL_LINE_FIELDS = {"quantity", "unit_price", "amount", "unit_price_inr", "amount_inr", "service_date", "item_code"}


def _line(enc, line_no):
    l = next((x for x in enc.bill_lines if x.line_no == int(line_no) and not x.is_deleted), None)
    if l is None:
        raise CorrectionError(f"bill line {line_no} not found")
    return l


def _line_dict(l):
    return dict(line_no=l.line_no, item_code=l.item_code, quantity=l.quantity, unit_price=l.unit_price,
                amount=l.amount, service_date=l.service_date and l.service_date.isoformat(), is_deleted=l.is_deleted)


def _bill_sum(enc):
    return round(sum(l.amount for l in enc.bill_lines if not l.is_deleted), 2)


def _title_for(ctx, code):
    return (ctx.icd10 or {}).get(code) or C.ICD10CM_TITLES.get(code)


def apply_operation(db, case: ClaimCase, op: str, payload: dict, ctx):
    enc = case.encounter
    ca = enc.claim_admin
    if op not in OP_DOMAINS:
        raise CorrectionError(f"unknown correction op {op!r}")
    domain = OP_DOMAINS[op]
    header_consistent = ca is not None and ca.bill_header_total is not None and abs(ca.bill_header_total - _bill_sum(enc)) <= 1.0

    if op == "update_bill_line":
        l = _line(enc, payload["line_no"])
        before = _line_dict(l)
        fields = dict(payload.get("fields") or {})
        if not fields or set(fields) - BILL_LINE_FIELDS:
            raise CorrectionError(f"fields must be a non-empty subset of {sorted(BILL_LINE_FIELDS)}")
        for k, v in fields.items():
            k = {"unit_price_inr": "unit_price", "amount_inr": "amount"}.get(k, k)
            if k in ("quantity", "unit_price", "amount"):
                v = float(v)
                if v < 0:
                    raise CorrectionError(f"{k} cannot be negative")
            if k == "service_date":
                v = dt.date.fromisoformat(v)
            setattr(l, k, v)
        after = _line_dict(l)
    elif op == "delete_bill_line":
        l = _line(enc, payload["line_no"])
        before = _line_dict(l)
        l.is_deleted = True
        after = _line_dict(l)
    elif op == "add_bill_line":
        line = dict(payload["line"])
        nxt = max([x.line_no for x in enc.bill_lines] or [0]) + 1
        qty = float(line.get("quantity", 1))
        price = float(line.get("unit_price", line.get("unit_price_inr")))
        amount = float(line.get("amount", line.get("amount_inr", qty * price)))
        if qty <= 0 or price < 0:
            raise CorrectionError("quantity must be > 0 and unit_price >= 0")
        nl = BillLine(encounter_id=enc.id, line_no=nxt, item_code=str(line["item_code"]),
                      category=line.get("category"), description=line.get("description"), quantity=qty,
                      unit_price=price, amount=amount,
                      service_date=dt.date.fromisoformat(line["service_date"]) if line.get("service_date") else None,
                      linked_icd_code=line.get("linked_icd_code") or None, provenance="CORRECTION")
        db.add(nl)
        enc.bill_lines.append(nl)
        before, after = None, _line_dict(nl)
    elif op == "set_bill_header_total":
        before = dict(header_total=ca.bill_header_total)
        v = payload["value"]
        ca.bill_header_total = _bill_sum(enc) if v == "SUM_OF_LINES" else float(v)
        after = dict(header_total=ca.bill_header_total)
    elif op == "update_diagnosis":
        d = next((x for x in enc.diagnoses if x.seq_num == int(payload["seq_num"])), None)
        if d is None:
            raise CorrectionError("diagnosis not found")
        before = dict(seq_num=d.seq_num, icd_code=d.icd_code, long_title=d.long_title)
        code = str(payload["icd_code"]).strip().upper().replace(".", "")
        d.icd_code, d.long_title = code, _title_for(ctx, code)
        after = dict(seq_num=d.seq_num, icd_code=d.icd_code, long_title=d.long_title)
    elif op == "delete_diagnosis":
        d = next((x for x in enc.diagnoses if x.seq_num == int(payload["seq_num"])), None)
        if d is None:
            raise CorrectionError("diagnosis not found")
        if d.seq_num == 1:
            raise CorrectionError("the principal diagnosis cannot be deleted; update it instead")
        before = dict(seq_num=d.seq_num, icd_code=d.icd_code)
        enc.diagnoses.remove(d)
        db.delete(d)
        after = None
    elif op == "add_diagnosis":
        code = str(payload["icd_code"]).strip().upper().replace(".", "")
        seq = max([x.seq_num for x in enc.diagnoses] or [0]) + 1
        nd = EncounterDiagnosis(encounter_id=enc.id, seq_num=seq, icd_code=code, icd_version=10,
                                long_title=_title_for(ctx, code))
        db.add(nd)
        enc.diagnoses.append(nd)
        before, after = None, dict(seq_num=seq, icd_code=code, long_title=nd.long_title)
    elif op == "update_procedure":
        p = next((x for x in enc.procedures if x.seq_num == int(payload["seq_num"])), None)
        if p is None:
            raise CorrectionError("procedure not found")
        before = dict(seq_num=p.seq_num, chartdate=p.chartdate and p.chartdate.isoformat())
        p.chartdate = dt.date.fromisoformat(payload["chartdate"])
        after = dict(seq_num=p.seq_num, chartdate=p.chartdate.isoformat())
    elif op == "set_discharge_time":
        before = dict(dischtime=enc.dischtime and enc.dischtime.isoformat())
        v = dt.datetime.fromisoformat(str(payload["value"]))
        if enc.admittime and v < enc.admittime:
            raise CorrectionError("discharge time cannot be before admission time")
        enc.dischtime = v
        after = dict(dischtime=v.isoformat())
    elif op == "add_document":
        doc = str(payload["document"]).strip()
        if not doc or "," in doc:
            raise CorrectionError("invalid document name")
        docs = [x for x in (ca.documents or "").split(",") if x]
        before = dict(documents=list(docs))
        if doc not in docs:
            docs.append(doc)
        ca.documents = ",".join(docs)
        after = dict(documents=docs)
    elif op == "complete_checklist_item":
        item = next((c for c in enc.checklist if c.item == payload["item"]), None)
        if item is None:
            item = ChecklistItem(encounter_id=enc.id, item=payload["item"], status="PENDING")
            db.add(item)
            enc.checklist.append(item)
        before = dict(item=item.item, status=item.status)
        item.status, item.completed_at = "COMPLETE", dt.datetime.utcnow()
        after = dict(item=item.item, status=item.status)
    elif op == "amend_note_add_diagnosis":
        if enc.note is None:
            raise CorrectionError("no discharge note to amend")
        text = str(payload["text"]).strip()
        if not text or len(text) > 300:
            raise CorrectionError("text must be 1-300 characters")
        before = dict(text_sha256=hashlib.sha256(enc.note.text.encode()).hexdigest())
        lines = enc.note.text.splitlines()
        try:
            i = next(k for k, ln in enumerate(lines) if ln.strip().lower().startswith("discharge diagnosis"))
            j = next(k for k in range(i, len(lines)) if lines[k].strip().lower().rstrip(":") == "primary")
            if j + 1 < len(lines) and lines[j + 1].strip() == "___":
                lines[j + 1] = f"- {text}"
            else:
                lines.insert(j + 1, f"- {text}")
        except StopIteration:
            lines += ["", "Discharge Diagnosis:", "Primary:", f"- {text}"]
        enc.note.text = "\n".join(lines) + "\n(Addendum recorded via ClearClaim correction)"
        after = dict(added=text)
    if domain == "bill" and op != "set_bill_header_total" and header_consistent:
        ca.bill_header_total = _bill_sum(enc)
    return domain, before, after


def apply_correction(db, case: ClaimCase, op: str, payload: dict, actor: str, reason: str,
                     review_task_id=None, expected_version=None, rerun=True, orchestrator: Orchestrator = None):
    """Apply one correction and (by default) run the targeted rerun. Single transaction."""
    if expected_version is not None and int(expected_version) != case.version:
        raise ConflictError(f"case was modified by another action (now version {case.version}, you had version "
                            f"{expected_version}); reload the case and re-apply if still needed")
    orch = orchestrator or Orchestrator(db, actor=actor)
    domain, before, after = apply_operation(db, case, op, payload, orch.ctx)
    case.version += 1
    corr = Correction(case_id=case.id, op=op, payload=payload, domain=domain, before=before, after=after,
                      actor=actor, reason=reason, review_task_id=review_task_id)
    db.add(corr)
    db.flush()
    audit(db, "CORRECTION_APPLIED", f"{op} on {domain}: {reason}", actor, case.id, "correction", corr.id,
          before=before, after=after)
    run = None
    if rerun:
        db.flush()
        db.expire(case.encounter)
        run = orch.process(case, trigger="CORRECTION", changed_domains=[domain])
        corr.run_id = run.id
        db.commit()
    return corr, run
