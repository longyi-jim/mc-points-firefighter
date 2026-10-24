#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分消防员 · 一键跑通

一条命令做完三件事：拿数据 → 算方案 → 出报告卡。

    python scripts/run.py            # 用示例数据跑
    python scripts/run.py --live     # 用你的真实账号跑（需 MCD_MCP_TOKEN）

没有任何第三方依赖，Python 3.9+ 即可。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import fetch_via_mcp  # noqa: E402
import optimize as opt  # noqa: E402
import report  # noqa: E402

ROOT = os.path.dirname(_HERE)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="麦麦积分消防员 · 一键跑通（拿数据 → 算方案 → 出报告）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--live", action="store_true", help="用真实账号（需环境变量 MCD_MCP_TOKEN）")
    parser.add_argument("--window", type=int, default=0, help="用券窗口天数，默认 14")
    parser.add_argument("--json-out", default=os.path.join(ROOT, "result.json"), help="计算结果的保存路径")
    parser.add_argument("--html-out", default=os.path.join(ROOT, "examples", "report.html"), help="报告卡保存路径")
    parser.add_argument("--quiet", action="store_true", help="不打印终端简报")
    args = parser.parse_args(argv)

    # 1. 拿数据
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if args.live:
        if not token:
            print(
                "要用真实账号，需要先设置 MCD_MCP_TOKEN。\n"
                "  1) 打开 https://open.mcd.cn/mcp 用手机号登录\n"
                "  2) 点控制台 → 激活 → 复制 MCP Token\n"
                '  3) export MCD_MCP_TOKEN="你复制的Token"',
                file=sys.stderr,
            )
            return 2
        try:
            data = fetch_via_mcp.fetch_live(token, fetch_via_mcp.DEFAULT_ENDPOINT)
        except fetch_via_mcp.McpError as exc:
            print(f"拉取失败：{exc}", file=sys.stderr)
            return 1
    else:
        data = fetch_via_mcp.load_demo()

    # 2. 算方案
    data_dir = os.path.join(ROOT, "examples")
    tmp_input = os.path.join(data_dir, "_run_input.json")
    os.makedirs(data_dir, exist_ok=True)
    with open(tmp_input, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    account, products, window, cat_pref = opt.load_input(tmp_input)
    if args.window > 0:
        window = args.window
    result = opt.optimize(account, products, window=window, cat_pref=cat_pref)
    result["_live"] = bool(args.live)

    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if not args.quiet:
        print(opt.render_text(result))

    # 3. 出报告
    note = "" if args.live else (
        "本报告使用示例数据（模拟），非真实账户。设置 MCD_MCP_TOKEN 后可用 --live 拉取你自己的数据。"
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.html_out)), exist_ok=True)
    with open(args.html_out, "w", encoding="utf-8") as f:
        f.write(report.render_html(result, note))

    print(f"计算结果：{args.json_out}", file=sys.stderr)
    print(f"报告卡：  {args.html_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
