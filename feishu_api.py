# -*- coding: utf-8 -*-
"""
飞书开放平台 API 模块
- 获取 tenant_access_token
- 查询当前用户发起的审批实例（购买申请 / 发票提交）
"""
import requests

BASE = "https://open.feishu.cn/open-apis"

# 审批定义 Code（22届智能车备赛组织内）
APPROVAL_BUY = "375BDE1F-4744-4A62-9F0D-26A2A6F8EBC3"       # 购买申请
APPROVAL_INVOICE = "116F4D1A-A5F5-4A75-8E59-10342B46347A"   # 发票提交

STATUS_TEXT = {1: "审批中", 2: "已通过", 3: "已驳回", 4: "已撤回", 5: "进行中"}


class FeishuError(Exception):
    pass


class FeishuApproval:
    """飞书审批查询器（租户自建应用模式）"""

    def __init__(self, app_id: str, app_secret: str, user_id: str = ""):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.user_id = (user_id or "").strip()   # open_id
        self.token = None

    # ---------- token ----------
    def get_token(self) -> str:
        if not self.app_id or not self.app_secret:
            raise FeishuError("请先填写 App ID 和 App Secret")
        resp = requests.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"获取 token 失败: {data.get('msg', data)}")
        self.token = data["tenant_access_token"]
        return self.token

    def _headers(self):
        if not self.token:
            self.get_token()
        return {"Authorization": f"Bearer {self.token}"}

    # ---------- 实例查询 ----------
    def list_instances(self, approval_code: str = "", max_pages: int = 3) -> list:
        """查询当前用户（user_id）发起的审批实例，返回实例 dict 列表"""
        if not self.user_id:
            raise FeishuError("请先填写你的 User ID（open_id）")
        url = f"{BASE}/approval/v4/instances"
        params = {"user_id": self.user_id, "user_id_type": "open_id", "page_size": 100}
        if approval_code:
            params["approval_code"] = approval_code
        results = []
        page_token = ""
        for _ in range(max_pages):
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=20)
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(f"查询审批实例失败: {data.get('msg', data)}")
            items = data.get("data", {}).get("instance_list", [])
            results.extend(items)
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        return results

    def list_buy(self) -> list:
        return self.list_instances(APPROVAL_BUY)

    def list_invoice(self) -> list:
        return self.list_instances(APPROVAL_INVOICE)

    # ---------- 解析摘要 ----------
    @staticmethod
    def summarize(instance: dict) -> dict:
        """从实例 dict 提取 金额/概述/状态 等关键信息"""
        defs = instance.get("definition_code", "")
        name = "购买申请" if defs == APPROVAL_BUY else "发票提交" if defs == APPROVAL_INVOICE else defs
        status = STATUS_TEXT.get(instance.get("instance_status"), str(instance.get("instance_status")))
        out = {
            "定义": name,
            "金额": "",
            "概述": "",
            "状态": status,
            "实例码": instance.get("instance_code", ""),
            "发起时间": "",
        }
        for s in instance.get("summaries", []) or []:
            key = s.get("key", "")
            val = s.get("value", "")
            if "金额" in key or "发票" in key and "对" in key:
                if not out["金额"]:
                    out["金额"] = val
            elif "概述" in key or "内容" in key:
                out["概述"] = val
        t = instance.get("start_time") or ""
        if t:
            import datetime
            try:
                out["发起时间"] = datetime.datetime.fromtimestamp(int(t) / 1000).strftime("%m-%d %H:%M")
            except Exception:
                out["发起时间"] = ""
        return out


# ================= 用户身份（OAuth 扫码授权）模式 =================
# 应用身份权限审核通不过时，使用已开通的用户身份权限：
#   approval:instance:read（获取审批实例详情）
#   approval:instance:write（操作审批实例：提交/撤回）
# 调用方式：软件内扫码授权 → 拿到 user_access_token → 以此调用 API

import os
import json
import urllib.parse

DEFAULT_REDIRECT = "http://localhost:8822/callback"


def build_auth_url(app_id: str, redirect_uri: str = DEFAULT_REDIRECT, state: str = "",
                   scope: str = "") -> str:
    """构造扫码授权链接；scope 多个用空格分隔（如 'approval:instance:read approval:instance:write'）"""
    url = (f"{BASE}/authen/v1/index?app_id={app_id}"
           f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
           f"&state={urllib.parse.quote(state, safe='')}")
    if scope:
        url += f"&scope={urllib.parse.quote(scope, safe='')}"
    return url


def exchange_user_token(app_id: str, app_secret: str, code: str,
                        redirect_uri: str = DEFAULT_REDIRECT) -> dict:
    """用授权码换 user_access_token，返回 {access_token, refresh_token, open_id, name, expire}"""
    resp = requests.post(
        f"{BASE}/authen/v1/access_token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "app_id": app_id,
            "app_secret": app_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("msg", "")
        if code == 20014:
            raise FeishuError(
                "App 凭证无效（20014）。可能原因：\n"
                "1) App Secret 没有复制完整（请到开放平台「凭证与基础信息」点显示后完整复制）；\n"
                "2) 授权码已被使用或过期（每个授权码只能用一次，请重新点「扫码授权登录」拿新码）")
        raise FeishuError(f"换取用户凭证失败: {data}")
    return data["data"]


class FeishuUser:
    """用户身份（user_access_token）模式：查看 + 提交审批"""

    def __init__(self, app_id: str, app_secret: str, token: str = ""):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.token = (token or "").strip()

    @property
    def has_token(self) -> bool:
        return bool(self.token)

    def _headers(self):
        if not self.token:
            raise FeishuError("尚未授权登录，请先扫码授权")
        return {"Authorization": f"Bearer {self.token}"}

    # ---- 查询（用户身份：我发起的审批实例列表）----
    def list_instances(self, approval_code: str = "", max_pages: int = 5) -> list:
        url = f"{BASE}/approval/v4/instances/initiated"
        params = {"page_size": 100}
        if approval_code:
            # 注意：initiated 接口只认 definition_code，传 approval_code 会被忽略
            # （会返回"我发起的全部实例"，导致购买申请/发票提交串数据）
            params["definition_code"] = approval_code
        results = []
        page_token = ""
        for _ in range(max_pages):
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=self._headers(), params=params, timeout=20)
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(f"查询审批实例失败: {data.get('msg', data)}")
            items = data.get("data", {}).get("instances", [])
            results.extend(items)
            page_token = data.get("data", {}).get("page_token", "")
            if not page_token:
                break
        return results

    def list_buy(self) -> list:
        return self.list_instances(APPROVAL_BUY)

    def list_invoice(self) -> list:
        return self.list_instances(APPROVAL_INVOICE)

    # ---- 提交 ----
    def _tenant_token(self) -> str:
        """获取应用身份（tenant_access_token），审批文件上传必须用应用身份"""
        if not self.app_id or not self.app_secret:
            raise FeishuError("请先填写 App ID 和 App Secret")
        resp = requests.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"获取应用凭证失败: {data.get('msg', data)}")
        return data["tenant_access_token"]

    def upload_file(self, file_path: str, file_type: str = "attachment") -> str:
        """上传审批文件（应用身份调用审批上传接口），返回 file_code（供附件/图片控件使用）

        注意：审批附件控件只认「审批系统文件 code」，必须走本接口；
        drive 云盘上传返回的 file_token 不认（实测 1395006）。
        需要应用身份权限：访问审批应用（approval:approval:readonly / approval:approval）
        """
        name = os.path.basename(file_path)
        tt = self._tenant_token()
        with open(file_path, "rb") as f:
            resp = requests.post(
                "https://www.feishu.cn/approval/openapi/v2/file/upload",
                headers={"Authorization": f"Bearer {tt}"},
                data={"name": name, "type": file_type},
                files={"content": (name, f, "application/octet-stream")},
                timeout=90,
            )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"上传审批文件失败: {data.get('msg', data)}")
        return data["data"]["code"]

    def create_instance(self, approval_code: str, form: list,
                        node_approver_list: list | None = None,
                        node_cc_list: list | None = None) -> str:
        """创建审批实例（用户身份，POST /approval/v4/instances/initiate），返回 instance_code"""
        body = {"approval_code": approval_code, "form": json.dumps(form, ensure_ascii=False)}
        if node_approver_list:
            body["node_approver_list"] = node_approver_list
        if node_cc_list:
            body["node_cc_list"] = node_cc_list
        resp = requests.post(f"{BASE}/approval/v4/instances/initiate",
                             headers=self._headers(), json=body, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"提交审批失败: {data.get('msg', data)}")
        return data["data"]["instance_code"]

    def recall_instance(self, instance_code: str) -> None:
        """撤回审批实例（用户身份，POST /approval/v4/instances/recall）"""
        resp = requests.post(f"{BASE}/approval/v4/instances/recall",
                             headers=self._headers(),
                             json={"instance_code": instance_code}, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"撤回审批失败: {data.get('msg', data)}")
