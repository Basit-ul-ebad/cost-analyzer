"""Cost calculations: variance, % variance, status, totals and category breakdown.

Definitions (used everywhere in the app and the report):
    Variance        = Actual - Estimated        (positive = overspent)
    % Variance      = Variance / Estimated * 100
    Status          = "Over budget"  if % variance >  TOLERANCE_PCT
                      "Under budget" if % variance < -TOLERANCE_PCT
                      "On budget"    otherwise
"""
from collections import OrderedDict

TOLERANCE_PCT = 2.0  # within +/-2% of the estimate counts as "on budget"


def pct_variance(estimated, variance):
    """Return % variance, or None when the estimate is 0 (division undefined)."""
    if estimated == 0:
        return None
    return round(variance / estimated * 100, 2)


def status_for(estimated, actual, pct):
    if pct is None:  # no estimate: any spend is unplanned
        return "Over budget" if actual > 0 else "On budget"
    if pct > TOLERANCE_PCT:
        return "Over budget"
    if pct < -TOLERANCE_PCT:
        return "Under budget"
    return "On budget"


def analyse_item(estimated, actual):
    variance = round(actual - estimated, 2)
    pct = pct_variance(estimated, variance)
    return {
        "variance": variance,
        "pct_variance": pct,
        "status": status_for(estimated, actual, pct),
    }


def analyse_project(items):
    """items: iterable of rows/dicts with item, category, estimated, actual."""
    rows = []
    for it in items:
        keys = it.keys()
        row = {k: it[k] for k in ("id", "item", "category", "estimated", "actual") if k in keys}
        for k in ("code", "cost_o", "cost_m", "cost_p", "dur_o", "dur_m", "dur_p", "predecessors"):
            row[k] = it[k] if k in keys else None
        row.update(analyse_item(row["estimated"], row["actual"]))
        rows.append(row)

    total_est = round(sum(r["estimated"] for r in rows), 2)
    total_act = round(sum(r["actual"] for r in rows), 2)
    total_var = round(total_act - total_est, 2)
    total_pct = pct_variance(total_est, total_var)

    cats = OrderedDict()
    for r in rows:
        c = cats.setdefault(r["category"], {"category": r["category"], "estimated": 0.0, "actual": 0.0, "count": 0})
        c["estimated"] += r["estimated"]
        c["actual"] += r["actual"]
        c["count"] += 1
    categories = []
    for c in cats.values():
        c["estimated"] = round(c["estimated"], 2)
        c["actual"] = round(c["actual"], 2)
        c.update(analyse_item(c["estimated"], c["actual"]))
        categories.append(c)
    categories.sort(key=lambda c: c["variance"], reverse=True)

    counts = {"Over budget": 0, "Under budget": 0, "On budget": 0}
    for r in rows:
        counts[r["status"]] += 1

    return {
        "lines": rows,
        "categories": categories,
        "totals": {
            "estimated": total_est,
            "actual": total_act,
            "variance": total_var,
            "pct_variance": total_pct,
            "status": status_for(total_est, total_act, total_pct),
            "item_count": len(rows),
            "status_counts": counts,
        },
        "tolerance_pct": TOLERANCE_PCT,
    }
