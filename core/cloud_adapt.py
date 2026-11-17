# -*- coding: utf-8 -*-
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""无桌面云端适配：真正关掉内置调度、单次退出、健康检查、报价降级。

不启动浏览器，不读取会话文件内容，不发送通知。
采集仍交给上游 main.py --once，且只有配置、航线和登录标记都允许时才会交给它。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

import yaml

from core.login_slots import LOGIN_SLOTS, slot_profile
from core.paths import app_root, resolve
from core.quote_basis import (
    crawl_contract,
    degradation_notes,
    parse_passengers,
    qualify_offer,
)

SCHEMA = "ticket-monitoring.cloud/v1"
SECRET_KEYS = {
    "token", "secret", "password", "webhook", "send_key", "access_token",
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _flag_present(block: dict, key: str) -> bool:
    return isinstance(block, dict) and key in block


def schedule_enabled(cfg: dict) -> bool:
    """缺省保持上游行为（会调度）。只有显式关闭才是真关。

    interval_minutes=10080 只是把下一轮推迟，不是关闭。
    """
    block = (cfg or {}).get("schedule") or {}
    if not _flag_present(block, "enabled"):
        return True
    return _as_bool(block.get("enabled"), default=True)


def collection_enabled(cfg: dict) -> bool:
    """缺省保持上游行为（会采集）。云端模板必须写 crawler.collect: false。"""
    block = (cfg or {}).get("crawler") or {}
    if not _flag_present(block, "collect"):
        return True
    return _as_bool(block.get("collect"), default=True)


def web_enabled(cfg: dict) -> bool:
    block = (cfg or {}).get("web") or {}
    if not _flag_present(block, "enabled"):
        return True
    return _as_bool(block.get("enabled"), default=True)


def upstream_version(root: str = None) -> str:
    root = root or app_root()
    path = os.path.join(root, "main.py")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                text = line.strip()
                if text.startswith("__version__"):
                    return text.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "unknown"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def state_dir() -> str:
    override = os.environ.get("TM_CLOUD_STATE_DIR")
    if override:
        return os.path.abspath(override)
    return ""


def _anchor(path: str, default: str) -> str:
    base = state_dir()
    if base:
        rel = path or default
        if os.path.isabs(rel):
            return rel
        return os.path.join(base, rel)
    return resolve(path or default, default)


def data_file(cfg: dict, name: str) -> str:
    db = _anchor(((cfg.get("output") or {}).get("db_path") or "data/prices.db"),
                 "data/prices.db")
    return os.path.join(os.path.dirname(db), name)


def log_file(cfg: dict) -> str:
    raw = ((cfg.get("output") or {}).get("log_path") or "logs/monitor.log")
    return os.path.join(os.path.dirname(_anchor(raw, "logs/monitor.log")),
                        "cloud-ops.jsonl")


def user_data_root(cfg: dict) -> str:
    raw = str(((cfg.get("crawler") or {}).get("user_data_dir") or "user_data"))
    return _anchor(raw, "user_data")


def write_json(path: str, doc: dict) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def append_ops(cfg: dict, doc: dict) -> None:
    path = log_file(cfg)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        line = {
            "at": doc.get("generated_at"),
            "schema": doc.get("schema"),
            "kind": doc.get("kind"),
            "status": doc.get("status"),
            "exit_code": doc.get("exit_code"),
            "family_quote_ok": False,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _notifiers(cfg: dict):
    if isinstance((cfg or {}).get("notifier"), dict):
        yield cfg["notifier"]
    for user in (cfg or {}).get("users") or []:
        if isinstance(user, dict) and isinstance(user.get("notifier"), dict):
            yield user["notifier"]


def _enabled_channels(notifier: dict) -> list:
    found = []
    for name in ("dingtalk", "serverchan", "email", "ntfy", "bark", "aliyun"):
        block = notifier.get(name) or {}
        if isinstance(block, dict) and _as_bool(block.get("enabled"), False):
            found.append(name)
    emails = notifier.get("emails")
    if isinstance(emails, list):
        for i, block in enumerate(emails):
            if isinstance(block, dict) and _as_bool(block.get("enabled"), False):
                found.append("emails[%d]" % i)
    alert = notifier.get("channel_alert") or {}
    if isinstance(alert, dict) and _as_bool(alert.get("enabled"), False):
        found.append("channel_alert")
    toast = notifier.get("win_toast", None)
    if isinstance(toast, dict):
        if _as_bool(toast.get("enabled"), False):
            found.append("win_toast")
    elif _as_bool(toast, False):
        found.append("win_toast")
    return found


def _secret_paths(obj, path: str = "") -> list:
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = "%s.%s" % (path, key) if path else str(key)
            if key in SECRET_KEYS and isinstance(value, str) and value.strip():
                found.append(child)
            elif (key == "key" and path.endswith("bark")
                  and isinstance(value, str) and value.strip()):
                found.append(child)
            else:
                found.extend(_secret_paths(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found.extend(_secret_paths(value, "%s[%d]" % (path, i)))
    return found


def outbound_problems(cfg: dict) -> list:
    problems = []
    for notifier in _notifiers(cfg):
        channels = _enabled_channels(notifier)
        if channels:
            problems.append({
                "level": "error",
                "code": "notifier_enabled",
                "detail": "外发通知开着：" + "、".join(channels),
            })
        if _as_bool(notifier.get("digest"), False):
            problems.append({
                "level": "warning",
                "code": "digest_on",
                "detail": "notifier.digest 为 true。通道关闭时不会发出，模板仍应关掉。",
            })
        if "win_toast" not in notifier:
            problems.append({
                "level": "warning",
                "code": "win_toast_default_on",
                "detail": "win_toast 缺省在 Windows 上会开。云端模板应写 false。",
            })
        secrets = _secret_paths(notifier)
        if secrets:
            problems.append({
                "level": "error",
                "code": "secret_material_present",
                "detail": "通知配置里有非空凭据字段：" + "、".join(secrets),
            })
    return problems


def bind_host(cfg: dict, env: dict = None) -> tuple:
    env = os.environ if env is None else env
    from_env = str(env.get("TM_WEB_HOST") or "").strip()
    if from_env:
        return from_env, "env"
    from_cfg = str(((cfg.get("web") or {}).get("host") or "")).strip()
    if from_cfg:
        return from_cfg, "config"
    return "127.0.0.1", "default"


def _routes(cfg: dict) -> list:
    rows = []
    for user in (cfg or {}).get("users") or []:
        if not isinstance(user, dict):
            continue
        for route in user.get("routes") or []:
            if isinstance(route, dict):
                rows.append(route)
    return rows


def active_routes(cfg: dict) -> list:
    return [r for r in _routes(cfg) if r.get("enabled") is not False]


def route_preferences(cfg: dict) -> list:
    prefs = []
    for route in _routes(cfg):
        direct = bool(route.get("direct_only"))
        prefs.append({
            "from": route.get("from"),
            "to": route.get("to"),
            "dates": list(route.get("dates") or []),
            "enabled": route.get("enabled") is not False,
            "direct_only": direct,
            "direct_only_effect": (
                "only_zeroes_alert_transfer" if direct else "unset"),
            "fetch_filtered_to_direct": False,
            "fetch_filtered_to_economy": False,
            "alert_direct": route.get("alert_direct", 0),
            "alert_transfer": 0 if direct else route.get("alert_transfer", 0),
        })
    return prefs


def configured_platforms(cfg: dict) -> list:
    found = []
    seen = set()

    def add(items):
        for item in items or []:
            name = str(item or "").strip()
            if name and name not in seen:
                seen.add(name)
                found.append(name)

    users = [u for u in (cfg.get("users") or []) if isinstance(u, dict)]
    if not users:
        plats = cfg.get("platforms")
        add(plats if plats is not None else ["qunar", "fliggy", "tongcheng", "tuniu"])
        return found
    for user in users:
        plats = user.get("platforms")
        if plats is None:
            plats = cfg.get("platforms")
        if plats is None:
            plats = ["qunar", "fliggy", "tongcheng", "tuniu"]
        add(plats)
    return found


def retired_platforms() -> set:
    by = {}
    for meta in LOGIN_SLOTS.values():
        plat = str(meta.get("plat") or "")
        by.setdefault(plat, []).append(bool(meta.get("service_closed")))
    return {plat for plat, flags in by.items() if flags and all(flags)}


def _marker(path: str) -> str:
    if not os.path.isfile(path):
        return "missing"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "unreadable"
    if size <= 2:
        return "empty"
    return "present_unread"


def login_report(cfg: dict, platforms: list) -> dict:
    """只看会话标记文件在不在。不开文件，因此不能证明已经登录。"""
    root = user_data_root(cfg)
    retired = retired_platforms()
    slots = []
    missing_primary = []
    for plat in platforms:
        if plat in retired or plat.startswith("app_"):
            continue
        metas = [m for m in LOGIN_SLOTS.values() if m.get("plat") == plat]
        if not metas:
            slots.append({
                "platform": plat,
                "profile": plat,
                "state": "unknown_platform",
                "required": False,
            })
            continue
        for meta in metas:
            profile = slot_profile(meta.get("slot") or plat)
            path = os.path.join(root, profile, "session-state.json")
            state = _marker(path)
            required = profile == plat
            if required and state != "present_unread":
                missing_primary.append(plat)
            slots.append({
                "platform": plat,
                "slot": meta.get("slot"),
                "profile": profile,
                "state": state,
                "required": required,
                "login_url": meta.get("login_url") or "",
                "contents_read": False,
            })
    return {
        "verified": False,
        "contents_read": False,
        "missing_primary": missing_primary,
        "slots": slots,
    }


def _problems_for_safety(cfg: dict, env: dict) -> list:
    problems = []
    if schedule_enabled(cfg):
        interval = ((cfg.get("schedule") or {}).get("interval_minutes"))
        problems.append({
            "level": "error",
            "code": "scheduler_enabled",
            "detail": (
                "内置调度会运行。interval_minutes=%s 只是间隔，不是关闭。"
                "要真正停掉，必须写 schedule.enabled: false。"
            ) % interval,
        })
    host, source = bind_host(cfg, env)
    if web_enabled(cfg):
        if host not in LOOPBACK:
            problems.append({
                "level": "error",
                "code": "public_bind",
                "detail": "web.enabled 且监听 %s（来自 %s）。云端模板不应监听非环回地址。"
                % (host, source),
            })
        else:
            problems.append({
                "level": "warning",
                "code": "web_loopback",
                "detail": "web.enabled 会在 %s 监听。云端模板应写 web.enabled: false。" % host,
            })
    elif host not in LOOPBACK:
        problems.append({
            "level": "warning",
            "code": "latent_public_host",
            "detail": "web.enabled 为 false，但 host=%s。打开网页前应改回 127.0.0.1。" % host,
        })
    problems.extend(outbound_problems(cfg))
    android = (cfg.get("android") or {})
    if _as_bool(android.get("enabled"), False):
        problems.append({
            "level": "error",
            "code": "android_enabled",
            "detail": "android.enabled 为 true。Linux 云端不启用 App 渠道。",
        })
    if (cfg.get("crawler") or {}).get("proxy"):
        problems.append({
            "level": "warning",
            "code": "proxy_set",
            "detail": "crawler.proxy 非空。云端模板不应替用户改出口。",
        })
    if not _flag_present((cfg.get("crawler") or {}), "collect"):
        problems.append({
            "level": "warning",
            "code": "collect_default_on",
            "detail": "crawler.collect 缺省。上游会采集。云端模板应写 false。",
        })
    elif collection_enabled(cfg):
        problems.append({
            "level": "warning",
            "code": "collection_armed",
            "detail": "crawler.collect 为 true。单次命令在登录标记不足时会拒绝，不会自动登录。",
        })
    users = [u for u in (cfg.get("users") or []) if isinstance(u, dict)]
    if users and any(u.get("platforms") is None and cfg.get("platforms") is None
                     for u in users):
        problems.append({
            "level": "warning",
            "code": "platforms_default",
            "detail": "platforms 缺省时上游会启用 qunar/fliggy/tongcheng/tuniu。应写空列表或显式渠道。",
        })
    if not crawl_contract(app_root()).get("matches"):
        problems.append({
            "level": "warning",
            "code": "crawl_contract_drift",
            "detail": "爬虫源码中的乘客参数与 cloud 记录不一致。全家价仍然未核验。",
        })
    return problems


def quote_block(cfg: dict, root: str = None) -> dict:
    requested = parse_passengers(cfg)
    contract = crawl_contract(root or app_root())
    return {
        "passengers": requested,
        "fetch_passengers": contract["fetch_passengers"],
        "passengers_applied_to_fetch": False,
        "family_total_cny": None,
        "family_total_status": "unverified",
        "family_quote_ok": False,
        "price_role": "adult_reference",
        "currency_status": "unverified",
        "tax_included_status": "unverified",
        "crawl_contract_matches": contract["matches"],
        "direct_only_changes_fetch": False,
        "economy_filter_applied_to_fetch": False,
        "degradation": degradation_notes(requested, contract),
        "contract_notes": contract["notes"],
    }


def _base(kind: str, status: str, code: int, cfg: dict) -> dict:
    return {
        "schema": SCHEMA,
        "kind": kind,
        "version": upstream_version(),
        "generated_at": _now(),
        "status": status,
        "exit_code": code,
        "ok": False,
        "quote_ok": False,
        "family_quote_ok": False,
        "scheduler_started": False,
        "browser_launched": False,
        "network_fetch": False,
        "quotes": [],
        "family_total_cny": None,
    }


def assess(cfg: dict, env: dict = None, root: str = None) -> dict:
    env = os.environ if env is None else env
    problems = _problems_for_safety(cfg, env)
    errors = [p for p in problems if p["level"] == "error"]
    code = 0 if not errors else 1
    host, source = bind_host(cfg, env)
    doc = _base("health", "safe" if code == 0 else "unsafe", code, cfg)
    doc["ok"] = code == 0
    doc.update(quote_block(cfg, root))
    doc.update({
        "schedule_enabled": schedule_enabled(cfg),
        "collection_enabled": collection_enabled(cfg),
        "web_enabled": web_enabled(cfg),
        "bind_host": host,
        "bind_host_source": source,
        "platforms": configured_platforms(cfg),
        "routes": route_preferences(cfg),
        "login": login_report(cfg, configured_platforms(cfg)),
        "problems": problems,
        "ready_to_quote": False,
        "real_quote_completed": False,
    })
    last = data_file(cfg, "cloud-once.json")
    if os.path.isfile(last):
        try:
            with open(last, encoding="utf-8") as f:
                prev = json.load(f)
            doc["last_once"] = {
                "status": prev.get("status"),
                "generated_at": prev.get("generated_at"),
                "quote_count": len(prev.get("quotes") or []),
                "family_quote_ok": False,
                "real_quote_completed": False,
            }
        except (OSError, ValueError):
            doc["last_once"] = {"status": "unreadable"}
    return doc


def once_preflight(cfg: dict, root: str = None) -> dict:
    requested = parse_passengers(cfg)
    contract = crawl_contract(root or app_root())
    platforms = configured_platforms(cfg)
    retired = retired_platforms()
    login = login_report(cfg, platforms)
    browser = [p for p in platforms if p not in retired and not p.startswith("app_")]
    skipped = []
    for plat in platforms:
        if plat in retired:
            skipped.append({"platform": plat, "reason": "web_retired"})
        elif plat.startswith("app_"):
            skipped.append({"platform": plat, "reason": "app_channel_not_used"})
    doc = _base("once", "collection_disabled", 0, cfg)
    doc.update({
        "passengers": requested,
        "fetch_passengers": contract["fetch_passengers"],
        "passengers_applied_to_fetch": False,
        "family_total_status": "unverified",
        "price_role": "adult_reference",
        "currency_status": "unverified",
        "tax_included_status": "unverified",
        "degradation": degradation_notes(requested, contract),
        "crawl_contract_matches": contract["matches"],
        "platforms": platforms,
        "skipped_platforms": skipped,
        "browser_platforms": browser,
        "routes": route_preferences(cfg),
        "login": login,
        "collection_enabled": collection_enabled(cfg),
        "ready_to_quote": False,
        "real_quote_completed": False,
        "action": "skip",
    })
    if requested.get("errors"):
        doc["status"] = "passenger_config_invalid"
        doc["exit_code"] = 2
        doc["error"] = "；".join(requested["errors"])
        return doc
    if not collection_enabled(cfg):
        doc["status"] = "collection_disabled"
        doc["exit_code"] = 0
        doc["error"] = "crawler.collect 为 false 或缺省被模板关掉。未访问任何机票网站。"
        return doc
    if not active_routes(cfg):
        doc["status"] = "no_active_route"
        doc["exit_code"] = 2
        doc["error"] = "没有 enabled 的航线。未启动浏览器。"
        return doc
    if not browser:
        reason = "no_browser_channel"
        if platforms and all(p in retired for p in platforms):
            reason = "web_channel_retired"
        elif platforms and all(str(p).startswith("app_") for p in platforms):
            reason = "app_channel_unsupported"
        doc["status"] = reason
        doc["exit_code"] = 2
        doc["error"] = "没有可运行的浏览器渠道。未启动浏览器，也没有报价。"
        return doc
    if login["missing_primary"]:
        doc["status"] = "login_required"
        doc["exit_code"] = 3
        doc["error"] = (
            "这些渠道没有未读取的会话标记文件，拒绝采集："
            + "、".join(login["missing_primary"])
            + "。本程序没有读取 cookie，也不会自动登录。"
        )
        return doc
    doc["action"] = "crawl"
    doc["status"] = "login_marker_present_unverified"
    doc["exit_code"] = 0
    doc["error"] = (
        "只确认会话标记文件存在，没有读取内容，不能证明站点仍认可登录。"
        "若继续，将交给上游单次采集；结果仍只是成人参考价。"
    )
    return doc


def rows_since(db_path: str, since: str) -> list:
    if not db_path or not os.path.isfile(db_path):
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        try:
            cur = con.execute(
                "SELECT platform, from_city, to_city, depart_date, price, "
                "airline, flight_no, depart_time, arrive_time, fetched_at, extra "
                "FROM flight_prices WHERE fetched_at >= ? ORDER BY id",
                (since,))
        except sqlite3.Error:
            return []
        return [dict(row) for row in cur.fetchall()]
    finally:
        con.close()


def finish_once_crawl(cfg: dict, logger, sweep, read_rows, root: str = None) -> int:
    """调用方已经决定可以采集。这里跑一轮并给每行贴上成人参考标记。"""
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error = None
    try:
        sweep()
    except Exception as exc:
        error = "%s: %s" % (type(exc).__name__, exc)
        if logger is not None:
            logger.exception("[云端] 单次采集失败")
    try:
        rows = list(read_rows(started) or [])
    except Exception as exc:
        rows = []
        extra = "read: %s" % exc
        error = extra if not error else error + "; " + extra
    requested = parse_passengers(cfg)
    contract = crawl_contract(root or app_root())
    quotes = [qualify_offer(row, requested, contract) for row in rows]
    doc = _base("once", "crawl_error" if error else (
        "adult_reference_only" if quotes else "crawl_finished_empty"),
                4 if error or not quotes else 0, cfg)
    doc.update({
        "browser_launched": True,
        "network_fetch": True,
        "passengers": requested,
        "fetch_passengers": contract["fetch_passengers"],
        "passengers_applied_to_fetch": False,
        "family_total_status": "unverified",
        "price_role": "adult_reference",
        "degradation": degradation_notes(requested, contract),
        "quotes": quotes,
        "quote_ok": bool(quotes) and not error,
        "error": error,
        "real_quote_completed": False,
        "ready_to_quote": False,
    })
    if quotes and not error:
        doc["error"] = (
            "得到 %d 条列表价。它们是成人参考，不是已核验的人民币含税全家价。"
            % len(quotes))
    elif not error:
        doc["error"] = "单次采集结束，但没有新的列表价。不能当成查价成功，也不能编造价格。"
    path = write_json(data_file(cfg, "cloud-once.json"), doc)
    append_ops(cfg, doc)
    if logger is not None:
        logger.info("[云端] 单次结果 %s → %s", doc["status"], path)
    _print_once(doc, path)
    return int(doc["exit_code"])


def _print_once(doc: dict, path: str) -> None:
    print("status=%s exit=%s family_quote_ok=false quotes=%d"
          % (doc.get("status"), doc.get("exit_code"), len(doc.get("quotes") or [])))
    if doc.get("error"):
        print(doc["error"])
    print(path)


def write_preflight(cfg: dict, doc: dict) -> str:
    path = write_json(data_file(cfg, "cloud-once.json"), doc)
    append_ops(cfg, doc)
    return path


def should_skip_preempt(args) -> bool:
    if getattr(args, "login", None):
        return True
    for name in ("setup_login", "report", "setup", "install_browser"):
        if getattr(args, name, False):
            return True
    return False


def preempt(args, cfg, logger=None):
    """在下载浏览器和进入调度器之前决定要不要直接退出。

    返回 None 表示继续走上游流程；返回整数表示进程应以此码退出。
    """
    if should_skip_preempt(args):
        return None
    if getattr(args, "once", False):
        doc = once_preflight(cfg)
        if doc.get("action") != "crawl":
            path = write_preflight(cfg, doc)
            if logger is not None:
                logger.info("[云端] 单次运行未采集：%s", doc.get("status"))
            _print_once(doc, path)
            return int(doc["exit_code"])
        return None
    if not schedule_enabled(cfg):
        doc = _base("idle", "scheduler_disabled", 0, cfg)
        doc["ok"] = True
        doc.update(quote_block(cfg))
        doc["collection_enabled"] = collection_enabled(cfg)
        doc["web_enabled"] = web_enabled(cfg)
        doc["error"] = "schedule.enabled=false。未进入定时调度，未监听，未采集。"
        path = write_json(data_file(cfg, "cloud-idle.json"), doc)
        append_ops(cfg, doc)
        if logger is not None:
            logger.info("[云端] 内置调度已禁用，进程退出 → %s", path)
        print("内置调度已禁用（schedule.enabled=false）。未监听端口，未采集。")
        print(path)
        return 0
    return None


def load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("配置根节点必须是映射")
    return data


def health_cli(config_path: str, env: dict = None) -> int:
    if not os.path.isfile(config_path):
        print("缺少配置文件：%s" % config_path)
        return 2
    try:
        cfg = load_cfg(config_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print("配置无法读取：%s" % exc)
        return 2
    doc = assess(cfg, env=env)
    path = write_json(data_file(cfg, "cloud-health.json"), doc)
    append_ops(cfg, doc)
    print("status=%s exit=%s family_quote_ok=false schedule_enabled=%s"
          % (doc["status"], doc["exit_code"], str(doc["schedule_enabled"]).lower()))
    for problem in doc["problems"]:
        print("[%s] %s: %s" % (problem["level"], problem["code"], problem["detail"]))
    print(path)
    return int(doc["exit_code"])


def idle_cli(config_path: str) -> int:
    if not os.path.isfile(config_path):
        print("缺少配置文件：%s" % config_path)
        return 2
    cfg = load_cfg(config_path)
    if schedule_enabled(cfg):
        print("拒绝启动：schedule.enabled 不是 false。把间隔调大并没有关掉调度。")
        return 2
    doc = _base("idle", "scheduler_disabled", 0, cfg)
    doc["ok"] = True
    doc.update(quote_block(cfg))
    doc["error"] = "start 没有留下常驻进程。"
    path = write_json(data_file(cfg, "cloud-idle.json"), doc)
    append_ops(cfg, doc)
    print("未启动常驻进程。内置调度保持关闭。")
    print(path)
    return 0


def _exec_main_once(config_path: str) -> None:
    root = app_root()
    main_py = os.path.join(root, "main.py")
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, main_py, "--once", "-c", config_path])


def once_cli(config_path: str, executor=None) -> int:
    if not os.path.isfile(config_path):
        print("缺少配置文件：%s" % config_path)
        return 2
    try:
        cfg = load_cfg(config_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print("配置无法读取：%s" % exc)
        return 2
    doc = once_preflight(cfg)
    if doc.get("action") != "crawl":
        path = write_preflight(cfg, doc)
        _print_once(doc, path)
        return int(doc["exit_code"])
    path = write_preflight(cfg, doc)
    print("会话标记存在但未核验。交给上游 python main.py --once。")
    print(path)
    if executor is None:
        _exec_main_once(os.path.abspath(config_path))
        return 5
    return int(executor(os.path.abspath(config_path)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.cloud_adapt",
        description="云端无桌面检查与单次运行（不替代上游采集实现）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("health", "once", "idle"):
        cmd = sub.add_parser(name)
        cmd.add_argument("-c", "--config", default="config.yaml")
    args = parser.parse_args(argv)
    if args.cmd == "health":
        return health_cli(args.config)
    if args.cmd == "idle":
        return idle_cli(args.config)
    return once_cli(args.config)


if __name__ == "__main__":
    sys.exit(main())
