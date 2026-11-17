# -*- coding: utf-8 -*-
"""云端适配：调度开关、单次拒绝、报价不得换算。使用虚构航线。"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.cloud_adapt import (  # noqa: E402
    assess,
    collection_enabled,
    health_cli,
    idle_cli,
    once_cli,
    once_preflight,
    outbound_problems,
    rows_since,
    schedule_enabled,
    web_enabled,
)
from core.cloud_adapt import finish_once_crawl  # noqa: E402
from core.quote_basis import crawl_contract, qualify_offer  # noqa: E402

SAFE = os.path.join(ROOT, "deploy", "cloud", "config.safe.example.yaml")
BANNED = ("LJG", "TSN", "PEK", "PKX", "2027-02-01")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TM_CLOUD_STATE_DIR", str(tmp_path))
    return tmp_path


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_interval_is_not_a_disable_switch():
    delayed = {"schedule": {"interval_minutes": 10080, "run_on_start": False}}
    assert schedule_enabled(delayed) is True
    assert schedule_enabled({"schedule": {"enabled": False,
                                          "interval_minutes": 10080}}) is False
    assert schedule_enabled({"schedule": {"enabled": "false"}}) is False
    assert schedule_enabled({"schedule": {"enabled": "off"}}) is False
    assert schedule_enabled({}) is True
    assert collection_enabled({}) is True
    assert collection_enabled({"crawler": {"collect": False}}) is False
    assert web_enabled({}) is True
    assert web_enabled({"web": {"enabled": False}}) is False


def test_safe_template_has_no_real_itinerary_and_is_closed():
    text = open(SAFE, encoding="utf-8").read()
    for token in BANNED:
        assert token not in text
    cfg = _load(SAFE)
    doc = assess(cfg, env={})
    assert doc["exit_code"] == 0
    assert doc["schedule_enabled"] is False
    assert doc["collection_enabled"] is False
    assert doc["web_enabled"] is False
    assert doc["family_quote_ok"] is False
    assert doc["family_total_cny"] is None
    assert doc["real_quote_completed"] is False
    assert doc["problems"] == []
    assert doc["passengers"]["adults"] == 2
    assert doc["passengers"]["children"][0]["age"] == 10
    assert doc["passengers"]["children"][0]["band"] == "child"
    assert doc["passengers_applied_to_fetch"] is False
    assert any("1 成人" in line for line in doc["degradation"])
    route = doc["routes"][0]
    assert route["fetch_filtered_to_direct"] is False
    assert route["direct_only_effect"] == "only_zeroes_alert_transfer"


def test_public_bind_and_notifier_secret_are_not_echoed():
    secret = "CANARY_SECRET_DO_NOT_LEAK"
    cfg = {
        "web": {"enabled": True, "host": "0.0.0.0"},
        "schedule": {"enabled": False},
        "crawler": {"collect": False},
        "users": [{
            "platforms": [],
            "notifier": {
                "digest": False,
                "win_toast": False,
                "dingtalk": {"enabled": True, "webhook": "https://example.invalid/" + secret},
            },
        }],
    }
    doc = assess(cfg, env={})
    blob = json.dumps(doc, ensure_ascii=False)
    assert secret not in blob
    assert doc["exit_code"] == 1
    codes = {p["code"] for p in doc["problems"]}
    assert "public_bind" in codes
    assert "notifier_enabled" in codes
    assert "secret_material_present" in codes


def test_loopback_web_disabled_with_public_host_is_only_a_warning():
    cfg = _load(SAFE)
    cfg["web"]["host"] = "0.0.0.0"
    doc = assess(cfg, env={})
    assert doc["exit_code"] == 0
    assert [p["code"] for p in doc["problems"]] == ["latent_public_host"]


def test_once_disabled_does_not_crawl_or_invent_family_price(state):
    cfg = _load(SAFE)
    called = []
    code = once_cli(SAFE, executor=lambda path: called.append(path) or 99)
    assert code == 0
    assert called == []
    doc = json.loads((state / "data" / "cloud-once.json").read_text(encoding="utf-8"))
    assert doc["status"] == "collection_disabled"
    assert doc["browser_launched"] is False
    assert doc["network_fetch"] is False
    assert doc["quotes"] == []
    assert doc["family_total_cny"] is None
    assert doc["family_quote_ok"] is False
    ops = (state / "logs" / "cloud-ops.jsonl").read_text(encoding="utf-8")
    assert "collection_disabled" in ops


def test_login_required_does_not_read_session_file(state, tmp_path):
    cfg = _load(SAFE)
    cfg["crawler"]["collect"] = True
    cfg["users"][0]["platforms"] = ["ctrip"]
    cfg["users"][0]["routes"][0]["enabled"] = True
    cfg["crawler"]["user_data_dir"] = str(tmp_path / "user_data")
    secret = "CANARY_SECRET_DO_NOT_LEAK"
    marker = tmp_path / "user_data" / "ctrip" / "session-state.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"cookie":"%s","saved_at":1}' % secret, encoding="utf-8")
    # 缺标记时拒绝。
    marker.unlink()
    doc = once_preflight(cfg)
    assert doc["status"] == "login_required"
    assert doc["exit_code"] == 3
    assert doc["action"] == "skip"
    assert "ctrip" in doc["login"]["missing_primary"]
    # 有标记也只报告文件在，不把内容写进结果。
    marker.write_text('{"cookie":"%s","saved_at":1}' % secret, encoding="utf-8")
    doc = once_preflight(cfg)
    blob = json.dumps(doc, ensure_ascii=False)
    assert secret not in blob
    assert doc["action"] == "crawl"
    assert doc["login"]["contents_read"] is False
    assert doc["login"]["verified"] is False
    states = [s["state"] for s in doc["login"]["slots"] if s["profile"] == "ctrip"]
    assert states == ["present_unread"]


def test_once_cli_refuses_without_calling_main(state, tmp_path):
    path = tmp_path / "config.yaml"
    cfg = _load(SAFE)
    cfg["crawler"]["collect"] = True
    cfg["users"][0]["platforms"] = ["ctrip", "qunar", "app_ctrip"]
    cfg["users"][0]["routes"][0]["enabled"] = True
    cfg["crawler"]["user_data_dir"] = str(tmp_path / "empty-user")
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    called = []
    code = once_cli(str(path), executor=lambda p: called.append(p) or 99)
    assert code == 3
    assert called == []
    doc = json.loads((state / "data" / "cloud-once.json").read_text(encoding="utf-8"))
    assert doc["status"] == "login_required"
    assert "qunar" in {s["platform"] for s in doc["skipped_platforms"]}
    assert "app_ctrip" in {s["platform"] for s in doc["skipped_platforms"]}


def test_disabled_route_and_retired_channel_do_not_crawl(state, tmp_path):
    path = tmp_path / "config.yaml"
    cfg = _load(SAFE)
    cfg["crawler"]["collect"] = True
    cfg["users"][0]["platforms"] = ["qunar"]
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert once_cli(str(path), executor=lambda p: 99) == 2
    cfg["users"][0]["platforms"] = ["ctrip"]
    cfg["users"][0]["routes"][0]["enabled"] = False
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert once_preflight(cfg)["status"] == "no_active_route"


def test_qualify_does_not_multiply_or_pad_or_keep_a_supplied_total():
    contract = crawl_contract(ROOT)
    assert contract["matches"] is True
    requested = {
        "specified": True, "adults": 2, "errors": [],
        "children": [{"age": 10, "band": "child"}],
        "cabin": "economy", "currency": "CNY",
    }
    raw = {
        "platform": "ctrip", "from_city": "SHA", "to_city": "CAN",
        "depart_date": "2099-01-01", "price": 800, "flight_no": "MU0001",
        "family_total": 9999,
        "extra": json.dumps({"childPrice": 400, "cookie": "CANARY_SECRET_DO_NOT_LEAK"}),
    }
    quote = qualify_offer(raw, requested, contract)
    blob = json.dumps(quote, ensure_ascii=False)
    assert "CANARY_SECRET_DO_NOT_LEAK" not in blob
    assert quote["list_price"] == 800
    assert quote["list_price"] != 900
    assert quote["family_total_cny"] is None
    assert quote["channel_child_unit_cny"] == 400
    assert quote["child_age_applied"] is False
    assert quote["price_role"] == "adult_reference"
    values = list(quote.values())
    for invented in (1600, 2000, 2400, 1200, 9999, 900):
        assert invented not in values
    assert "family_total" in quote["dropped_unverified_total_fields"]


def test_finish_once_marks_adult_rows_and_empty_is_not_success(state):
    cfg = _load(SAFE)

    def sweep():
        return None

    code = finish_once_crawl(cfg, None, sweep, lambda since: [])
    assert code == 4
    doc = json.loads((state / "data" / "cloud-once.json").read_text(encoding="utf-8"))
    assert doc["status"] == "crawl_finished_empty"
    assert doc["real_quote_completed"] is False
    assert doc["family_quote_ok"] is False

    def read_rows(since):
        assert len(since) == 19
        return [{
            "platform": "tongcheng", "from_city": "SHA", "to_city": "CAN",
            "depart_date": "2099-01-01", "price": 510, "flight_no": "CZ0001",
            "extra": "{}",
        }]

    code = finish_once_crawl(cfg, None, sweep, read_rows)
    assert code == 0
    doc = json.loads((state / "data" / "cloud-once.json").read_text(encoding="utf-8"))
    assert doc["status"] == "adult_reference_only"
    assert doc["quote_ok"] is True
    assert doc["family_quote_ok"] is False
    assert doc["quotes"][0]["list_price"] == 510
    assert doc["quotes"][0]["family_total_cny"] is None
    assert doc["real_quote_completed"] is False


def test_rows_since_filters_by_timestamp(tmp_path):
    db = tmp_path / "prices.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE flight_prices (id INTEGER PRIMARY KEY, platform TEXT, "
        "from_city TEXT, to_city TEXT, depart_date TEXT, price REAL, "
        "airline TEXT, flight_no TEXT, depart_time TEXT, arrive_time TEXT, "
        "fetched_at TEXT, extra TEXT)")
    con.execute(
        "INSERT INTO flight_prices (platform, from_city, to_city, depart_date, "
        "price, airline, flight_no, depart_time, arrive_time, fetched_at, extra) "
        "VALUES ('ctrip','SHA','CAN','2099-01-01',1,'','MU1','','','2099-01-01 00:00:00','')")
    con.commit()
    con.close()
    assert rows_since(str(db), "2099-01-01 00:00:01") == []
    got = rows_since(str(db), "2099-01-01 00:00:00")
    assert len(got) == 1
    assert got[0]["price"] == 1


def test_health_and_idle_scripts_write_files(state, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(open(SAFE, encoding="utf-8").read(), encoding="utf-8")
    assert health_cli(str(cfg_path), env={}) == 0
    health = json.loads((state / "data" / "cloud-health.json").read_text(encoding="utf-8"))
    assert health["status"] == "safe"
    assert idle_cli(str(cfg_path)) == 0
    idle = json.loads((state / "data" / "cloud-idle.json").read_text(encoding="utf-8"))
    assert idle["status"] == "scheduler_disabled"
    assert idle["scheduler_started"] is False


def test_idle_refuses_when_scheduler_would_run(state, tmp_path):
    path = tmp_path / "config.yaml"
    cfg = _load(SAFE)
    cfg["schedule"]["enabled"] = True
    cfg["schedule"]["interval_minutes"] = 10080
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert idle_cli(str(path)) == 2


def test_outbound_lists_bark_and_email_without_values():
    secret = "CANARY_MAIL_SECRET"
    problems = outbound_problems({
        "users": [{
            "notifier": {
                "win_toast": False,
                "digest": False,
                "bark": {"enabled": True, "key": secret},
                "email": {"enabled": False, "password": secret},
            },
        }],
    })
    blob = json.dumps(problems, ensure_ascii=False)
    assert secret not in blob
    assert "notifier_enabled" in blob
    assert "secret_material_present" in blob


def test_main_wires_cloud_gate_before_scheduler():
    text = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    body = text[text.index("\ndef main("):]
    health = body.index("health_cli")
    lock = body.index("acquire_instance_lock")
    install = body.index('ensure_chromium(["qunar"])')
    preempt = body.index("preempt(args, cfg, logger)")
    crawl = body.index("ensure_chromium(cfg.get")
    finish = body.index("finish_once_crawl(")
    guard = body.index("if not _sched_on(cfg)")
    sched = body.index("run_scheduler(")
    assert health < lock
    assert install < preempt < crawl
    assert finish < guard < sched
    assert "web.enabled=false，不监听控制台端口" in body


def test_shell_scripts_and_module_cli(state, tmp_path):
    for name in ("install.sh", "health.sh", "start.sh", "stop.sh", "run-once.sh"):
        path = os.path.join(ROOT, "deploy", "cloud", name)
        subprocess.check_call(["bash", "-n", path])
    env = os.environ.copy()
    env["TM_CLOUD_STATE_DIR"] = str(state)
    env.pop("TM_WEB_HOST", None)
    proc = subprocess.run(
        [sys.executable, "-m", "core.cloud_adapt", "health", "-c", SAFE],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "family_quote_ok=false" in proc.stdout
    proc = subprocess.run(
        [sys.executable, "-m", "core.cloud_adapt", "once", "-c", SAFE],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "collection_disabled" in proc.stdout
    stop = subprocess.run(
        ["bash", os.path.join(ROOT, "deploy", "cloud", "stop.sh")],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert stop.returncode == 0
    assert "没有由 deploy/cloud 启动的常驻进程" in stop.stdout


def test_invalid_child_age_is_an_error_not_a_price(state, tmp_path):
    cfg = _load(SAFE)
    cfg["passengers"]["children"] = [{"age": 99}]
    doc = once_preflight(cfg)
    assert doc["status"] == "passenger_config_invalid"
    assert doc["exit_code"] == 2
    assert doc["quotes"] == []
    assert doc["family_total_cny"] is None
