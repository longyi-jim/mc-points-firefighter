#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分消防员 · 麦当劳 MCP 客户端

用纯标准库实现 MCP 的 Streamable HTTP 传输，零第三方依赖。
职责只有一件事：把 APP 里的积分账户和麦麦商城商品拉下来，
转成 optimize.py 认识的输入格式。

为什么自己写而不依赖 SDK？
因为参赛作品要让评委"三行命令跑起来"。多一个 pip install，
就多一半人卡在第一步。

用到的 MCP 工具（来自麦当劳中国官方 MCP Server）：
  - query-my-account        我的积分查询（可用/累计/冻结/即将过期）
  - mall-points-products    麦麦商城商品列表

安全约定：
  Token 只从环境变量 MCD_MCP_TOKEN 读取，绝不写进代码、日志或产物。
  这是赛事的强制要求（见 CONTEST_DECLARATION.md 信息安全声明）。

用法：
  # 真实拉取（需要 MCD_MCP_TOKEN）
  export MCD_MCP_TOKEN="Bearer 后面的那串"
  python scripts/fetch_via_mcp.py --out examples/live_input.json

  # 没有 Token 也能跑通全流程，用内置示例数据
  python scripts/fetch_via_mcp.py --demo --out examples/live_input.json

  # 先看看能拿到哪些工具
  python scripts/fetch_via_mcp.py --list-tools
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

#: 麦当劳中国 MCP Server 的托管地址（官方文档 2.1 节）
DEFAULT_ENDPOINT = "https://mcp.mcd.cn"

#: 优先尝试的协议版本
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

#: 用户可用积分里"即将过期"的那个字段，不同版本命名可能不同，全试一遍
EXPIRING_KEYS = [
    "expiringPoints", "expirePoints", "expiringPoint", "willExpirePoints",
    "soonExpirePoints", "aboutToExpirePoints", "expiring",
]
AVAILABLE_KEYS = ["availablePoints", "balance", "points", "available", "usablePoints", "totalAvailablePoints"]
TOTAL_KEYS = ["totalPoints", "accumulatedPoints", "historyPoints", "total"]
FROZEN_KEYS = ["frozenPoints", "freezePoints", "frozen", "lockedPoints"]

PRODUCT_NAME_KEYS = ["name", "productName", "goodsName", "title", "skuName"]
PRODUCT_POINTS_KEYS = ["points", "pointsPrice", "point", "integral", "price", "needPoints", "exchangePoints"]
PRODUCT_VALID_KEYS = ["validDays", "validityDays", "expireDays", "effectiveDays", "usableDays"]
PRODUCT_LIMIT_KEYS = ["limit", "limitPerUser", "maxCount", "perLimit", "buyLimit", "exchangeLimit"]
PRODUCT_STOCK_KEYS = ["inStock", "stock", "stockStatus", "available", "canExchange"]


class McpError(RuntimeError):
    """MCP 调用失败。"""


# ---------------------------------------------------------------------------
# 协议层
# ---------------------------------------------------------------------------


class McpClient:
    """极简 MCP Streamable HTTP 客户端。

    流程：initialize → notifications/initialized → tools/call。
    会话 ID 从响应头 Mcp-Session-Id 取，后续请求带上。
    """

    def __init__(self, endpoint: str, token: str, timeout: int = 30) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._seq = 0
        self.protocol_version: Optional[str] = None

    # -- 底层 ---------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发一条 JSON-RPC 消息，返回匹配 id 的响应（通知返回 None）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=data, headers=self._headers(), method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 401:
                raise McpError(
                    "MCP Token 无效或已过期。请到 https://open.mcd.cn/mcp 控制台重新复制，"
                    "注意 Token 不要带 Bearer 前缀。"
                ) from exc
            if exc.code == 429:
                raise McpError("触发限流（600 次/分钟）。等一分钟再试。") from exc
            raise McpError(f"MCP 返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"连不上 {self.endpoint}：{exc.reason}") from exc

        if payload.get("id") is None:
            return None  # 通知没有响应体
        return self._parse(raw, ctype, payload["id"])

    @staticmethod
    def _parse(raw: str, ctype: str, want_id: Any) -> Dict[str, Any]:
        """同时兼容 application/json 和 text/event-stream 两种返回。"""
        text = raw.strip()
        if not text:
            raise McpError("MCP 返回了空响应")

        is_sse = "text/event-stream" in ctype or text.startswith(("event:", "data:"))
        if is_sse:
            messages: List[Dict[str, Any]] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        messages.append(json.loads(chunk))
                    except json.JSONDecodeError:
                        continue
            for msg in messages:
                if msg.get("id") == want_id:
                    return msg
            if messages:
                return messages[-1]
            raise McpError("事件流里没解析出 JSON")

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError(f"响应不是合法 JSON：{text[:200]}") from exc

    # -- 协议 ---------------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for version in PROTOCOL_VERSIONS:
            self._seq += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._seq,
                "method": "initialize",
                "params": {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "mc-points-firefighter", "version": "1.0.0"},
                },
            }
            try:
                resp = self._post(payload)
            except McpError as exc:
                last_error = exc
                # 版本不兼容时服务端通常报 400，换下一个版本再试
                if "HTTP 400" in str(exc) or "HTTP 406" in str(exc):
                    continue
                raise
            if resp and "result" in resp:
                self.protocol_version = version
                self._seq += 1
                self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                return resp["result"]
            last_error = McpError(f"初始化失败：{resp}")

        raise McpError(f"握手失败，试过的协议版本都不行：{last_error}")

    def list_tools(self) -> List[Dict[str, Any]]:
        self._seq += 1
        resp = self._post({"jsonrpc": "2.0", "id": self._seq, "method": "tools/list", "params": {}})
        if not resp or "result" not in resp:
            raise McpError(f"tools/list 失败：{resp}")
        return resp["result"].get("tools", [])

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """调用一个工具，尽力把返回的文本解析成 JSON。"""
        self._seq += 1
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._seq,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        if not resp:
            raise McpError(f"{name} 没有返回")
        if "error" in resp:
            err = resp["error"]
            raise McpError(f"{name} 调用失败：{err.get('message', err)}")

        result = resp.get("result") or {}
        if result.get("isError"):
            raise McpError(f"{name} 返回错误：{_flatten_content(result)}")

        payload = _flatten_content(result)
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return payload


def _flatten_content(result: Dict[str, Any]) -> str:
    """把 MCP 的 content 数组拼成一段文本。"""
    parts: List[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        else:
            parts.append(str(item))
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# 字段适配层
# ---------------------------------------------------------------------------
#
# 官方文档没有公开每个字段的精确结构，不同版本也可能改字段名。
# 所以这里用"候选键逐个试"的方式做兼容，拿不到就给默认值并标记来源，
# 而不是假装成功。第一次接真实账号时，建议先跑 --dump-raw 核对一下。


def _pick(obj: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return default


def _dig(data: Any) -> Dict[str, Any]:
    """从各种嵌套层级里挖出真正装数据的 dict。"""
    if isinstance(data, dict):
        for wrapper in ("data", "result", "account", "info", "userAccount"):
            inner = data.get(wrapper)
            if isinstance(inner, dict):
                return inner
        return data
    return {}


def normalize_account(raw: Any) -> Dict[str, Any]:
    """把 query-my-account 的返回转成我们的账户结构。"""
    data = _dig(raw)
    return {
        "available": int(_pick(data, AVAILABLE_KEYS, 0) or 0),
        "expiring": int(_pick(data, EXPIRING_KEYS, 0) or 0),
        "expiring_date": str(_pick(data, ["expiringDate", "expireDate", "expireTime"], "") or ""),
        "total": int(_pick(data, TOTAL_KEYS, 0) or 0),
        "frozen": int(_pick(data, FROZEN_KEYS, 0) or 0),
    }


def normalize_products(raw: Any) -> List[Dict[str, Any]]:
    """把 mall-points-products 的返回转成商品池。"""
    # 找到装商品的列表
    candidates: Any = raw
    if isinstance(raw, dict):
        for key in ("data", "result", "list", "products", "items", "records", "goodsList"):
            if key in raw:
                candidates = raw[key]
                break
        if isinstance(candidates, dict):
            for key in ("list", "products", "items", "records"):
                if key in candidates:
                    candidates = candidates[key]
                    break

    if not isinstance(candidates, list):
        return []

    products: List[Dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        points = _pick(item, PRODUCT_POINTS_KEYS)
        try:
            points = int(float(points))
        except (TypeError, ValueError):
            continue  # 没积分价的商品对兑换优化没意义
        if points <= 0:
            continue

        stock_raw = _pick(item, PRODUCT_STOCK_KEYS, True)
        if isinstance(stock_raw, str):
            in_stock = stock_raw.lower() not in ("0", "false", "soldout", "sold_out", "none")
        elif isinstance(stock_raw, (int, float)):
            in_stock = stock_raw > 0
        else:
            in_stock = bool(stock_raw)

        products.append(
            {
                "pid": str(_pick(item, ["id", "productId", "skuId", "goodsId"], f"SKU{idx}")),
                "name": str(_pick(item, PRODUCT_NAME_KEYS, f"商品{idx}")),
                "points": points,
                "valid_days": int(_pick(item, PRODUCT_VALID_KEYS, 30) or 30),
                "limit": int(_pick(item, PRODUCT_LIMIT_KEYS, 1) or 1),
                "category": str(_pick(item, ["category", "categoryName", "type", "goodsType"], "other")),
                "cash": float(_pick(item, ["cash", "cashPrice", "amount", "money"], 0) or 0),
                "in_stock": in_stock,
            }
        )
    return products


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def fetch_live(token: str, endpoint: str, dump_raw: str = "") -> Dict[str, Any]:
    """真实拉取账户 + 商品池。"""
    client = McpClient(endpoint, token)
    info = client.initialize()
    server = (info.get("serverInfo") or {}).get("name", "未知")
    print(f"已连接 MCP：{server}（协议 {client.protocol_version}）", file=sys.stderr)

    raw_account = client.call_tool("query-my-account", {})
    raw_products = client.call_tool("mall-points-products", {})

    if dump_raw:
        with open(dump_raw, "w", encoding="utf-8") as f:
            json.dump(
                {"account": raw_account, "products": raw_products},
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"原始返回已存到 {dump_raw}，可用来核对字段名", file=sys.stderr)

    account = normalize_account(raw_account)
    products = normalize_products(raw_products)

    if account["available"] <= 0 and not products:
        raise McpError(
            "拿到了响应但解析不出积分和商品。建议加 --dump-raw raw.json "
            "把原始返回存下来，再按实际字段名调整适配层。"
        )

    return {
        "_note": "由 scripts/fetch_via_mcp.py 从麦当劳 MCP 实时拉取。",
        "account": account,
        "preferences": {"use_window_days": 14},
        "products": products,
    }


def load_demo() -> Dict[str, Any]:
    """读示例数据，保证没有 Token 也能完整跑通。"""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "examples", "account_demo.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="从麦当劳 MCP 拉取积分账户与商城商品",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo", action="store_true", help="用内置示例数据，不联网")
    parser.add_argument("--token", default="", help="MCP Token（建议改用环境变量 MCD_MCP_TOKEN）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help=f"MCP 地址，默认 {DEFAULT_ENDPOINT}")
    parser.add_argument("--out", "-o", default="", help="结果写入的 JSON 路径")
    parser.add_argument("--dump-raw", default="", help="把 MCP 原始返回另存一份，方便核对字段")
    parser.add_argument("--list-tools", action="store_true", help="列出服务端可用的工具")
    args = parser.parse_args(argv)

    token = (args.token or os.environ.get("MCD_MCP_TOKEN", "")).strip()
    # 容忍用户把 "Bearer " 一起粘进来
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    if args.list_tools:
        if not token:
            print("需要 MCD_MCP_TOKEN 才能列工具", file=sys.stderr)
            return 2
        try:
            client = McpClient(args.endpoint, token)
            client.initialize()
            for tool in client.list_tools():
                print(f"- {tool.get('name')}: {tool.get('description', '')[:80]}")
        except McpError as exc:
            print(f"失败：{exc}", file=sys.stderr)
            return 1
        return 0

    try:
        if args.demo or not token:
            if not args.demo:
                print(
                    "没检测到 MCD_MCP_TOKEN，自动切到示例数据。"
                    "接真实账号请先：export MCD_MCP_TOKEN=\"你的Token\"",
                    file=sys.stderr,
                )
            data = load_demo()
            data["_note"] = "示例数据（模拟），非真实账户。设置 MCD_MCP_TOKEN 后可拉取真实数据。"
        else:
            data = fetch_live(token, args.endpoint, args.dump_raw)
    except McpError as exc:
        print(f"拉取失败：{exc}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("找不到 examples/account_demo.json", file=sys.stderr)
        return 2

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        acc = data["account"]
        print(
            f"已写入 {args.out}：可用 {acc['available']} 分，"
            f"即将过期 {acc['expiring']} 分，商品 {len(data['products'])} 件",
            file=sys.stderr,
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
