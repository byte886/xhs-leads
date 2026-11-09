#!/usr/bin/env python3
"""壳窗（左窗）启动过程与页面 URL 监测试验。

独立进程：只读 CDP 9223，不新建/不关闭/不导航任何页面，因此不会干扰被测应用。

测什么：
  - 每个 page target 归属哪个 window（Browser.getWindowForTarget），窗口的屏幕 bounds
  - 壳窗判定：内含 APP 页（默认 127.0.0.1:8791）的窗口；无 APP 页时退化为 left 最小的窗口
  - 壳窗内页面 URL 的时间线：谁在什么时候出现/消失
  - 违规判定：壳窗内出现非 APP 域名的页面（例如小红书站点页）即记 violation

为何要独立进程：
  应用自身的日志只记它认为自己做了什么；从外部按 windowId 归属逐帧采样，才能证明
  「左窗从启动到运行始终只有 APP 页」，而不是靠在应用内部打点自证。

用法：
  backend/.venv/bin/python scripts/dev/shell_window_probe.py --duration 120 --out /tmp/shell_probe.jsonl

退出码：0 = 无 violation；1 = 壳窗出现过非 APP 域名页面；2 = 一次都没采到 CDP。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import OrderedDict

from websocket import create_connection

APP_URL_MARK = "127.0.0.1:8791"


def _http_json(url: str, timeout: float = 3.0) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


class Cdp:
    """极简 CDP browser 连接：断线自动重连，只发 Browser.* 查询。"""

    def __init__(self, port: int):
        self.port = port
        self.ws = None
        self._mid = 0

    def _connect(self) -> bool:
        try:
            ver = _http_json(f"http://127.0.0.1:{self.port}/json/version")
            self.ws = create_connection(ver["webSocketDebuggerUrl"], timeout=10)
            self.ws.settimeout(10)
            self._mid = 0
            return True
        except (urllib.error.URLError, OSError, KeyError, ValueError, Exception):
            self.ws = None
            return False

    def call(self, method: str, params: dict | None = None) -> dict:
        for _attempt in range(2):
            if self.ws is None and not self._connect():
                raise ConnectionError("cdp unavailable")
            self._mid += 1
            mid = self._mid
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            deadline = time.time() + 10
            try:
                while time.time() < deadline:
                    msg = json.loads(self.ws.recv())
                    if msg.get("id") == mid:
                        return msg.get("result", {})
            except Exception:  # noqa: BLE001（socket 断开/超时 → 重连再试一次）
                self.ws = None
                continue
        raise ConnectionError("cdp call timeout")


class Probe:
    BLANK_URLS = ("about:blank", "", "chrome://newtab/")

    def __init__(self, port: int, app_mark: str):
        self.cdp = Cdp(port)
        self.app_mark = app_mark
        self.url_stat: OrderedDict[str, dict] = OrderedDict()
        self.violations: list[dict] = []
        self.samples = 0
        self.shell_seen = 0
        self.blank_samples = 0
        self.max_shell_pages = 0
        self.last_key = None
        self.window_ids_seen: set[int] = set()

    def _pages(self) -> list[dict]:
        ts = _http_json(f"http://127.0.0.1:{self.cdp.port}/json/list")
        return [t for t in ts if t.get("type") == "page"]

    def _snapshot(self) -> dict[int, dict]:
        """windowId → {bounds, pages:[{url,title,id}]}"""
        wins: dict[int, dict] = {}
        for p in self._pages():
            try:
                wid = self.cdp.call(
                    "Browser.getWindowForTarget", {"targetId": p["id"]}
                ).get("windowId")
                bounds = self.cdp.call("Browser.getWindowBounds", {"windowId": wid}).get(
                    "bounds", {}
                )
            except ConnectionError:
                continue
            w = wins.setdefault(wid, {"bounds": bounds, "pages": []})
            w["pages"].append(
                {"id": p["id"], "url": p.get("url") or "", "title": p.get("title") or ""}
            )
        return wins

    def _pick_shell(self, wins: dict[int, dict]) -> tuple[int | None, str]:
        hit = [
            wid for wid, w in wins.items() if any(self.app_mark in p["url"] for p in w["pages"])
        ]
        if hit:
            best = min(hit, key=lambda wid: wins[wid]["bounds"].get("left", 0))
            return best, "has_app_page"
        if wins:
            best = min(wins, key=lambda wid: wins[wid]["bounds"].get("left", 0))
            return best, "leftmost_fallback"
        return None, "none"

    def tick(self, out) -> None:
        try:
            wins = self._snapshot()
        except (ConnectionError, urllib.error.URLError, OSError):
            self._emit(out, {"kind": "cdp_down"}, force=True)
            return
        self.samples += 1
        shell_wid, why = self._pick_shell(wins)
        now = round(time.time(), 3)

        # 页面 URL 台账（全量，用于回答「打开过哪些页面」）
        for wid, w in wins.items():
            for p in w["pages"]:
                u = p["url"]
                rec = self.url_stat.setdefault(
                    u, {"first": now, "last": now, "n": 0, "windows": set()}
                )
                rec["last"] = now
                rec["n"] += 1
                rec["windows"].add(wid)

        if shell_wid is not None:
            self.shell_seen += 1
            self.window_ids_seen.add(shell_wid)
            shell_urls = [p["url"] for p in wins[shell_wid]["pages"]]
            self.max_shell_pages = max(self.max_shell_pages, len(shell_urls))
            # 启动瞬间壳窗先是 about:blank（窗口已建、APP 页未加载）——正常状态，不算违规，
            # 单列 blank_samples 记录；只有出现真正的站外页面才是 violation。
            if all(u in self.BLANK_URLS for u in shell_urls):
                self.blank_samples += 1
            bad = [
                u for u in shell_urls if self.app_mark not in u and u not in self.BLANK_URLS
            ]
            if bad:
                self.violations.append(
                    {"t": now, "windowId": shell_wid, "urls": bad, "why": why}
                )

        key = (
            shell_wid,
            tuple(sorted(p["url"] for p in wins[shell_wid]["pages"])) if shell_wid else None,
            tuple(sorted(wins.keys())),
            # 窗口 bounds 也算变化：验证「采集窗有没有先在左半屏出现再被挪走」
            # 靠的就是 coordinates 的变化，只看 URL 集会漏掉整个移动过程。
            tuple(sorted((wid, self._bounds_key(w["bounds"])) for wid, w in wins.items())),
        )
        changed = key != self.last_key
        self.last_key = key

        if changed:
            detail = {
                "kind": "change",
                "t": now,
                "shell": None
                if shell_wid is None
                else {
                    "windowId": shell_wid,
                    "why": why,
                    "bounds": wins[shell_wid]["bounds"],
                    "pages": wins[shell_wid]["pages"],
                },
                "other_windows": {
                    str(wid): {
                        "bounds": w["bounds"],
                        "pages": [p["url"] for p in w["pages"]],
                    }
                    for wid, w in wins.items()
                    if wid != shell_wid
                },
            }
            self._emit(out, detail, force=True)

    @staticmethod
    def _bounds_key(b: dict) -> tuple:
        """窗口坐标/尺寸的可比较签名（windowState 单列，避免 normal↔maximized 噪声）。"""
        if not isinstance(b, dict):
            return ()
        return (b.get("left"), b.get("top"), b.get("width"), b.get("height"),
                b.get("windowState"))

    def _emit(self, out, obj: dict, force: bool = False) -> None:
        obj.setdefault("t", round(time.time(), 3))
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    def report(self) -> dict:
        urls = []
        for u, rec in self.url_stat.items():
            urls.append(
                {
                    "url": u,
                    "first": rec["first"],
                    "last": rec["last"],
                    "samples": rec["n"],
                    "windows": sorted(rec["windows"]),
                }
            )
        bad_urls = [
            u["url"]
            for u in urls
            if self.app_mark not in u["url"] and u["url"] not in self.BLANK_URLS
        ]
        return {
            "samples": self.samples,
            "shell_seen": self.shell_seen,
            "blank_samples": self.blank_samples,
            "max_shell_pages": self.max_shell_pages,
            "shell_window_ids": sorted(self.window_ids_seen),
            "seen_urls": urls,
            "non_app_urls_any_window": bad_urls,
            "violations": self.violations[:20],
            "violation_count": len(self.violations),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--duration", type=float, default=120, help="0 = 跑到收到 SIGINT")
    ap.add_argument("--app-mark", default=APP_URL_MARK)
    ap.add_argument("--out", default="/tmp/shell_probe.jsonl")
    args = ap.parse_args()

    probe = Probe(args.port, args.app_mark)
    started = time.time()
    with open(args.out, "w", encoding="utf-8") as out:
        probe._emit(out, {"kind": "start", "interval": args.interval}, force=True)
        while True:
            if args.duration and time.time() - started >= args.duration:
                break
            probe.tick(out)
            time.sleep(args.interval)
        rep = probe.report()
        probe._emit(out, {"kind": "report", **rep}, force=True)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if probe.samples == 0:
        return 2
    return 1 if probe.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
