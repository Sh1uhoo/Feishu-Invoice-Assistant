# -*- coding: utf-8 -*-
"""
整理模块
- 扫描发票根目录下的订单文件夹（0X-xxx-金额元）
- 解析订单：名称 / 金额 / 文件构成
- 汇总购买记录截图到 购买记录\\ 目录
（三合一 PDF 合并已移除：由用户已有的合并脚本负责，避免错误合并方式）
"""
import os
import re
import shutil

ORDER_RE = re.compile(r"^(\d+)[-_—].*?(\d+(?:\.\d+)?)\s*元\s*$")

# 关键词分类：支付记录 vs 购买记录
PAY_KEYWORDS = ["支付", "付款", "账单", "交易", "zhifu", "pay", "付款成功", "已支付"]
BUY_KEYWORDS = ["购买", "订单", "商品", "详情", "buy", "order", "goumai"]


def parse_order_dir(name: str):
    """从文件夹名解析 (序号, 名称, 金额)；无法解析返回 None"""
    m = ORDER_RE.match(name)
    if not m:
        # 兜底：尝试提取任意数字+元
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*元", name)
        if not m2:
            return None
        return (None, name, float(m2.group(1)))
    seq = int(m.group(1))
    amt = float(m.group(2))
    # 名称 = 去掉序号前缀与金额后缀
    tail = re.sub(r"^\d+[-—_]", "", name)
    tail = re.sub(r"[-—_]\d+(?:\.\d+)?\s*元\s*$", "", tail)
    return (seq, tail, amt)


def classify_image(filename: str) -> str:
    """按文件名判断图片类别: pay / buy / unknown"""
    f = filename.lower()
    if any(k in f for k in PAY_KEYWORDS):
        return "pay"
    if any(k in f for k in BUY_KEYWORDS):
        return "buy"
    return "unknown"


def list_order_folders(root: str) -> list:
    """返回订单文件夹信息列表: [{seq, name, amt, path, files}]"""
    if not os.path.isdir(root):
        return []
    orders = []
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if not os.path.isdir(p) or entry in ("购买记录", "三合一"):
            continue
        info = parse_order_dir(entry)
        files = []
        for f in sorted(os.listdir(p)):
            fp = os.path.join(p, f)
            if os.path.isfile(fp):
                files.append(fp)
        orders.append({
            "folder": entry,
            "seq": info[0] if info else None,
            "name": info[1] if info else entry,
            "amt": info[2] if info else None,
            "path": p,
            "files": files,
        })
    return orders


def split_files(orders_files: list):
    """把订单文件夹内文件分为 发票PDF / 支付图 / 购买图 / 其他"""
    inv = []
    pays = []
    buys = []
    others = []
    for f in orders_files:
        low = f.lower()
        if low.endswith(".pdf"):
            inv.append(f)
        elif low.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            cls = classify_image(os.path.basename(f))
            if cls == "pay":
                pays.append(f)
            elif cls == "buy":
                buys.append(f)
            else:
                others.append(f)
        else:
            others.append(f)
    # 无法分类的图片：按修改时间排序，靠前视为购买记录、靠后视为支付记录（可手动调整）
    if others:
        others.sort(key=lambda p: os.path.getmtime(p))
        mid = len(others) // 2 if len(others) > 1 else 1
        buys = buys + others[:mid]
        pays = pays + others[mid:]
    return inv, pays, buys


def has_three_in_one(order: dict, three_dir: str) -> bool:
    """检查该订单是否已有三合一 PDF（按金额判断）"""
    if not os.path.isdir(three_dir):
        return False
    amt = order.get("amt")
    if amt is None:
        return False
    amt_s = f"{amt:.2f}"
    return any(amt_s in f for f in os.listdir(three_dir))


def collect_buy_records(root: str, out_dir: str) -> int:
    """把每个订单里的购买记录截图复制到 out_dir，返回复制数量"""
    os.makedirs(out_dir, exist_ok=True)
    count = 0
    for order in list_order_folders(root):
        _, pays, buys = split_files(order["files"])
        for src in (buys + pays):   # 购买记录汇总，包含支付记录更完整
            dst = os.path.join(out_dir, os.path.basename(src))
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                count += 1
    return count
