# -*- coding: utf-8 -*-
"""
对比模块：本地订单 ↔ 飞书单据
输出四类：未申请购买 / 概述待确认 / 已申请未提交发票 / 已闭环
"""
import re

from feishu_api import FeishuApproval


def _norm(v):
    """从 '22.95 CNY'、'111.0元'、111.0 等任意格式提取金额数字"""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        m = re.search(r"-?\d+(\.\d+)?", str(v))
        if not m:
            return None
        return round(float(m.group()), 2)
    except Exception:
        return None


def _valid_status(s):
    """只有这些状态的单据才算数（撤回/驳回/终止不算）"""
    return s in ("已通过", "审批中", "进行中")


def compare(orders: list, buy_rows: list, invoice_rows: list) -> dict:
    """
    orders: organize.list_order_folders 的结果
    buy_rows / invoice_rows: FeishuApproval.summarize 的结果列表
    返回: {"no_buy": [...], "buy_ok_no_invoice": [...], "closed": [...], "unknown": [...]}
    每个条目: {"订单": 文件夹名, "金额": x, "购买申请": str, "发票": str}
    """
    # 飞书金额索引（只收有效状态；撤回/驳回/终止不算）
    buy_amt = {}   # amt -> 概述
    for r in buy_rows:
        if not _valid_status(r.get("状态")):
            continue
        a = _norm(r.get("金额"))
        if a is None:
            continue
        buy_amt.setdefault(a, r.get("概述", ""))
    inv_amt = {}   # amt -> 状态（用于展示核对）
    for r in invoice_rows:
        if not _valid_status(r.get("状态")):
            continue
        a = _norm(r.get("金额"))
        if a is not None:
            inv_amt.setdefault(a, r.get("状态", ""))

    no_buy, buy_ok_no_invoice, closed, unknown = [], [], [], []
    for o in orders:
        amt = _norm(o.get("amt"))
        item = {
            "订单": o["folder"],
            "金额": amt,
            "购买申请": "",
            "发票": "",
        }
        has_buy = amt is not None and amt in buy_amt
        has_inv = amt is not None and amt in inv_amt
        if has_buy:
            item["购买申请"] = buy_amt[amt]
        if has_inv:
            item["发票"] = f"已提交({inv_amt[amt]})"
        if amt is None:
            unknown.append(item)
        elif not has_buy:
            no_buy.append(item)
        elif not has_inv:
            buy_ok_no_invoice.append(item)
        else:
            closed.append(item)
    return {
        "no_buy": no_buy,
        "buy_ok_no_invoice": buy_ok_no_invoice,
        "closed": closed,
        "unknown": unknown,
    }


def build_overview(orders: list, buy_rows: list, invoice_rows: list) -> list:
    """
    总览视图：每个订单一行，直接标出 购买申请 / 发票提交 是否齐全。
    返回: [{订单, 金额, 购买申请, 申请概述, 发票, 状态}]
    """
    buy_amt = {}
    for r in buy_rows:
        if not _valid_status(r.get("状态")):
            continue
        a = _norm(r.get("金额"))
        if a is None:
            continue
        buy_amt.setdefault(a, r.get("概述", ""))
    inv_amt = {}
    for r in invoice_rows:
        if not _valid_status(r.get("状态")):
            continue
        a = _norm(r.get("金额"))
        if a is not None:
            inv_amt.setdefault(a, r.get("状态", ""))

    out = []
    for o in orders:
        amt = _norm(o.get("amt"))
        has_buy = amt is not None and amt in buy_amt
        has_inv = amt is not None and amt in inv_amt
        if amt is None:
            status = "未解析金额"
        elif not has_buy:
            status = "❌ 缺购买申请"
        elif not has_inv:
            status = "❌ 缺发票提交"
        else:
            status = "✅ 全部完成"
        out.append({
            "订单": o["folder"],
            "金额": amt if amt is not None else "",
            "购买申请": "✅ 已申请" if has_buy else "❌ 未申请",
            "申请概述": buy_amt.get(amt, "") if has_buy else "",
            "发票": f"✅ 已提交({inv_amt.get(amt, '')})" if has_inv else "❌ 未提交",
            "状态": status,
        })
    return out


def build_report(result: dict) -> str:
    lines = []
    lines.append("===== 发票整理对比报告 =====")
    lines.append("")
    lines.append(f"【未申请购买】{len(result['no_buy'])} 个")
    for it in result["no_buy"]:
        lines.append(f"  - {it['订单']}（{it['金额']}元）")
    lines.append("")
    lines.append(f"【已申请购买、未提交发票】{len(result['buy_ok_no_invoice'])} 个")
    for it in result["buy_ok_no_invoice"]:
        lines.append(f"  - {it['订单']}（{it['金额']}元）购买申请概述: {it['购买申请'] or '无'}")
    lines.append("")
    lines.append(f"【已闭环】{len(result['closed'])} 个")
    for it in result["closed"]:
        lines.append(f"  - {it['订单']}（{it['金额']}元）")
    lines.append("")
    lines.append(f"【无法解析】{len(result['unknown'])} 个")
    for it in result["unknown"]:
        lines.append(f"  - {it['订单']}")
    return "\n".join(lines)
