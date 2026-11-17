# -*- coding: utf-8 -*-
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""报价口径：只标记采集请求实际覆盖了谁，不换算全家价。

浏览器渠道的公开页面参数在源码里写死为 1 成人、0 儿童。
本模块不改这些请求，也不把成人单价乘人数、不加固定税费。
"""
from __future__ import annotations

import json
import os

# 与爬虫源码逐字对应。健康检查会再读这些文件；对不上就报漂移，
# 不能继续把旧说明当成当前请求。
CRAWL_LITERALS = (
    ("crawlers/ctrip.py", "adult=1&child=0&infant=0"),
    ("crawlers/ctrip.py", "cabin=y_s_c_f"),
    ("crawlers/ctrip_multicity.py", "adult=1&child=0&infant=0"),
    ("crawlers/tongcheng.py", "an=1&cn=0&baby=0"),
    ("crawlers/tongcheng.py", "direct=0"),
    ("crawlers/tuniu.py", '"adultQuantity": 1'),
    ("crawlers/tuniu.py", '"childQuantity": 0'),
    ("crawlers/qunar.py", "&child=0&baby=0"),
)

# 飞猪列表 URL 没有乘客参数。用「源码里不出现这些请求串」作证据。
FLIGGY_ABSENT = ("adult=", "child=", "infant=")

FETCH_PASSENGERS = {
    "adults": 1,
    "children": 0,
    "infants": 0,
    "child_ages": [],
    "applied_from_config": False,
}


def age_band(age: int) -> str:
    """只用于给配置里的年龄贴标签。不是从航司或 OTA 读到的儿童规则。"""
    if age < 2:
        return "infant"
    if age <= 11:
        return "child"
    return "adult_or_youth"


def parse_passengers(cfg: dict) -> dict:
    raw = (cfg or {}).get("passengers")
    if not isinstance(raw, dict):
        for user in (cfg or {}).get("users") or []:
            if isinstance(user, dict) and isinstance(user.get("passengers"), dict):
                raw = user["passengers"]
                break
    if not isinstance(raw, dict):
        return {
            "specified": False,
            "adults": None,
            "children": [],
            "currency": None,
            "cabin": "",
            "errors": [],
        }
    errors = []
    try:
        adults = int(raw.get("adults"))
    except (TypeError, ValueError):
        adults = None
        errors.append("passengers.adults 不是整数")
    if adults is not None and adults < 1:
        errors.append("passengers.adults 必须 >= 1")
        adults = None
    children = []
    incoming = raw.get("children") or []
    if not isinstance(incoming, list):
        errors.append("passengers.children 必须是列表")
        incoming = []
    for i, item in enumerate(incoming):
        age = item.get("age") if isinstance(item, dict) else item
        try:
            age_i = int(age)
        except (TypeError, ValueError):
            errors.append("children[%d].age 不是整数" % i)
            continue
        if age_i < 0 or age_i > 17:
            errors.append("children[%d].age=%d 超出 0–17" % (i, age_i))
            continue
        children.append({"age": age_i, "band": age_band(age_i)})
    currency = raw.get("currency")
    return {
        "specified": True,
        "adults": adults,
        "children": children,
        "currency": str(currency).upper() if currency else None,
        "cabin": str(raw.get("cabin") or ""),
        "errors": errors,
    }


def matches_fetch(requested: dict) -> bool:
    """配置是否刚好就是当前写死的 1 成人、0 儿童。没配置也算对不上全家请求。"""
    if not requested.get("specified"):
        return False
    if requested.get("errors"):
        return False
    if requested.get("adults") != 1:
        return False
    return not requested.get("children")


def degradation_notes(requested: dict, contract: dict) -> list:
    notes = [
        "采集请求仍是 1 成人、0 儿童、0 婴儿，配置里的人数和年龄不会改这些请求。",
        "本工具不计算全家总价：不会把成人价乘人数，也不会加一笔固定税费。",
        "family_total_cny 恒为 null。列表上的数字只作成人参考，含税与币种都未按本次乘客组合核验。",
    ]
    if not contract.get("matches", False):
        notes.append("爬虫源码里的乘客参数与记录不一致，不能沿用旧的请求说明。")
    cabin = str(requested.get("cabin") or "")
    if cabin and cabin.lower() in ("economy", "y", "经济舱"):
        notes.append(
            "配置写了经济舱，但携程国内列表参数仍含 cabin=y_s_c_f"
            "（经济/超级经济/公务/头等），本适配没有另加经济舱过滤。")
    if requested.get("specified") and not matches_fetch(requested):
        bits = []
        if requested.get("adults") is not None:
            bits.append("%d 成人" % requested["adults"])
        for child in requested.get("children") or []:
            bits.append("%d 岁（标记为 %s）" % (child["age"], child["band"]))
        notes.append("配置请求的是 %s。该组合没有发给渠道。" % "、".join(bits))
    elif not requested.get("specified"):
        notes.append("未配置乘客。即使后来采到价格，也只是渠道列表的成人参考价。")
    return notes


def crawl_contract(root: str) -> dict:
    checks = []
    ok = True
    for rel, needle in CRAWL_LITERALS:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as f:
                found = needle in f.read()
        except OSError:
            found = False
        ok = ok and found
        checks.append({"file": rel, "needle": needle, "found": found})
    fliggy = os.path.join(root, "crawlers", "fliggy.py")
    absent_ok = True
    try:
        with open(fliggy, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
        absent_ok = False
    for needle in FLIGGY_ABSENT:
        found = needle in text
        if found:
            absent_ok = False
        checks.append({
            "file": "crawlers/fliggy.py",
            "needle": "absent:" + needle,
            "found": not found,
        })
    return {
        "matches": bool(ok and absent_ok),
        "fetch_passengers": dict(FETCH_PASSENGERS),
        "checks": checks,
        "direct_only_changes_fetch": False,
        "direct_only_code_effect": "only_zeroes_alert_transfer",
        "economy_filter_applied_to_fetch": False,
        "notes": [
            "去哪儿网页 URL 仍写 child=0，但该网页渠道已停服，不能当作成人价或儿童价来源。",
            "飞猪列表没有乘客人数参数。爬虫文件写明其 PC 页展示价不含税费。",
            "告警链的 FLIGGY_TAX_PAD 当前为 0，不会再加 100 元。"
            "不加税垫，也不等于指定乘客的含税全家价。",
        ],
    }


def _hints(extra) -> dict:
    """只取允许的数字。不拷贝 extra 原文，避免把会话材料带出来。"""
    if not extra or isinstance(extra, bool):
        return {}
    obj = extra
    if isinstance(extra, str):
        try:
            obj = json.loads(extra)
        except (TypeError, ValueError):
            return {}
    blobs = []
    if isinstance(obj, dict):
        blobs = [obj]
    elif isinstance(obj, list):
        blobs = [x for x in obj if isinstance(x, dict)]
    hints = {}
    for blob in blobs:
        for key in ("childPrice", "infantPrice"):
            value = blob.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value > 0 and key not in hints:
                hints[key] = value
    return hints


def qualify_offer(raw: dict, requested: dict, contract: dict) -> dict:
    """一行列表价 → 成人参考。调用方塞来的全家价字段一律丢掉。"""
    price = raw.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        list_price = None
    else:
        list_price = price
    hints = _hints(raw.get("extra"))
    child_unit = hints.get("childPrice")
    infant_unit = hints.get("infantPrice")
    dropped = []
    for key in ("family_total", "family_total_cny", "total", "tax_included_total"):
        if key in (raw or {}):
            dropped.append(key)
    quote = {
        "platform": raw.get("platform"),
        "from": raw.get("from_city") or raw.get("from"),
        "to": raw.get("to_city") or raw.get("to"),
        "date": raw.get("depart_date") or raw.get("date"),
        "flight_no": raw.get("flight_no") or "",
        "list_price": list_price,
        "currency_status": "unverified",
        "tax_included_status": "unverified",
        "price_role": "adult_reference",
        "family_total_cny": None,
        "family_total_status": "unverified",
        "child_age_applied": False,
        "passengers_applied_to_fetch": False,
        "fetch_passengers": dict(FETCH_PASSENGERS),
    }
    if child_unit is not None:
        quote["channel_child_unit_cny"] = child_unit
        quote["channel_child_unit_note"] = (
            "渠道报文里的儿童单价。没有按配置年龄核验，禁止与成人价相加后当成全家总价。")
    if infant_unit is not None:
        quote["channel_infant_unit_cny"] = infant_unit
        quote["channel_infant_unit_note"] = (
            "渠道报文里的婴儿单价。没有核验，禁止计入全家总价。")
    if dropped:
        quote["dropped_unverified_total_fields"] = dropped
    quote["degradation"] = degradation_notes(requested, contract)
    return quote
