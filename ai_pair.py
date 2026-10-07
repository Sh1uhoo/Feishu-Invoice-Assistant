# -*- coding: utf-8 -*-
"""
AI 配对模块：调用 OpenAI 兼容的视觉模型接口识别散照片，
按金额把「购买记录 + 支付记录」配对，生成 0X-商品-金额元 文件夹。
支持任意 OpenAI 兼容 /chat/completions 的视觉模型（豆包、通义、GLM、DeepSeek 等）。
"""
import base64
import json
import os
import re
import shutil

import requests

PROMPT = (
    "你是发票整理助手。请识别这张图片的内容，只输出一个 JSON 对象（不要输出其他文字）：\n"
    '{"类别":"购买记录或支付记录或其他","金额":数字(单位元,无法识别填null),"商品":"商品或店铺名称(无法识别填空字符串)"}\n'
    "判断规则：商品订单/购物详情截图→购买记录；付款成功/支付成功/转账账单截图→支付记录。"
)


def _b64_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def call_vision(api_url: str, api_key: str, model: str, image_path: str,
                prompt: str = PROMPT, timeout: int = 60) -> str:
    """调用 OpenAI 兼容视觉接口，返回模型文本"""
    ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "jpeg"
    mime = "png" if ext == "png" else "jpeg"
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/{mime};base64,{_b64_image(image_path)}"}},
            ],
        }],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(api_url, headers=headers, json=body, timeout=timeout)
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"AI 接口错误: {data['error']}")
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"AI 返回格式异常: {str(data)[:300]}")


def identify_image(api_url: str, api_key: str, model: str, image_path: str) -> dict:
    """识别单张图片，返回 {类别, 金额, 商品}"""
    text = call_vision(api_url, api_key, model, image_path)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"类别": "其他", "金额": None, "商品": ""}
    try:
        obj = json.loads(m.group())
    except Exception:
        return {"类别": "其他", "金额": None, "商品": ""}
    cat = str(obj.get("类别", "")).strip()
    if "购买" in cat:
        cat = "购买记录"
    elif "支付" in cat or "付款" in cat:
        cat = "支付记录"
    else:
        cat = "其他"
    amt = obj.get("金额")
    if isinstance(amt, (int, float)):
        amt = round(float(amt), 2)
    else:
        try:
            amt = round(float(str(amt).replace("元", "").strip()), 2)
        except Exception:
            amt = None
    return {"类别": cat, "金额": amt, "商品": str(obj.get("商品", "") or "").strip()}


def _fmt_amt(amt):
    return f"{amt:g}"


def pair_images(results: list) -> tuple:
    """results: [{path, 类别, 金额, 商品}] → (pairs, singles)
    pairs: [{商品, 金额, 购买路径, 支付路径}]
    singles: [{path, 类别, 金额, 商品, 原因}]
    """
    buys = [r for r in results if r["类别"] == "购买记录" and r["金额"] is not None]
    pays = [r for r in results if r["类别"] == "支付记录" and r["金额"] is not None]
    used_b, used_p = set(), set()
    pairs = []
    for b in buys:
        for i, p in enumerate(pays):
            if i in used_p:
                continue
            if abs(b["金额"] - p["金额"]) < 0.01:
                used_b.add(id(b))
                used_p.add(i)
                pairs.append({
                    "商品": b["商品"] or p["商品"] or _fmt_amt(b["金额"]),
                    "金额": b["金额"],
                    "购买路径": b["path"],
                    "支付路径": p["path"],
                })
                break
    singles = []
    for r in results:
        if id(r) in used_b or (r in pays and pays.index(r) in used_p):
            continue
        reason = "金额无法识别" if r["金额"] is None else "无匹配的另一半"
        if r["类别"] == "其他":
            reason = "无法识别为购买/支付记录"
        singles.append({**r, "原因": reason})
    return pairs, singles


def create_order_folders(pairs: list, root: str, seq_start: int = 1) -> list:
    """把配对结果生成 0X-商品-金额元 文件夹并复制两张图进去，返回 (成功列表, 失败列表)"""
    ok, fail = [], []
    seq = seq_start
    for p in pairs:
        name = f"{seq:02d}-{p['商品']}-{_fmt_amt(p['金额'])}元"
        folder = os.path.join(root, name)
        try:
            os.makedirs(folder, exist_ok=True)
            copied = []
            for src in (p["购买路径"], p["支付路径"]):
                if src and os.path.exists(src):
                    dst = os.path.join(folder, os.path.basename(src))
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                    copied.append(dst)
            ok.append((name, copied))
        except Exception as e:
            fail.append((name, str(e)))
        seq += 1
    return ok, fail
