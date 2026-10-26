"""PERT cost and schedule analysis tests."""
import io

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

import pert
from conftest import ROOT, create_project, csrf_token, post_form, register
from test_security import upload

PERT_CSV = (ROOT / "static" / "sample_pert.csv").read_bytes()


def act(code, o, m, p, preds="", item=None):
    return {"code": code, "item": item or f"Task {code}", "estimated": 0, "actual": 0,
            "dur_o": o, "dur_m": m, "dur_p": p, "predecessors": preds, "cost_o": None}


def test_three_point_formula():
    e, sd, var = pert.three_point(2, 4, 12)
    assert e == 5.0 and sd == round(10 / 6, 2) and var == pytest.approx((10 / 6) ** 2)


def test_schedule_network_forward_backward_pass():
    # A(4) -> B(5) -> D(3); A -> C(2) -> D.  Critical path A-B-D = 12, C has 3 days slack.
    r = pert.schedule_pert([act("A", 2, 4, 6), act("B", 3, 5, 7, "A"), act("C", 1, 2, 3, "A"), act("D", 2, 3, 4, "B, C")])
    by = {a["code"]: a for a in r["activities"]}
    assert r["duration"] == 12.0 and r["critical_path"] == ["A", "B", "D"]
    assert by["C"]["slack"] == 3.0 and not by["C"]["critical"]
    assert (by["B"]["es"], by["B"]["ef"], by["D"]["ls"]) == (4.0, 9.0, 9.0)
    # SD of the critical path = sqrt(4/9 + 4/9 + 1/9) = 1.0
    assert r["sd"] == 1.0


def test_probability_of_meeting_target():
    lines = [act("A", 2, 4, 6), act("B", 3, 5, 7, "A"), act("D", 2, 3, 4, "B")]
    assert pert.schedule_pert(lines, 12)["p_target"] == 50.0       # target = expected time
    assert pert.schedule_pert(lines, 13)["p_target"] == 84.1       # one SD later
    assert pert.schedule_pert(lines, 11)["p_target"] == 15.9


def test_cycle_is_reported_not_crashed():
    r = pert.schedule_pert([act("A", 1, 2, 3, "B"), act("B", 1, 2, 3, "A")])
    assert "loop" in r["error"]


def test_unknown_predecessor_is_ignored_with_warning():
    r = pert.schedule_pert([act("A", 1, 2, 3, "Z9")])
    assert r["duration"] == 2.0 and "Z9" in r["warnings"][0]


def test_cost_pert_entered_values_take_priority():
    lines = [{"item": "X", "estimated": 100, "actual": 120, "cost_o": 80, "cost_m": 100, "cost_p": 180}]
    c = pert.cost_pert(lines)
    assert c["expected"] == 110.0 and c["sd"] == round(100 / 6, 2) and c["given"] == 1 and c["auto"] == 0
    assert c["p_within_budget"] == pytest.approx(27.4, abs=0.1)


def test_auto_cost_pert_uses_default_range_with_little_history():
    # Only 2 items have actuals, so the standard -10% / +30% range is used.
    lines = [{"item": "X", "estimated": 100, "actual": 120, "cost_o": None},
             {"item": "Y", "estimated": 50, "actual": 0, "cost_o": None}]
    c = pert.cost_pert(lines)
    assert c["auto"] == 2 and c["auto_factors"] == {"o": 0.9, "p": 1.3}
    assert "standard range" in c["auto_basis"]
    y = next(i for i in c["items"] if i["item"] == "Y")
    assert (y["o"], y["m"], y["p"], y["expected"]) == (45.0, 50, 65.0, 51.67)


def test_auto_cost_pert_calibrates_from_project_history():
    ratios = [0.8, 0.9, 1.0, 1.0, 1.1, 1.2, 1.5, 2.0]
    lines = [{"item": f"I{i}", "estimated": 100, "actual": 100 * r, "cost_o": None} for i, r in enumerate(ratios)]
    lines.append({"item": "Unplanned", "estimated": 0, "actual": 500, "cost_o": None})
    c = pert.cost_pert(lines)
    assert "8 items in this project" in c["auto_basis"]
    assert 0.5 <= c["auto_factors"]["o"] <= 0.95 and c["auto_factors"]["p"] >= 1.5
    assert c["auto"] == 8 and all(i["item"] != "Unplanned" for i in c["items"])
    assert c["actual"] == sum(100 * r for r in ratios) + 500   # unplanned spend still counts as actual


def test_plain_cost_sheet_gets_cost_pert_but_no_schedule(client):
    register(client, "plain")
    pid = create_project(client)
    upload(client, pid, "costs.csv", (ROOT / "static" / "sample_costs.csv").read_bytes())
    html = client.get(f"/projects/{pid}").get_data(as_text=True)
    assert "Auto-estimated ranges" in html and "10 auto-estimated" in html
    assert "The schedule needs information a cost sheet" in html


def test_sample_pert_file_end_to_end(client):
    register(client, "planner")
    pid = create_project(client, "PERT demo")
    assert b"Imported 14 item(s)" in upload(client, pid, "pert.csv", PERT_CSV).data
    html = client.get(f"/projects/{pid}").get_data(as_text=True)
    assert "Schedule PERT" in html and "A → B → D → E → G → J → K → M" in html
    assert "57.99" in html or "58.0 days" in html
    # set a deadline and read back the probability
    post_form(client, f"/projects/{pid}/target", {"target_days": "62"})
    html = client.get(f"/projects/{pid}").get_data(as_text=True)
    assert "Chance by day 62" in html and "82.8%" in html
    pdf = client.get(f"/projects/{pid}/report.pdf").data
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "PERT analysis" in text and "Critical path" in text
    wb = load_workbook(io.BytesIO(client.get(f"/projects/{pid}/report.xlsx").data))
    assert {"PERT cost", "PERT schedule"} <= set(wb.sheetnames)


@pytest.mark.parametrize("csv_text,message", [
    ("Item,Estimated,Actual,Optimistic Cost,Most Likely Cost,Pessimistic Cost\nX,1,1,50,40,60\n", "optimistic &lt;= most likely"),
    ("Item,Estimated,Actual,Optimistic Days,Most Likely Days\nX,1,1,2,3\n", "needs all three values"),
    ("Item,Estimated,Actual,ID,Optimistic Days,Most Likely Days,Pessimistic Days,Predecessors\nX,1,1,A,1,2,3,<script>\n", "Predecessors must be a list"),
    ("Item,Estimated,Actual,ID\nX,1,1,bad id!\n", "ID may only use"),
])
def test_invalid_pert_values_are_rejected(client, csv_text, message):
    register(client, "pertval")
    pid = create_project(client)
    body = upload(client, pid, "bad.csv", csv_text.encode()).get_data(as_text=True)
    assert "Upload rejected" in body and message in body


def test_target_route_is_protected(make_client):
    owner, other = make_client(), make_client()
    register(owner, "owner1")
    pid = create_project(owner)
    register(other, "other1")
    r = other.post(f"/projects/{pid}/target", data={"target_days": "10", "csrf_token": csrf_token(other, "/")})
    assert r.status_code == 404
    assert owner.post(f"/projects/{pid}/target", data={"target_days": "10"}).status_code == 400  # no CSRF token
