# -*- coding: utf-8 -*-
"""
发票整理助手 v1.2
- 整理：扫描订单、汇总购买记录（三合一合并由用户脚本负责）
- 飞书：扫码授权登录（用户身份），查看购买申请 / 发票提交状态
- 对比：本地订单 ↔ 飞书单据，找出未申请购买 / 未提交发票
"""
import os
import json
import sys
import time
import traceback
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPushButton, QLineEdit, QLabel, QFileDialog, QTableWidget, QTableWidgetItem,
    QTextEdit, QMessageBox, QGridLayout, QGroupBox, QHeaderView, QSplitter,
)

import organize
import compare
import feishu_api
from feishu_api import FeishuApproval, FeishuUser

# exe（打包后）时配置文件放在 exe 旁边；源码运行时放在源码目录
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
AUTH_PORT = 8822


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"app_id": "", "app_secret": "", "user_id": "",
            "root_dir": "D:\\施宇豪\\桌面\\发票", "team": "22备赛飞机队",
            "user_token": "", "user_name": ""}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class WorkerSignals(QObject):
    done = Signal(object)      # (ok, message)
    log = Signal(str)


def run_thread(fn, signals: WorkerSignals):
    def target():
        try:
            result = fn()
            signals.done.emit((True, result))
        except Exception as e:
            signals.done.emit((False, f"{e}\n{traceback.format_exc()}"))
    threading.Thread(target=target, daemon=True).start()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("发票整理助手")
        self.resize(1280, 800)
        self.cfg = load_config()
        self.orders = []          # 本地订单
        self.buy_rows = []        # 飞书购买申请
        self.invoice_rows = []    # 飞书发票提交
        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        tabs = QTabWidget()
        tabs.addTab(self._build_overview_tab(), "总览")
        tabs.addTab(self._build_organize_tab(), "整理")
        tabs.addTab(self._build_feishu_tab(), "飞书")
        tabs.addTab(self._build_compare_tab(), "对比")
        self.setCentralWidget(tabs)
        # 署名：窗口右下角
        sb = self.statusBar()
        sig = QLabel("施宇豪之作")
        sig.setStyleSheet("color: #999; padding-right: 6px;")
        sb.addPermanentWidget(sig)

    # ---- 总览页 ----
    def _build_overview_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        b_refresh = QPushButton("刷新总览（需已扫描订单并拉取飞书）")
        b_refresh.clicked.connect(self._refresh_overview)
        top.addWidget(b_refresh)
        self.overview_stat = QLabel("")
        top.addWidget(self.overview_stat, 1)
        lay.addLayout(top)
        self.overview_table = self._make_table(
            ["状态", "订单", "金额", "购买申请", "申请概述", "发票"], [150, 340, 90, 110, 220, 110])
        lay.addWidget(self.overview_table, 1)
        tip = QLabel("说明：①「发票文件夹」列出本地订单；②「购买申请」对比飞书购买申请，缺的标 ❌；"
                     "③「发票」对比购买申请和本地文件夹，缺的标 ❌。先扫描订单、再扫码拉取飞书后刷新即可。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888;")
        lay.addWidget(tip)
        return w

    def _refresh_overview(self):
        # 自动扫描发票文件夹（无需先去整理页）
        root = self.root_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, "提示", "发票根目录无效，请先在「整理」页设置路径")
            return
        self._remember_root(root)
        self.orders = organize.list_order_folders(root)

        fu = self._feishu()
        if not fu.has_token:
            QMessageBox.warning(self, "提示", "尚未授权飞书，请先在「飞书」页扫码授权")
            return

        # 未拉取过飞书数据时自动拉取，拉完自动刷新总览
        if not self.buy_rows and not self.invoice_rows:
            self._log("总览：自动扫描完成，正在拉取飞书数据...")

            def job():
                buys = [FeishuApproval.summarize(x) for x in fu.list_buy()]
                invs = [FeishuApproval.summarize(x) for x in fu.list_invoice()]
                return buys, invs

            sig = WorkerSignals()
            sig.done.connect(self._on_overview_fetch_done)
            run_thread(job, sig)
        else:
            self._fill_overview()

    def _on_overview_fetch_done(self, payload):
        ok, res = payload
        if not ok:
            QMessageBox.critical(self, "拉取失败", str(res))
            return
        self.buy_rows, self.invoice_rows = res
        self._fill_overview()

    def _fill_overview(self):
        if not self.orders:
            QMessageBox.warning(self, "提示", "没有扫描到订单")
            return
        rows = compare.build_overview(self.orders, self.buy_rows, self.invoice_rows)
        data = []
        for r in rows:
            data.append([r["状态"], r["订单"], r["金额"], r["购买申请"], r["申请概述"], r["发票"]])
        self._fill_table(self.overview_table, data)
        # 着色：状态列
        for i, r in enumerate(rows):
            color = "#e74c3c" if r["状态"].startswith("❌") else "#2ecc71" if r["状态"].startswith("✅") else "#888"
            self.overview_table.item(i, 0).setForeground(Qt.GlobalColor.red if r["状态"].startswith("❌")
                                                          else Qt.GlobalColor.darkGreen if r["状态"].startswith("✅")
                                                          else Qt.GlobalColor.gray)
        n_ok = sum(1 for r in rows if r["状态"].startswith("✅"))
        n_need = sum(1 for r in rows if r["状态"].startswith("❌"))
        self.overview_stat.setText(f"共 {len(rows)} 单 | 完成 {n_ok} | 待处理 {n_need}")

    def _make_table(self, headers, widths=None):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        if widths:
            hh = t.horizontalHeader()
            for i, w in enumerate(widths):
                if w:
                    hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        return t

    def _fill_table(self, table, rows):
        table.setRowCount(0)
        for r in rows:
            row = table.rowCount()
            table.insertRow(row)
            for c, v in enumerate(r):
                table.setItem(row, c, QTableWidgetItem(str(v) if v is not None else ""))

    # ---- 整理页 ----
    def _build_organize_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        top = QHBoxLayout()
        top.addWidget(QLabel("发票根目录:"))
        self.root_edit = QLineEdit(self.cfg.get("root_dir", ""))
        self.root_edit.setMinimumWidth(420)
        top.addWidget(self.root_edit, 1)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self._pick_root)
        top.addWidget(btn_browse)
        btn_scan = QPushButton("扫描订单")
        btn_scan.clicked.connect(self._scan)
        top.addWidget(btn_scan)
        lay.addLayout(top)

        self.order_table = self._make_table(
            ["序号", "订单", "金额", "文件数", "发票PDF", "三合一"], [60, 400, 80, 80, 80, 80])
        lay.addWidget(self.order_table, 3)

        mid = QHBoxLayout()
        btn_collect = QPushButton("汇总购买记录")
        btn_collect.clicked.connect(self._collect)
        mid.addWidget(btn_collect)
        btn_refresh = QPushButton("刷新状态")
        btn_refresh.clicked.connect(self._scan)
        mid.addWidget(btn_refresh)
        lay.addLayout(mid)

        lay.addWidget(QLabel("日志:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(160)
        lay.addWidget(self.log_view)
        return w

    def _pick_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择发票根目录", self.root_edit.text())
        if d:
            self.root_edit.setText(d)
            self._remember_root(d)

    def _remember_root(self, root):
        """把发票根目录记到 config，下次启动自动带出"""
        if self.cfg.get("root_dir") != root:
            self.cfg["root_dir"] = root
            save_config(self.cfg)

    def _scan(self):
        root = self.root_edit.text().strip()
        if not root or not os.path.isdir(root):
            QMessageBox.warning(self, "提示", "发票根目录无效")
            return
        self._remember_root(root)
        self.orders = organize.list_order_folders(root)
        three_dir = os.path.join(root, "三合一")
        rows = []
        for o in self.orders:
            inv, _, _ = organize.split_files(o["files"])
            amt = o.get("amt")
            amt_s = f"{amt:.2f}" if amt is not None else ""
            has3 = "是" if organize.has_three_in_one(o, three_dir) else "否"
            rows.append([
                o.get("seq", ""), o["folder"], amt_s,
                len(o["files"]), len(inv), has3,
            ])
        self._fill_table(self.order_table, rows)
        self._log(f"扫描完成：共 {len(self.orders)} 个订单文件夹")

    def _collect(self):
        root = self.root_edit.text().strip()
        if not self.orders:
            self._log("请先扫描订单")
            return
        out = os.path.join(root, "购买记录")

        def job():
            return organize.collect_buy_records(root, out)

        sig = WorkerSignals()
        sig.done.connect(self._on_collect_done)
        run_thread(job, sig)

    def _on_collect_done(self, payload):
        ok, res = payload
        if not ok:
            self._log(f"汇总失败：{res}")
            return
        self._log(f"购买记录汇总完成：新增复制 {res} 个文件到 购买记录\\")

    def _log(self, msg):
        self.log_view.append(msg)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    # ---- 飞书页 ----
    def _build_feishu_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        box = QGroupBox("飞书开放平台配置")
        g = QGridLayout(box)
        g.addWidget(QLabel("App ID:"), 0, 0)
        self.app_id_edit = QLineEdit(self.cfg.get("app_id", ""))
        g.addWidget(self.app_id_edit, 0, 1)
        g.addWidget(QLabel("App Secret:"), 1, 0)
        self.app_secret_edit = QLineEdit(self.cfg.get("app_secret", ""))
        self.app_secret_edit.setEchoMode(QLineEdit.Password)
        g.addWidget(self.app_secret_edit, 1, 1)
        g.addWidget(QLabel("User ID (open_id):"), 2, 0)
        self.user_id_edit = QLineEdit(self.cfg.get("user_id", ""))
        g.addWidget(self.user_id_edit, 2, 1)
        tip = QLabel("使用方式：填好 App ID / App Secret 后点「扫码授权登录」，"
                     "用你的飞书账号扫码确认即完成（用户身份权限）。"
                     "User ID 仅在应用身份模式下需要，可留空。"
                     "首次使用前请在开放平台「安全设置 → 重定向 URL」添加 http://localhost:8822/callback")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888;")
        g.addWidget(tip, 3, 0, 1, 2)
        lay.addWidget(box)

        btns = QHBoxLayout()
        b_auth = QPushButton("扫码授权登录")
        b_auth.clicked.connect(self._authorize)
        btns.addWidget(b_auth)
        b_save = QPushButton("保存配置")
        b_save.clicked.connect(self._save_cfg)
        btns.addWidget(b_save)
        b_test = QPushButton("测试连接")
        b_test.clicked.connect(self._test_conn)
        btns.addWidget(b_test)
        b_fetch = QPushButton("拉取购买申请 & 发票提交")
        b_fetch.clicked.connect(self._fetch)
        btns.addWidget(b_fetch)
        lay.addLayout(btns)

        # 兜底：浏览器授权成功但回调没收到时，手动粘贴授权码
        manual = QHBoxLayout()
        manual.addWidget(QLabel("授权码(code)兜底:"))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("浏览器地址栏 ?code= 后面的内容（授权成功但软件没更新时用）")
        manual.addWidget(self.code_edit, 1)
        b_manual = QPushButton("手动完成授权")
        b_manual.clicked.connect(self._manual_auth)
        manual.addWidget(b_manual)
        lay.addLayout(manual)

        self.auth_status_label = QLabel("")
        if self.cfg.get("user_name"):
            self.auth_status_label.setText(f"已授权：{self.cfg['user_name']}")
        lay.addWidget(self.auth_status_label)

        lay.addWidget(QLabel("购买申请:"))
        self.buy_table = self._make_table(
            ["金额", "概述", "状态", "发起时间", "实例码"], [100, 240, 90, 120, 300])
        lay.addWidget(self.buy_table, 2)
        lay.addWidget(QLabel("发票提交:"))
        self.inv_table = self._make_table(
            ["金额", "状态", "发起时间", "实例码"], [100, 90, 120, 300])
        lay.addWidget(self.inv_table, 2)
        return w

    def _save_cfg(self):
        self.cfg.update({
            "app_id": self.app_id_edit.text().strip(),
            "app_secret": self.app_secret_edit.text().strip(),
            "user_id": self.user_id_edit.text().strip(),
            "root_dir": self.root_edit.text().strip(),
        })
        save_config(self.cfg)
        QMessageBox.information(self, "完成", "配置已保存")

    def _feishu(self):
        return FeishuUser(self.app_id_edit.text(), self.app_secret_edit.text(),
                          self.cfg.get("user_token", ""))

    # ---- 扫码授权 ----
    def _authorize(self):
        """扫码授权：本地回调自动完成；若回调收不到，可手动粘贴 code 兜底"""
        app_id = self.app_id_edit.text().strip()
        app_secret = self.app_secret_edit.text().strip()
        if not app_id or not app_secret:
            QMessageBox.warning(self, "提示", "请先填写 App ID 和 App Secret")
            return
        self._auth_done = False
        self.code_edit.clear()

        def job():
            code_box = {}
            got = threading.Event()

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query)
                    c = q.get("code", [""])[0]
                    if c:
                        code_box["code"] = c
                        got.set()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        "<html><body style='font-family:sans-serif;text-align:center;padding-top:80px'>"
                        "<h2>授权成功 ✅</h2><p>可以关闭此页面，回到软件继续操作。</p></body></html>"
                        .encode("utf-8"))

                def log_message(self, *a):
                    pass

            server = None
            try:
                server = HTTPServer(("127.0.0.1", AUTH_PORT), Handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            except OSError:
                # 端口被占用（如上一实例没关干净）：跳过自动回调，走手动
                return None
            webbrowser.open(feishu_api.build_auth_url(
                app_id, scope="approval:instance:read approval:instance:write"))
            try:
                got.wait(timeout=150)
                if self._auth_done or not code_box.get("code"):
                    return None  # 已手动完成 / 超时未收到回调
                return feishu_api.exchange_user_token(app_id, app_secret, code_box["code"])
            finally:
                if server:
                    server.shutdown()

        self.auth_status_label.setText("已打开授权页面，请扫码确认…")
        sig = WorkerSignals()
        sig.done.connect(self._on_auth_done)
        run_thread(job, sig)

    def _on_auth_done(self, payload):
        ok, res = payload
        if not ok:
            self.auth_status_label.setText("授权失败")
            QMessageBox.critical(self, "授权失败", str(res))
            return
        if res is None:
            # 没有自动收到回调：若已手动授权则静默，否则提示手动兜底
            if self.cfg.get("user_token"):
                self.auth_status_label.setText(f"已授权：{self.cfg.get('user_name', '')}")
            else:
                self.auth_status_label.setText(
                    "未自动收到回调：请复制浏览器地址栏 ?code= 后面的内容粘贴到上方输入框，点「手动完成授权」")
            return
        d = res
        self.cfg["user_token"] = d["access_token"]
        self.cfg["user_name"] = d.get("name", "")
        self._auth_done = True
        save_config(self.cfg)
        self.auth_status_label.setText(f"已授权：{d.get('name', '')}")
        QMessageBox.information(self, "成功", f"授权成功：{d.get('name', '')}")

    def _manual_auth(self):
        """浏览器授权成功但回调没收到时，手动粘贴授权码完成授权"""
        app_id = self.app_id_edit.text().strip()
        app_secret = self.app_secret_edit.text().strip()
        code = self.code_edit.text().strip()
        if not app_id or not app_secret:
            QMessageBox.warning(self, "提示", "请先填写 App ID 和 App Secret")
            return
        if not code:
            QMessageBox.warning(self, "提示",
                                "请先在浏览器授权页完成后，把地址栏 ?code= 后面的内容复制粘贴到这里")
            return
        try:
            d = feishu_api.exchange_user_token(app_id, app_secret, code)
            self.cfg["user_token"] = d["access_token"]
            self.cfg["user_name"] = d.get("name", "")
            self._auth_done = True
            save_config(self.cfg)
            self.auth_status_label.setText(f"已授权：{d.get('name', '')}")
            QMessageBox.information(self, "成功", f"授权成功：{d.get('name', '')}")
        except Exception as e:
            QMessageBox.critical(self, "授权失败", str(e))

    def _test_conn(self):
        try:
            fu = self._feishu()
            if not fu.has_token:
                QMessageBox.warning(self, "提示", "尚未授权登录，请先点「扫码授权登录」")
                return
            rows = fu.list_instances(max_pages=1)
            QMessageBox.information(self, "成功",
                                    f"连接成功，token 有效（可查询到 {len(rows)} 条审批实例）")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    def _fetch(self):
        fu = self._feishu()
        if not fu.has_token:
            QMessageBox.warning(self, "提示", "尚未授权登录，请先点「扫码授权登录」")
            return

        def job():
            buys = [FeishuApproval.summarize(x) for x in fu.list_buy()]
            invs = [FeishuApproval.summarize(x) for x in fu.list_invoice()]
            return buys, invs

        sig = WorkerSignals()
        sig.done.connect(self._on_fetch_done)
        run_thread(job, sig)

    def _on_fetch_done(self, payload):
        ok, res = payload
        if not ok:
            QMessageBox.critical(self, "拉取失败", str(res))
            return
        self.buy_rows, self.invoice_rows = res
        self._fill_table(self.buy_table, [
            [r["金额"], r["概述"], r["状态"], r["发起时间"], r["实例码"]] for r in self.buy_rows])
        self._fill_table(self.inv_table, [
            [r["金额"], r["状态"], r["发起时间"], r["实例码"]] for r in self.invoice_rows])
        QMessageBox.information(self, "完成",
                                f"拉取完成：购买申请 {len(self.buy_rows)} 条，发票提交 {len(self.invoice_rows)} 条")

    # ---- 对比页 ----
    def _build_compare_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        btns = QHBoxLayout()
        b_run = QPushButton("运行对比（需先扫描订单并拉取飞书）")
        b_run.clicked.connect(self._run_compare)
        btns.addWidget(b_run)
        b_export = QPushButton("导出报告")
        b_export.clicked.connect(self._export_report)
        btns.addWidget(b_export)
        lay.addLayout(btns)

        self.cmp_table = self._make_table(
            ["类别", "订单", "金额", "购买申请", "发票"], [160, 360, 90, 200, 120])
        lay.addWidget(self.cmp_table, 3)
        lay.addWidget(QLabel("报告:"))
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        lay.addWidget(self.report_view, 2)
        self._last_report = ""
        return w

    def _run_compare(self):
        if not self.orders:
            QMessageBox.warning(self, "提示", "请先在「整理」页扫描订单")
            return
        if not self.buy_rows and not self.invoice_rows:
            QMessageBox.warning(self, "提示", "请先在「飞书」页拉取数据")
            return
        result = compare.compare(self.orders, self.buy_rows, self.invoice_rows)
        rows = []
        for cat, items in [
            ("未申请购买", result["no_buy"]),
            ("已申请未提交发票", result["buy_ok_no_invoice"]),
            ("已闭环", result["closed"]),
            ("无法解析", result["unknown"]),
        ]:
            for it in items:
                rows.append([cat, it["订单"], it["金额"], it["购买申请"], it["发票"]])
        self._fill_table(self.cmp_table, rows)
        self._last_report = compare.build_report(result)
        self.report_view.setPlainText(self._last_report)

    def _export_report(self):
        if not self._last_report:
            QMessageBox.warning(self, "提示", "请先运行对比")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出报告", "发票对比报告.txt", "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._last_report)
            QMessageBox.information(self, "完成", f"已导出到 {path}")


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
