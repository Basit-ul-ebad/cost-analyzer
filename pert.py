"""PERT (Program Evaluation and Review Technique) analysis for cost and schedule.

Three-point estimates: O = optimistic, M = most likely, P = pessimistic.
    Expected value  E  = (O + 4M + P) / 6
    Std deviation   SD = (P - O) / 6          variance = SD^2

Cost PERT:   project expected cost = sum of item E (items without a three-point estimate
             count at their single estimate, with no uncertainty); project SD = sqrt(sum of variances).
Schedule PERT: activities with three-point durations and predecessors form a network.
             A forward pass gives earliest start/finish (ES/EF), a backward pass latest start/finish
             (LS/LF); slack = LS - ES; activities with zero slack form the critical path.
             Project duration T = largest EF; its SD = sqrt(sum of variances on the critical path).
Probabilities use the normal approximation: P(X <= x) = Phi((x - E) / SD).
"""
import math
from collections import deque

CONFIDENCE_LEVELS = [(50, 0.0), (80, 0.8416), (90, 1.2816), (95, 1.6449)]
EPS = 1e-9


def three_point(o, m, p):
    expected = (o + 4 * m + p) / 6
    sd = (p - o) / 6
    return round(expected, 2), round(sd, 2), sd * sd


def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def probability_within(limit, expected, sd):
    """Chance (0-100%) that the outcome is at most `limit`."""
    if sd <= EPS:
        return 100.0 if limit >= expected - EPS else 0.0
    return round(normal_cdf((limit - expected) / sd) * 100, 1)


def split_predecessors(text):
    import re
    return [p for p in re.split(r"[,;\s]+", (text or "").strip()) if p]


# --------------------------------------------------------------------- cost
def cost_pert(lines):
    items, exp_total, var_total, covered = [], 0.0, 0.0, 0
    for l in lines:
        if l.get("cost_o") is None:
            exp_total += l["estimated"]
            continue
        e, sd, var = three_point(l["cost_o"], l["cost_m"], l["cost_p"])
        covered += 1
        exp_total += e
        var_total += var
        items.append({"code": l.get("code") or "", "item": l["item"], "o": l["cost_o"], "m": l["cost_m"],
                      "p": l["cost_p"], "expected": e, "sd": sd, "actual": l["actual"],
                      "actual_vs_expected": round(l["actual"] - e, 2)})
    if not covered:
        return None
    sd_total = math.sqrt(var_total)
    budget = sum(l["estimated"] for l in lines)
    actual = sum(l["actual"] for l in lines)
    items.sort(key=lambda i: i["sd"], reverse=True)
    return {
        "covered": covered,
        "total_items": len(lines),
        "expected": round(exp_total, 2),
        "sd": round(sd_total, 2),
        "budget": round(budget, 2),
        "actual": round(actual, 2),
        "p_within_budget": probability_within(budget, exp_total, sd_total),
        "p_within_actual": probability_within(actual, exp_total, sd_total),
        "confidence": [{"level": lvl, "value": round(exp_total + z * sd_total, 2)} for lvl, z in CONFIDENCE_LEVELS],
        "items": items,
    }


# --------------------------------------------------------------------- schedule
def schedule_pert(lines, target_days=None):
    acts = {}
    warnings = []
    for idx, l in enumerate(lines):
        if l.get("dur_o") is None:
            continue
        code = l.get("code") or f"#{idx + 1}"
        if code in acts:
            warnings.append(f"Duplicate activity ID {code}: only the first one is used")
            continue
        e, sd, var = three_point(l["dur_o"], l["dur_m"], l["dur_p"])
        acts[code] = {"code": code, "item": l["item"], "o": l["dur_o"], "m": l["dur_m"], "p": l["dur_p"],
                      "te": e, "sd": sd, "var": var, "preds": [], "succs": []}
    if not acts:
        return None

    for idx, l in enumerate(lines):
        code = l.get("code") or f"#{idx + 1}"
        if code not in acts or acts[code]["item"] != l["item"]:
            continue
        for p in split_predecessors(l.get("predecessors")):
            if p not in acts:
                warnings.append(f"{code}: predecessor {p} has no PERT durations or does not exist, so it is ignored")
            elif p == code:
                warnings.append(f"{code}: an activity cannot depend on itself, ignored")
            elif p not in acts[code]["preds"]:
                acts[code]["preds"].append(p)
                acts[p]["succs"].append(code)

    # Topological order (Kahn's algorithm, iterative so large inputs cannot exhaust the stack)
    indeg = {c: len(a["preds"]) for c, a in acts.items()}
    queue = deque(c for c, d in indeg.items() if d == 0)
    order = []
    while queue:
        c = queue.popleft()
        order.append(c)
        for s in acts[c]["succs"]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(order) != len(acts):
        stuck = sorted(c for c in acts if c not in order)
        return {"error": "The dependencies form a loop (" + ", ".join(stuck[:8]) + "), so no schedule can be calculated.",
                "warnings": warnings}

    for c in order:  # forward pass
        a = acts[c]
        a["es"] = max((acts[p]["ef"] for p in a["preds"]), default=0.0)
        a["ef"] = a["es"] + a["te"]
    duration = max(a["ef"] for a in acts.values())
    for c in reversed(order):  # backward pass
        a = acts[c]
        a["lf"] = min((acts[s]["ls"] for s in a["succs"]), default=duration)
        a["ls"] = a["lf"] - a["te"]
        a["slack"] = a["ls"] - a["es"]
        a["critical"] = abs(a["slack"]) < 1e-6

    # Critical path: walk back from the activity that ends last, choosing critical predecessors
    # (on ties, the branch with the larger variance, as is conventional in PERT).
    best = {}
    for c in order:
        a = acts[c]
        if not a["critical"]:
            continue
        cands = [p for p in a["preds"] if acts[p]["critical"] and abs(acts[p]["ef"] - a["es"]) < 1e-6]
        prev = max(cands, key=lambda p: best[p][0], default=None)
        best[c] = ((best[prev][0] if prev else 0.0) + a["var"], prev)
    end = max((c for c in acts if acts[c]["critical"] and abs(acts[c]["ef"] - duration) < 1e-6),
              key=lambda c: best[c][0])
    path, node = [], end
    while node:
        path.append(node)
        node = best[node][1]
    path.reverse()
    sd = math.sqrt(sum(acts[c]["var"] for c in path))

    rows = []
    for c in order:
        a = acts[c]
        rows.append({k: (round(v, 2) + 0.0 if isinstance(v, float) else v) for k, v in a.items()
                     if k not in ("succs", "var")})
    rows.sort(key=lambda r: (r["es"], r["code"]))
    result = {
        "activities": rows,
        "duration": round(duration, 2),
        "sd": round(sd, 2),
        "critical_path": path,
        "critical_names": [acts[c]["item"] for c in path],
        "confidence": [{"level": lvl, "value": round(duration + z * sd, 1)} for lvl, z in CONFIDENCE_LEVELS],
        "warnings": warnings,
        "target_days": target_days,
        "p_target": probability_within(target_days, duration, sd) if target_days else None,
        "z_target": round((target_days - duration) / sd, 2) if target_days and sd > EPS else None,
    }
    return result


def analyse(lines, target_days=None):
    return {"cost": cost_pert(lines), "schedule": schedule_pert(lines, target_days)}
