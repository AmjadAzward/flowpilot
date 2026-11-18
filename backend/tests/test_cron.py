import json
from datetime import datetime, timedelta, timezone

import pytest

from app import main
from conftest import edge, make_flow, node


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


def nxt(expr, after):
    return main.cron_next(main.parse_cron(expr), after).astimezone()


def fields(dt):
    return (dt.year, dt.month, dt.day, dt.hour, dt.minute)


def test_every_fifteen_minutes_is_strictly_after():
    assert fields(nxt("*/15 * * * *", local(2026, 3, 2, 10, 30))) == (2026, 3, 2, 10, 45)
    assert fields(nxt("*/15 * * * *", local(2026, 3, 2, 10, 59))) == (2026, 3, 2, 11, 0)


def test_weekday_mornings_skip_the_weekend():
    friday = local(2026, 3, 6, 10, 0)  # 2026-03-06 is a Friday
    assert fields(nxt("0 9 * * 1-5", friday)) == (2026, 3, 9, 9, 0)  # Monday


def test_lists_ranges_and_steps():
    assert fields(nxt("0,30 8-10/2 * * *", local(2026, 3, 2, 8, 30))) == (2026, 3, 2, 10, 0)


def test_sunday_can_be_written_as_0_or_7():
    monday = local(2026, 3, 2, 9, 0)
    assert fields(nxt("0 12 * * 7", monday)) == (2026, 3, 8, 12, 0)
    assert fields(nxt("0 12 * * 0", monday)) == (2026, 3, 8, 12, 0)


def test_day_of_month_and_weekday_combine_with_or():
    # "the 13th, or any Friday": the first Friday after Mon 2 March is the 6th
    assert fields(nxt("0 0 13 * 5", local(2026, 3, 2, 12, 0))) == (2026, 3, 6, 0, 0)


def test_leap_day_is_found():
    assert fields(nxt("30 2 29 2 *", local(2026, 3, 1))) == (2028, 2, 29, 2, 30)


def test_month_restriction():
    assert fields(nxt("0 0 1 6 *", local(2026, 3, 2))) == (2026, 6, 1, 0, 0)


@pytest.mark.parametrize("expr", [
    "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *",
    "a * * * *", "5-1 * * * *", "1-70 * * * *", "*/x * * * *", "",
])
def test_invalid_cron_expressions(expr):
    with pytest.raises(ValueError):
        main.parse_cron(expr)


def test_impossible_date_is_rejected_when_validating():
    with pytest.raises(ValueError, match="never matches"):
        main.validate_schedule({"mode": "cron", "cron": "0 0 31 2 *"})


def test_validate_schedule_cron_mode():
    cfg = main.validate_schedule({"mode": "cron", "cron": " 0 9 * * 1-5 "})
    assert cfg["mode"] == "cron"
    now = datetime.now(timezone.utc)
    assert main.next_run_after(cfg, now) > now


# ---------------------------------------------------------------- activation and the scheduler
@pytest.fixture(autouse=True)
def _clean(clean_state):
    yield


def cron_flow(client, expr="*/5 * * * *"):
    return make_flow(client, [node("s", "schedule", mode="cron", cron=expr), node("m", "template", template="ran {{scheduled_at}}")], [edge("s", "m")])


def test_cron_workflow_activates_and_reports_next_run(client):
    wid = cron_flow(client, "*/5 * * * *")
    res = client.post(f"/api/workflows/{wid}/active", json={"active": True}).json()
    due = datetime.fromisoformat(res["next_run_at"])
    assert timedelta(0) < due - datetime.now(timezone.utc) <= timedelta(minutes=5, seconds=1)


def test_invalid_cron_cannot_be_activated(client):
    wid = cron_flow(client, "every day")
    res = client.post(f"/api/workflows/{wid}/active", json={"active": True})
    assert res.status_code == 400 and "cron" in res.json()["detail"].lower()


def test_scheduler_runs_cron_workflows_and_advances(client):
    wid = cron_flow(client, "*/10 * * * *")
    client.post(f"/api/workflows/{wid}/active", json={"active": True})
    later = datetime.now(timezone.utc) + timedelta(minutes=11)
    assert main.scheduler_tick(later) == 1
    runs = client.get("/api/executions", params={"workflow_id": wid}).json()
    assert len(runs) == 1 and runs[0]["trigger"] == "schedule" and runs[0]["status"] == "success"
    assert datetime.fromisoformat(client.get(f"/api/workflows/{wid}").json()["next_run_at"]) > later
