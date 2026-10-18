r"""chrome-mcp 直连客户端：绕过 Harness 配置，直接与本机 ChromeMcpServer 通信。

前置：Chrome 扩展 mcp-chrome 已加载且点击过 Connect（本地端点 127.0.0.1:12306）。

用法：
  python chrome_mcp.py tools                 # 列出所有可用工具
  python chrome_mcp.py call <tool_name>      # 调用工具（无参数）
  python chrome_mcp.py call <tool_name> --args '{"url":"https://x.com"}'
仅标准库；localhost 直连，不走系统代理。
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "http://127.0.0.1:12306/mcp"
PROTOCOL = "2025-03-26"
SESSION_FILE = Path(__file__).resolve().parents[1] / "logs" / "chrome_mcp_session.json"


class McpClient:
    """极简 MCP Streamable HTTP 客户端（initialize → tools/list → tools/call）。"""

    def __init__(self) -> None:
        self.session: str | None = self._load_session()
        self._id_counter = 0
        # localhost 必须直连，绝不能被系统代理劫持
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # 服务端是单会话设计：会话 ID 存盘复用；失效时自动重新握手
    def _load_session(self) -> str | None:
        if SESSION_FILE.exists():
            try:
                return json.loads(SESSION_FILE.read_text(encoding="utf-8")).get("id")
            except Exception:  # noqa: BLE001
                return None
        return None

    def _save_session(self) -> None:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"id": self.session}), encoding="utf-8")

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _post(self, payload: dict) -> dict | None:
        try:
            return self._post_raw(payload)
        except urllib.error.HTTPError as e:
            # 会话失效（404/400）→ 重新握手重试一次
            if e.code in (400, 404) and payload.get("method") != "initialize":
                self.session = None
                self.initialize()
                return self._post_raw(payload)
            raise

    def _post_raw(self, payload: dict) -> dict | None:
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream",
                     **({"Mcp-Session-Id": self.session} if self.session else {})})
        with self._opener.open(req, timeout=120) as resp:
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self.session = sid
            body = resp.read().decode("utf-8", errors="replace")
        for line in body.splitlines():  # SSE 格式：data: {...}
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(body) if body.strip() else None

    def initialize(self) -> dict:
        result = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "fakenews-agent", "version": "0.1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result or {}

    def list_tools(self) -> list[dict]:
        r = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list"})
        return (r or {}).get("result", {}).get("tools", [])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        r = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}}})
        return r or {}

    def ensure(self) -> dict | None:
        """复用存盘会话；失效则重新握手并保存。返回 serverInfo（复用时为 None）。"""
        if self.session:
            try:
                self.list_tools()
                return None
            except Exception:  # noqa: BLE001
                self.session = None
        info = self.initialize()
        self._save_session()
        return info.get("result", {}).get("serverInfo", {})


def main() -> None:
    ap = argparse.ArgumentParser(description="chrome-mcp 直连客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="列出可用工具")
    p_call = sub.add_parser("call", help="调用工具")
    p_call.add_argument("tool")
    p_call.add_argument("--args", default="{}", help="JSON 参数")
    a = ap.parse_args()

    cli = McpClient()
    server = cli.ensure()
    if server:
        print(f"# 已连接 {server.get('name')} v{server.get('version')}")
    else:
        print("# 复用已有会话")

    if a.cmd == "tools":
        for t in cli.list_tools():
            desc = (t.get("description") or "").split("\n")[0][:70]
            print(f"  {t['name']:<38} {desc}")
    else:
        result = cli.call(a.tool, json.loads(a.args))
        if (result or {}).get("error"):
            print(json.dumps(result["error"], ensure_ascii=False, indent=2))
            raise SystemExit(1)
        content = result.get("result", {}).get("content", [])
        for block in content:
            if block.get("type") == "text":
                print(block.get("text", ""))
        if not content:
            print(json.dumps(result, ensure_ascii=False)[:500])


if __name__ == "__main__":
    main()
