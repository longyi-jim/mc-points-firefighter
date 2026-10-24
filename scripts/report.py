#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分消防员 · 报告卡生成器

把 optimize.py 的计算结果渲染成一张单文件 HTML 报告卡。

为什么非要做这一张卡？
因为积分这件事，光讲道理没人信。得让人看见：
"随手换"和"算一遍"之间，隔着 960 分。

产物特点：
  - 单文件，不引用任何外部资源（无 CDN、无字体、无图片）
  - 手机屏幕直接能读，电脑上截图也清楚
  - 打印友好（A4 不截断）

用法：
  python scripts/report.py --input result.json --out examples/report.html
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import sys
from typing import Any, Dict, List, Optional

PLAN_COLORS = {
    "max_coverage": "#DA291C",
    "balanced": "#1A7F62",
    "max_utility": "#2B5AA7",
}


def _fmt(n: Any) -> str:
    """千分位。"""
    try:
        num = float(n)
    except (TypeError, ValueError):
        return str(n)
    if abs(num - round(num)) < 1e-9:
        return f"{int(round(num)):,}"
    return f"{num:,.1f}"


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _bar(ratio: float, color: str) -> str:
    width = max(1.5, min(100.0, ratio * 100.0))
    return (
        f'<div class="bar-track"><div class="bar-fill" '
        f'style="width:{width:.1f}%;background:{color}"></div></div>'
    )


def _plan_card(plan: Dict[str, Any], capacity: int, is_best: bool) -> str:
    key = plan.get("key", "")
    color = PLAN_COLORS.get(key, "#666")
    badge = '<span class="badge">推荐</span>' if is_best else ""
    expected = plan.get("expected_waste", 0) or 0
    ratio = (expected / capacity) if capacity else 0.0

    rows: List[str] = []
    for pick in plan.get("picks") or []:
        risk = pick.get("secondary_risk", 0) or 0
        risk_chip = (
            f'<span class="risk">券 {pick.get("valid_days")} 天到期 · 用不完风险 {risk * 100:.0f}%</span>'
            if risk > 0
            else f'<span class="safe">券 {pick.get("valid_days")} 天内有效</span>'
        )
        rows.append(
            "<li>"
            f'<span class="pname">{_esc(pick.get("name"))}</span>'
            f'<span class="pcount">×{pick.get("count")}</span>'
            f'<span class="ppoints">-{_fmt(pick.get("spent"))}</span>'
            f"{risk_chip}"
            "</li>"
        )
    if not rows:
        rows.append('<li class="empty">积分不够换任何商品</li>')

    return f"""
    <article class="plan" style="--accent:{color}">
      <div class="plan-head">
        <div class="plan-title">{_esc(plan.get('label'))}{badge}</div>
        <div class="plan-score">{plan.get('score', 0)}<span>/100</span></div>
      </div>
      <p class="plan-desc">{_esc(plan.get('desc'))}</p>
      <ul class="picks">{''.join(rows)}</ul>
      <div class="plan-foot">
        <div><span class="fk">消耗</span><span class="fv">{_fmt(plan.get('consumed'))} 分</span></div>
        <div><span class="fk">剩余</span><span class="fv">{_fmt(plan.get('residual'))} 分</span></div>
        <div class="warn"><span class="fk">期望烧掉</span><span class="fv">{_fmt(expected)} 分</span></div>
      </div>
      {_bar(ratio, color)}
    </article>
    """


def render_html(result: Dict[str, Any], note: str = "") -> str:
    acc = result.get("account", {})
    headline = result.get("headline", {})
    baseline = result.get("baseline", {})
    plans: List[Dict[str, Any]] = result.get("plans", [])
    capacity = result.get("capacity", 0) or 0
    best_key = headline.get("recommend")

    naive_expected = baseline.get("expected_waste", 0) or 0
    best_expected = headline.get("best_expected_waste", 0) or 0
    saved = headline.get("saved_points", 0) or 0
    scale = max(naive_expected, best_expected, 1)

    # 三方案 + 对照的横向条形对比
    bars = [
        ("随手换", naive_expected, "#B0AAA4"),
    ]
    for plan in plans:
        bars.append((plan.get("label", ""), plan.get("expected_waste", 0) or 0,
                     PLAN_COLORS.get(plan.get("key", ""), "#666")))
    bar_rows = "".join(
        f'<div class="brow"><div class="blabel">{_esc(label)}</div>'
        f'{_bar(value / scale, color)}'
        f'<div class="bval">{_fmt(value)} 分</div></div>'
        for label, value, color in bars
    )

    cards = "".join(
        _plan_card(plan, capacity, plan.get("key") == best_key) for plan in plans
    )

    today = _dt.date.today().strftime("%Y-%m-%d")
    note_html = f'<p class="datanote">{note}</p>' if note else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>麦麦积分消防员 · 积分救火报告</title>
<style>
  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0;
    background: #F2F0EC;
    color: #1A1A1A;
    font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui, sans-serif;
    line-height: 1.6;
  }}
  .page {{
    max-width: 860px;
    margin: 28px auto;
    background: #FFFFFF;
    border-radius: 18px;
    padding: 34px 38px 28px;
    box-shadow: 0 10px 34px rgba(26, 26, 26, 0.10);
  }}
  header {{ border-bottom: 3px solid #1A1A1A; padding-bottom: 16px; margin-bottom: 22px; }}
  .brand {{ font-size: 25px; font-weight: 800; letter-spacing: -0.4px; }}
  .brand .fire {{ color: #DA291C; }}
  .en {{
    display: inline-block; margin-left: 10px; font-size: 10.5px; font-weight: 700;
    letter-spacing: 2.2px; color: #9A948D; vertical-align: 4px;
  }}
  .sub {{ margin-top: 7px; font-size: 13.5px; color: #5C5751; }}

  .account {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 26px; }}
  .stat {{ background: #F7F5F1; border-radius: 11px; padding: 13px 14px; }}
  .stat .k {{ font-size: 11.5px; color: #85807A; letter-spacing: .4px; }}
  .stat .v {{ font-size: 24px; font-weight: 800; margin-top: 3px; letter-spacing: -.6px; }}
  .stat .v.small {{ font-size: 15px; font-weight: 700; padding-top: 6px; }}
  .stat.danger {{ background: #FDF0EE; }}
  .stat.danger .v {{ color: #DA291C; }}

  .headline {{
    display: flex; align-items: center; gap: 16px;
    background: #1A1A1A; color: #FFF; border-radius: 14px;
    padding: 22px 26px; margin-bottom: 24px;
  }}
  .hl-block {{ text-align: center; }}
  .hl-block .l {{ font-size: 11.5px; color: #A9A39C; letter-spacing: .6px; }}
  .hl-block .n {{ font-size: 38px; font-weight: 800; line-height: 1.1; letter-spacing: -1.4px; }}
  .hl-block .c {{ font-size: 11px; color: #8C867F; }}
  .hl-block.bad .n {{ color: #E5675B; }}
  .hl-block.good .n {{ color: #FFC72C; }}
  .hl-arrow {{ font-size: 21px; color: #6E6963; }}
  .hl-saved {{
    margin-left: auto; text-align: right; border-left: 1px solid #3A3733; padding-left: 22px;
  }}
  .hl-saved .l {{ font-size: 11.5px; color: #A9A39C; }}
  .hl-saved .n {{ font-size: 30px; font-weight: 800; color: #FFC72C; letter-spacing: -1px; }}

  h2 {{ font-size: 14px; letter-spacing: 1.4px; color: #6E6963; margin: 28px 0 12px; font-weight: 700; }}
  h2::before {{ content: ""; display: inline-block; width: 4px; height: 13px; background: #DA291C; margin-right: 8px; vertical-align: -1px; border-radius: 1px; }}

  .brow {{ display: flex; align-items: center; gap: 12px; margin-bottom: 9px; }}
  .blabel {{ width: 108px; font-size: 12.5px; color: #4A4540; text-align: right; flex-shrink: 0; }}
  .bval {{ width: 78px; font-size: 12.5px; font-weight: 700; color: #2B2724; flex-shrink: 0; }}
  .bar-track {{ flex: 1; height: 15px; background: #EFEDE8; border-radius: 8px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 8px; }}

  .plans {{ display: grid; gap: 13px; }}
  .plan {{ border: 1.5px solid #E8E5DF; border-left: 4px solid var(--accent); border-radius: 12px; padding: 16px 18px 13px; }}
  .plan-head {{ display: flex; align-items: baseline; justify-content: space-between; }}
  .plan-title {{ font-size: 15.5px; font-weight: 800; }}
  .badge {{ display: inline-block; margin-left: 8px; background: var(--accent); color: #FFF;
            font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px;
            vertical-align: 2px; letter-spacing: .5px; }}
  .plan-score {{ font-size: 19px; font-weight: 800; color: var(--accent); }}
  .plan-score span {{ font-size: 10.5px; color: #A8A29B; font-weight: 600; }}
  .plan-desc {{ margin: 3px 0 10px; font-size: 12.5px; color: #7A746E; }}
  .picks {{ list-style: none; margin: 0 0 12px; padding: 0; }}
  .picks li {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
               padding: 6px 0; border-bottom: 1px dashed #EFEDE8; font-size: 13px; }}
  .picks li:last-child {{ border-bottom: none; }}
  .pname {{ font-weight: 700; }}
  .pcount {{ color: #6E6963; font-size: 12px; }}
  .ppoints {{ margin-left: auto; font-weight: 800; color: #DA291C; font-variant-numeric: tabular-nums; }}
  .risk {{ flex-basis: 100%; font-size: 11px; color: #B4570E; }}
  .safe {{ flex-basis: 100%; font-size: 11px; color: #7F8A80; }}
  .empty {{ color: #A8A29B; font-size: 12.5px; }}
  .plan-foot {{ display: flex; gap: 26px; font-size: 12px; margin-bottom: 9px; }}
  .fk {{ color: #96908A; margin-right: 6px; }}
  .fv {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
  .plan-foot .warn .fv {{ color: #DA291C; }}

  .notes {{ margin-top: 29px; padding-top: 16px; border-top: 1px solid #EFEDE8; }}
  .notes p {{ font-size: 11.5px; color: #8C867F; margin: 0 0 6px; }}
  .notes b {{ color: #5C5751; }}
  .datanote {{ font-size: 11.5px; color: #B4570E; background: #FFF8ED; border-radius: 7px;
               padding: 8px 11px; margin-top: 12px !important; }}
  footer {{ margin-top: 18px; font-size: 10.5px; color: #B0AAA4; display: flex; justify-content: space-between; }}

  @media print {{
    body {{ background: #FFF; }}
    .page {{ box-shadow: none; margin: 0; max-width: 100%; padding: 12mm; }}
    .plan {{ break-inside: avoid; }}
  }}
  @media (max-width: 640px) {{
    .page {{ margin: 0; border-radius: 0; padding: 22px 16px; }}
    .account {{ grid-template-columns: repeat(2, 1fr); }}
    .headline {{ flex-wrap: wrap; gap: 10px; padding: 18px; }}
    .hl-block .n {{ font-size: 30px; }}
    .hl-saved {{ margin-left: 0; border-left: none; padding-left: 0; text-align: left; flex-basis: 100%; }}
    .blabel {{ width: 78px; font-size: 11.5px; }}
    .bval {{ width: 62px; font-size: 11.5px; }}
  }}
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="brand">麦麦<span class="fire">积分消防员</span><span class="en">MC POINTS FIREFIGHTER</span></div>
    <div class="sub">积分到期就归零。在它烧完之前，先算一遍。</div>
  </header>

  <section class="account">
    <div class="stat"><div class="k">可用积分</div><div class="v">{_fmt(acc.get('available'))}</div></div>
    <div class="stat danger"><div class="k">即将过期</div><div class="v">{_fmt(acc.get('expiring'))}</div></div>
    <div class="stat"><div class="k">到期日</div><div class="v small">{_esc(acc.get('expiring_date') or '—')}</div></div>
    <div class="stat"><div class="k">可兑商品</div><div class="v">{_fmt(result.get('usable_products'))}</div></div>
  </section>

  <section class="headline">
    <div class="hl-block bad">
      <div class="l">随手换 · 期望烧掉</div>
      <div class="n">{_fmt(naive_expected)}</div>
      <div class="c">分</div>
    </div>
    <div class="hl-arrow">→</div>
    <div class="hl-block good">
      <div class="l">算一遍 · 期望烧掉</div>
      <div class="n">{_fmt(best_expected)}</div>
      <div class="c">分</div>
    </div>
    <div class="hl-saved">
      <div class="l">挽回</div>
      <div class="n">{_fmt(saved)}</div>
      <div class="c" style="font-size:11px;color:#8C867F">分积分</div>
    </div>
  </section>

  <h2>四种做法，期望烧掉多少</h2>
  {bar_rows}

  <h2>方案明细</h2>
  <div class="plans">{cards}</div>

  <section class="notes">
    <p><b>「期望烧掉」怎么算的：</b>没花掉的零头 ＋ 花掉了但券大概率用不完的部分。第二项按券的有效期估算——有效期短于 {result.get('use_window_days', 14)} 天的，按比例折算成可能用不掉的风险。</p>
    <p><b>为什么不是「花得最干净」就好：</b>用 1600 分换 5 张 7 天到期的薯条券，账面浪费是 0，但 14 天内吃不了 5 次，这 800 分和烧掉没区别。</p>
    <p><b>算法：</b>有界背包精确求解（二进制拆分 + 动态规划），不是贪心启发式；单测用暴力穷举交叉验证过 600 组随机数据。</p>
    <p><b>支付边界：</b>本工具只做计算和建议。真正的兑换动作由你自己在麦当劳 App 里确认，工具不代扣积分。</p>
    {note_html}
  </section>

  <footer>
    <span>生成于 {today} · mc-points-firefighter v1.0.0</span>
    <span>非麦当劳官方产品 · 数据以官方渠道实时结果为准</span>
  </footer>
</div>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成积分救火报告卡（单文件 HTML）")
    parser.add_argument("--input", "-i", default="result.json", help="optimize.py 输出的 JSON")
    parser.add_argument("--out", "-o", default="examples/report.html", help="HTML 输出路径")
    parser.add_argument("--note", default="", help="附加在报告底部的数据来源说明")
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            result = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {args.input}，先跑：python scripts/optimize.py --json -o result.json", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{args.input} 不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    note = args.note
    if not note:
        note = "本报告使用示例数据（模拟），非真实账户。设置 MCD_MCP_TOKEN 后可拉取你自己的积分与商城数据。"
        if result.get("_live"):
            note = ""

    html_text = render_html(result, note)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"报告已生成：{args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
