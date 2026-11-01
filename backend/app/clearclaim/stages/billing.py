"""
Stage: billing verification (line-level reconciliation against the rate card).

Formulas (also shown in the UI):
  expected_unit_price = rate_card[item_code]
                        (PROC-* not on the card -> PROC-GENERIC; PHARM-* -> PHARM-GENERIC)
  expected_amount     = quantity x min(unit_price, expected_unit_price)
  arithmetic check    : |amount - quantity x unit_price| <= tolerance
  tariff check        : unit_price <= expected_unit_price x (1 + tolerance_pct)
  bed-days check      : sum(qty of ROOM-*/ICU-DAY lines) == length of stay (from data_validation)
  documentation check : every PROC-* line's linked ICD code is a coded procedure, and vice versa
  duplicate check     : identical (item, service date, qty, unit price, amount) lines
disputed_amount = sum of over-charges identified above (excluded from the payable base until resolved).
All money in INR, rounded to 2 decimals.
"""
from collections import defaultdict

from app.clearclaim.findings import finding

ROOM_ITEMS = ("ROOM-GW", "ROOM-SP", "ROOM-PVT", "ICU-DAY")


def _expected_price(item_code, rate_card):
    if item_code in rate_card:
        return rate_card[item_code]["unit_price"], item_code
    if item_code.startswith("PROC-") and "PROC-GENERIC" in rate_card:
        return rate_card["PROC-GENERIC"]["unit_price"], "PROC-GENERIC"
    if item_code.startswith("PHARM-") and "PHARM-GENERIC" in rate_card:
        return rate_card["PHARM-GENERIC"]["unit_price"], "PHARM-GENERIC"
    return None, None


def _los_max_component(findings):
    return sum((x.get("amount") or 0) for x in findings if x["code"] == "LOS_BILLING_MISMATCH")


def run_billing(inp, up, ctx):
    f = []
    lines = inp["bill"]["lines"]
    rc = ctx.rate_card
    tol_amt = float(ctx.settings["billing_amount_tolerance_inr"])
    tol_pct = float(ctx.settings["billing_price_tolerance_pct"])
    line_results = []
    disputed = 0.0
    disputed_min = 0.0   # lower bound: LOS excess valued at the CHEAPEST room rate (used by causal rules)

    for l in lines:
        exp_price, card_item = _expected_price(l["item_code"], rc)
        codes = []
        arith_diff = l["amount"] - l["quantity"] * l["unit_price"]
        if abs(arith_diff) > tol_amt:
            codes.append("BILL_ARITHMETIC_ERROR")
            over = max(0.0, arith_diff)
            disputed += over
            f.append(finding("BILL_ARITHMETIC_ERROR", "BLOCKING",
                             f"Line {l['line_no']} ({l['item_code']}): amount {l['amount']:.2f} does not equal "
                             f"{l['quantity']:g} x {l['unit_price']:.2f} = {l['quantity'] * l['unit_price']:.2f}.",
                             expected=round(l["quantity"] * l["unit_price"], 2), actual=l["amount"], amount=over,
                             line_no=l["line_no"], item_code=l["item_code"]))
        if exp_price is None:
            codes.append("UNKNOWN_BILL_ITEM")
            f.append(finding("UNKNOWN_BILL_ITEM", "REVIEW", f"Line {l['line_no']}: item {l['item_code']} is not on the rate card.",
                             line_no=l["line_no"], item_code=l["item_code"]))
        elif l["unit_price"] > exp_price * (1 + tol_pct) + 1e-9:
            codes.append("UNIT_PRICE_ABOVE_TARIFF")
            over = (l["unit_price"] - exp_price) * l["quantity"]
            disputed += over
            f.append(finding("UNIT_PRICE_ABOVE_TARIFF", "BLOCKING",
                             f"Line {l['line_no']} ({l['item_code']}): unit price {l['unit_price']:.2f} exceeds the "
                             f"rate card {exp_price:.2f} ({card_item}).",
                             expected=exp_price, actual=l["unit_price"], amount=over, line_no=l["line_no"],
                             item_code=l["item_code"], rate_card_item=card_item))
        line_results.append(dict(line_no=l["line_no"], item_code=l["item_code"], description=l["description"],
                                 category=l["category"], quantity=l["quantity"], unit_price=l["unit_price"],
                                 expected_unit_price=exp_price, amount=l["amount"],
                                 expected_amount=(round(l["quantity"] * min(l["unit_price"], exp_price), 2)
                                                  if exp_price is not None else None),
                                 service_date=l["service_date"], linked_icd_code=l["linked_icd_code"], codes=codes))

    # duplicates
    groups = defaultdict(list)
    for l in lines:
        groups[(l["item_code"], l["service_date"], l["quantity"], l["unit_price"], l["amount"])].append(l)
    for key, ls in groups.items():
        if len(ls) > 1 and key[0] not in ROOM_ITEMS:
            for extra in sorted(ls, key=lambda x: x["line_no"])[1:]:
                disputed += extra["amount"]
                f.append(finding("DUPLICATE_BILL_ITEM", "BLOCKING",
                                 f"Line {extra['line_no']} duplicates line {ls[0]['line_no']} "
                                 f"({extra['item_code']} on {extra['service_date']}).",
                                 expected="1 line", actual=f"{len(ls)} identical lines", amount=extra["amount"],
                                 line_no=extra["line_no"], duplicate_of=ls[0]["line_no"], item_code=extra["item_code"]))
                for lr in line_results:
                    if lr["line_no"] == extra["line_no"]:
                        lr["codes"].append("DUPLICATE_BILL_ITEM")

    # bed-days versus length of stay
    los = up["data_validation"]["los_days"]
    room_lines = [l for l in lines if l["item_code"] in ROOM_ITEMS]
    bed_days = sum(l["quantity"] for l in room_lines)
    ward = [l for l in room_lines if l["item_code"] != "ICU-DAY"]
    room_rate = ward[0]["unit_price"] if ward else None
    ward_days = sum(l["quantity"] for l in ward)
    if los is None:
        f.append(finding("LOS_NOT_VERIFIABLE", "REVIEW",
                         "Bed-days on the bill cannot be verified because the length of stay is unknown.",
                         expected="known length of stay", actual=f"{bed_days:g} bed-days billed"))
    elif room_lines and bed_days != los:
        if bed_days > los:
            # charge the excess at the most expensive room line's rate (conservative for the payer)
            rate = max(l["unit_price"] for l in room_lines)
            over = (bed_days - los) * rate
            disputed += over
            disputed_min += (bed_days - los) * min(l["unit_price"] for l in room_lines)
            sev, msg_amt = "BLOCKING", over
        else:
            sev, msg_amt = "REVIEW", 0.0
        target = max(room_lines, key=lambda l: l["quantity"])
        f.append(finding("LOS_BILLING_MISMATCH", sev,
                         f"{bed_days:g} room/ICU day(s) billed but the length of stay is {los} day(s).",
                         expected=f"{los} day(s)", actual=f"{bed_days:g} day(s)", amount=msg_amt,
                         line_no=target["line_no"], item_code=target["item_code"]))
        for lr in line_results:
            if lr["line_no"] == target["line_no"]:
                lr["codes"].append("LOS_BILLING_MISMATCH")

    # procedures billed versus documented
    coded = {p["icd_code"] for p in inp["procedures"]}
    billed_proc = {}
    for l in lines:
        if l["item_code"].startswith("PROC-") and l["linked_icd_code"]:
            billed_proc.setdefault(l["linked_icd_code"], l)
            if l["linked_icd_code"] not in coded:
                disputed += l["amount"]
                f.append(finding("BILLED_PROCEDURE_NOT_DOCUMENTED", "BLOCKING",
                                 f"Line {l['line_no']} charges procedure {l['linked_icd_code']} which is not in the "
                                 f"coded procedure record.", expected="documented procedure",
                                 actual=l["linked_icd_code"], amount=l["amount"], line_no=l["line_no"],
                                 item_code=l["item_code"], icd_code=l["linked_icd_code"]))
                for lr in line_results:
                    if lr["line_no"] == l["line_no"]:
                        lr["codes"].append("BILLED_PROCEDURE_NOT_DOCUMENTED")
    # Each coded procedure OCCURRENCE needs its own bill line: real coding repeats codes (e.g. three
    # 88.41 angiographies), so comparing only "is the code billed at all" hides a missing repeat.
    billed_count = defaultdict(int)
    for l in lines:
        if l["item_code"].startswith("PROC-") and l["linked_icd_code"]:
            billed_count[l["linked_icd_code"]] += 1
    missing_amt = 0.0
    for p in inp["procedures"]:
        if billed_count[p["icd_code"]] > 0:
            billed_count[p["icd_code"]] -= 1
            continue
        price, _ = _expected_price(f"PROC-{p['icd_code']}", rc)
        missing_amt += price or 0.0
        f.append(finding("MISSING_CHARGE", "REVIEW",
                         f"Coded procedure {p['icd_code']} ({p.get('long_title') or 'no title'}, sequence "
                         f"{p['seq_num']}) has no bill line of its own.",
                         expected=f"PROC-{p['icd_code']} line", actual="not billed", amount=price,
                         seq_num=p["seq_num"], icd_code=p["icd_code"]))

    lines_total = round(sum(l["amount"] for l in lines), 2)
    # every other over-charge is exact, so it counts fully in both bounds
    disputed_min = round(disputed_min + (disputed - _los_max_component(f)), 2)
    disputed = round(disputed, 2)
    return dict(data=dict(lines_total=lines_total, disputed_amount=disputed, disputed_amount_min=disputed_min,
                          verified_total=round(lines_total - disputed, 2), missing_charge_amount=round(missing_amt, 2),
                          room_rate=room_rate, ward_days=ward_days, bed_days=bed_days, n_lines=len(lines),
                          line_results=line_results),
                findings=f)
