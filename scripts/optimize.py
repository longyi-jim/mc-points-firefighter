#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分消防员 · 兑换方案优化内核

核心问题
--------
麦当劳积分（麦麦积分）有有效期。到期未用，积分直接归零——不是打折，是清零。

而商城里的商品积分价是**离散的整数**：300、600、1200……
你的即将过期积分是 1800，很可能凑不出正好 1800 的组合。
剩下的零头，一分不剩地烧掉。

所以这不是"推荐几个商品"的问题，而是一个**有界背包问题**：
在"每件商品限购 N 件、积分价固定、总预算 = 即将过期积分"的约束下，
怎样组合才能把积分花得最干净。

本模块提供三种策略，同一个背包内核，不同的价值函数：

1. max_coverage  一分不浪费  —— 目标是"消耗积分最多"，宁可换用不太上的东西
2. max_utility   只换用得上的 —— 目标是"换到手的东西真能用掉"，允许留一点零头
3. balanced      折中        —— 积分和实用性各算一半

为什么还要算"二次浪费"？
------------------------
这是本项目最想指出的一件事：
**你换来的券，自己也会过期。**

用一个 300 积分、有效期 7 天的甜筒券，去救一批 30 天后过期的积分，
只是把火从左手挪到了右手。所以每个方案都会额外给出"二次浪费风险"评分：
券的有效期越短、越接近用不完，风险分越高。

依赖：Python 3.9+，纯标准库，零第三方依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: DP 容量上限。真实积分很少超过一万，这里兜住异常输入，避免内存爆掉。
MAX_CAPACITY = 50_000

#: 默认"用券窗口"（天）。假设一个普通用户从拿到券到真正用掉，平均 14 天。
#: 券的有效期短于这个窗口，就有用不完的风险。
DEFAULT_USE_WINDOW = 14

#: 品类偏好分。1.0 = 最想要，数字越小越"鸡肋"。
#: 用户可在输入 JSON 的 preferences.category_pref 里覆盖。
DEFAULT_CATEGORY_PREF: Dict[str, float] = {
    "food": 1.00,      # 主食 / 汉堡 / 套餐
    "drink": 0.95,     # 饮料 / 咖啡
    "dessert": 0.90,   # 甜品 / 冰淇淋
    "breakfast": 1.00, # 早餐
    "coupon": 0.80,    # 通用代金券
    "merch": 0.55,     # 实物周边（换回来大概率闲置）
    "other": 0.70,
}

#: 二次浪费风险的口径常量已并入 expected_waste，保留此注释说明历史沿革。


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Product:
    """商城里的一件可兑换商品。"""

    pid: str
    name: str
    points: int                     # 兑换所需积分
    valid_days: int = 30            # 兑换后券的有效天数
    limit: int = 1                  # 每人限兑数量
    category: str = "other"
    cash: float = 0.0               # 需要额外加价的金额（0 表示纯积分兑换）
    in_stock: bool = True

    def __post_init__(self) -> None:
        self.points = int(self.points)
        self.valid_days = int(self.valid_days)
        self.limit = int(self.limit)
        self.cash = float(self.cash or 0.0)
        if self.points <= 0:
            raise ValueError(f"商品 {self.pid} 的积分价必须为正整数")
        if self.limit < 0:
            raise ValueError(f"商品 {self.pid} 的限购数不能为负")


@dataclass
class Account:
    """积分账户快照（对应 MCP query-my-account 的返回）。"""

    available: int                  # 可用积分
    expiring: int                   # 即将过期积分
    expiring_date: str = ""         # 即将过期积分的到期日
    total: int = 0                  # 累计积分
    frozen: int = 0                 # 冻结积分

    def __post_init__(self) -> None:
        self.available = int(self.available)
        self.expiring = int(self.expiring)
        self.total = int(self.total or 0)
        self.frozen = int(self.frozen or 0)
        if self.available < 0 or self.expiring < 0:
            raise ValueError("积分数量不能为负")
        # 防御：即将过期积分不可能超过可用积分
        if self.expiring > self.available:
            self.expiring = self.available


@dataclass
class Pick:
    """方案里的一行：某商品换几件。"""

    pid: str
    name: str
    points: int
    count: int
    valid_days: int
    risk: float

    @property
    def spent(self) -> int:
        return self.points * self.count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "name": self.name,
            "points": self.points,
            "count": self.count,
            "spent": self.spent,
            "valid_days": self.valid_days,
            "secondary_risk": round(self.risk, 4),
        }


@dataclass
class Plan:
    """一个完整的兑换方案。"""

    key: str
    label: str
    desc: str
    picks: List[Pick] = field(default_factory=list)
    capacity: int = 0

    @property
    def consumed(self) -> int:
        return sum(p.spent for p in self.picks)

    @property
    def residual(self) -> int:
        return max(0, self.capacity - self.consumed)

    @property
    def waste_rate(self) -> float:
        """浪费率：即将过期积分里没被用掉的比例。"""
        if self.capacity <= 0:
            return 0.0
        return self.residual / self.capacity

    @property
    def secondary_risk(self) -> float:
        """二次浪费风险：按消耗积分加权平均的"用不完"概率。"""
        spent = self.consumed
        if spent <= 0:
            return 0.0
        return sum(p.risk * p.spent for p in self.picks) / spent

    @property
    def expected_waste(self) -> float:
        """期望烧掉的积分——本项目的核心指标。

        = 没花掉的零头 + 花掉了但券大概率用不完的那部分

        为什么不用"浪费率"排名？
        因为花光积分不等于没浪费：
        用 1600 分换 5 张 7 天有效期的薯条券，账面浪费是 0，
        但如果 14 天内根本吃不了 5 次，这 800 分和烧掉没区别。
        把这两笔损失放在同一个口径里，方案之间才可比。
        """
        wasted = self.residual
        for p in self.picks:
            wasted += p.risk * p.spent
        return wasted

    @property
    def score(self) -> float:
        """综合评分（0~100）：期望浪费越少分越高。"""
        if self.capacity <= 0:
            return 100.0
        return round(100.0 * (1.0 - self.expected_waste / self.capacity), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "desc": self.desc,
            "picks": [p.to_dict() for p in self.picks],
            "consumed": self.consumed,
            "residual": self.residual,
            "waste_rate": round(self.waste_rate, 4),
            "secondary_risk": round(self.secondary_risk, 4),
            "expected_waste": round(self.expected_waste, 1),
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# 价值函数
# ---------------------------------------------------------------------------


def _freshness(valid_days: int, window: int) -> float:
    """券的"能用完"程度。有效期 >= 窗口记 1.0，短则线性折减，最低 0.2。

    这是把"二次浪费"提前塞进背包的方式：短效期商品的单位价值天然更低，
    DP 会自己避开它们，而不是等方案出来再补救。
    """
    if window <= 0:
        return 1.0
    if valid_days >= window:
        return 1.0
    return max(0.2, valid_days / window)


def _unit_utility(p: Product, cat_pref: Dict[str, float], window: int) -> float:
    """单位积分的效用。= 品类偏好 × 有效期充分度"""
    pref = cat_pref.get(p.category, cat_pref.get("other", 0.7))
    return pref * _freshness(p.valid_days, window)


def _make_value_fn(
    strategy: str,
    cat_pref: Dict[str, float],
    window: int,
) -> Callable[[Product], float]:
    """按策略返回"每件商品值多少"，供背包最大化。"""

    def max_coverage(p: Product) -> float:
        # 主目标：消耗积分越多越好。微小的效用项只用于同分时挑更实用的。
        u = _unit_utility(p, cat_pref, window)
        return p.points + 1e-6 * p.points * u

    def max_utility(p: Product) -> float:
        # 主目标：换到的东西真能用掉。积分消耗只是约束，不是目标。
        return p.points * _unit_utility(p, cat_pref, window)

    def balanced(p: Product) -> float:
        # 一半看积分，一半看实用。
        u = _unit_utility(p, cat_pref, window)
        return p.points * (0.5 + 0.5 * u)

    table = {
        "max_coverage": max_coverage,
        "max_utility": max_utility,
        "balanced": balanced,
    }
    if strategy not in table:
        raise ValueError(f"未知策略: {strategy}")
    return table[strategy]


# ---------------------------------------------------------------------------
# 背包求解
# ---------------------------------------------------------------------------


def _binary_split(count: int) -> List[int]:
    """把"最多 count 件"拆成 1,2,4,... 的二进制组，把有界背包降为 0-1 背包。"""
    if count <= 0:
        return []
    groups: List[int] = []
    k = 1
    remaining = count
    while remaining > 0:
        take = min(k, remaining)
        groups.append(take)
        remaining -= take
        k *= 2
    return groups


def knapsack(
    products: Sequence[Product],
    capacity: int,
    value_fn: Callable[[Product], float],
) -> Dict[str, int]:
    """有界背包：在总积分 <= capacity 的约束下，最大化 Σ value。

    返回 {pid: 兑换件数}。使用二进制拆分 + 一维 DP，复杂度
    O(capacity × Σlog(limit))，几千积分的真实场景毫秒级完成。

    这是一个**精确解**，不是启发式——测试用暴力穷举交叉验证过。

    回溯不需要额外矩阵：0-1 背包里如果第 i 层在容量 cur 上做了更新，
    它的前驱必然是 cur - w_i，所以只需记录"这一层有没有动过 cur"。
    """
    if capacity <= 0:
        return {}

    cap = min(int(capacity), MAX_CAPACITY)

    # 拆成 0-1 物品：(重量, 价值, 单个商品, 这一组代表几件)
    units: List[Tuple[int, float, Product, int]] = []
    for p in products:
        if not p.in_stock or p.points > cap:
            continue
        affordable = cap // p.points
        max_count = min(p.limit, affordable)
        for group in _binary_split(max_count):
            units.append((p.points * group, value_fn(p) * group, p, group))

    if not units:
        return {}

    NEG = float("-inf")
    dp = [NEG] * (cap + 1)
    dp[0] = 0.0
    # took[i] 是第 i 个物品这一层的标记：1 表示该层在容量 w 上做了更新
    took: List[bytearray] = []

    for w, v, _p, _g in units:
        layer = bytearray(cap + 1)
        # 逆序更新，保证每组物品最多用一次
        for cur in range(cap, w - 1, -1):
            if dp[cur - w] == NEG:
                continue
            cand = dp[cur - w] + v
            if cand > dp[cur]:
                dp[cur] = cand
                layer[cur] = 1
        took.append(layer)

    # 全局取最优：dp[w] 已经包含价值函数，直接扫一遍即可
    # （max_coverage 的价值就是积分本身，所以等价于"取容量最大的可达点"）
    best_w = 0
    best_v = dp[0]
    for w in range(1, cap + 1):
        if dp[w] != NEG and dp[w] > best_v:
            best_v = dp[w]
            best_w = w

    # 回溯
    result: Dict[str, int] = {}
    cur = best_w
    for i in range(len(units) - 1, -1, -1):
        if cur <= 0:
            break
        if took[i][cur]:
            w, _v, p, g = units[i]
            result[p.pid] = result.get(p.pid, 0) + g
            cur -= w

    return result


# ---------------------------------------------------------------------------
# 方案生成
# ---------------------------------------------------------------------------


def _build_plan(
    key: str,
    label: str,
    desc: str,
    products: Sequence[Product],
    capacity: int,
    value_fn: Callable[[Product], float],
    window: int,
) -> Plan:
    selection = knapsack(products, capacity, value_fn)
    by_id = {p.pid: p for p in products}
    picks: List[Pick] = []
    for pid, count in selection.items():
        p = by_id[pid]
        picks.append(
            Pick(
                pid=p.pid,
                name=p.name,
                points=p.points,
                count=count,
                valid_days=p.valid_days,
                risk=round(1.0 - _freshness(p.valid_days, window), 4),
            )
        )
    picks.sort(key=lambda x: x.spent, reverse=True)
    return Plan(key=key, label=label, desc=desc, picks=picks, capacity=capacity)


def naive_baseline(products: Sequence[Product], capacity: int) -> Plan:
    """朴素做法：只换一种商品，挑"单品类能花掉最多积分"的那个。

    这是大多数人打开商城时的真实行为——看到什么换什么，不会算组合。
    拿它当对照，才能说明优化到底省了多少。
    """
    best_plan = Plan(
        key="naive",
        label="不看的话（常见做法）",
        desc="只换一种商品，哪个花得多换哪个",
        capacity=capacity,
    )
    best_consumed = 0

    for p in products:
        if not p.in_stock or p.points > capacity:
            continue
        max_count = min(p.limit, capacity // p.points)
        if max_count <= 0:
            continue
        consumed = max_count * p.points
        if consumed > best_consumed:
            best_consumed = consumed
            best_plan.picks = [
                Pick(
                    pid=p.pid,
                    name=p.name,
                    points=p.points,
                    count=max_count,
                    valid_days=p.valid_days,
                    risk=0.0,
                )
            ]
    return best_plan


def optimize(
    account: Account,
    products: Sequence[Product],
    window: int = DEFAULT_USE_WINDOW,
    cat_pref: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """跑完三种策略 + 朴素对照，返回完整结果（可直接 JSON 序列化）。"""
    cat_pref = {**DEFAULT_CATEGORY_PREF, **(cat_pref or {})}
    capacity = account.expiring

    strategies = [
        ("max_coverage", "一分不浪费", "优先把积分花干净，宁可换点不太用得上的"),
        ("balanced", "折中方案", "积分和实用性各算一半，多数人的选择"),
        ("max_utility", "只换用得上的", "只换真能用掉的东西，允许留一点零头"),
    ]

    plans: List[Plan] = []
    for key, label, desc in strategies:
        vf = _make_value_fn(key, cat_pref, window)
        plans.append(_build_plan(key, label, desc, products, capacity, vf, window))

    baseline = naive_baseline(products, capacity)
    # 朴素方案也补上二次浪费风险，方便同口径对比
    for pick in baseline.picks:
        pick.risk = round(1.0 - _freshness(pick.valid_days, window), 4)

    # 推荐口径：期望烧掉的积分最少，而不是"账面花得最干净"
    best = min(plans, key=lambda p: p.expected_waste) if plans else None
    saved_points = (baseline.expected_waste - best.expected_waste) if best else 0.0

    return {
        "account": {
            "available": account.available,
            "expiring": account.expiring,
            "expiring_date": account.expiring_date,
            "total": account.total,
        },
        "use_window_days": window,
        "capacity": capacity,
        "product_pool": len(products),
        "usable_products": sum(1 for p in products if p.in_stock and p.points <= capacity),
        "plans": [p.to_dict() for p in plans],
        "baseline": baseline.to_dict(),
        "headline": {
            "recommend": best.key if best else None,
            "recommend_label": best.label if best else "",
            "saved_points": round(saved_points, 1),
            "naive_expected_waste": round(baseline.expected_waste, 1),
            "best_expected_waste": round(best.expected_waste, 1) if best else 0.0,
            "naive_waste_rate": round(baseline.waste_rate, 4),
            "best_waste_rate": round(best.waste_rate, 4) if best else 0.0,
            "best_score": best.score if best else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# 输入解析
# ---------------------------------------------------------------------------


def load_input(path: str) -> Tuple[Account, List[Product], int, Dict[str, float]]:
    """读取输入 JSON。

    结构见 examples/account_demo.json。字段命名尽量贴近 MCP 返回，
    真正接 MCP 时由 fetch_via_mcp.py 做一层转换。
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    acc_raw = raw.get("account") or {}
    account = Account(
        available=acc_raw.get("available", 0),
        expiring=acc_raw.get("expiring", 0),
        expiring_date=acc_raw.get("expiring_date", ""),
        total=acc_raw.get("total", 0),
        frozen=acc_raw.get("frozen", 0),
    )

    products: List[Product] = []
    for item in raw.get("products", []):
        products.append(
            Product(
                pid=str(item.get("pid") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                points=item.get("points", 0),
                valid_days=item.get("valid_days", 30),
                limit=item.get("limit", 1),
                category=item.get("category", "other"),
                cash=item.get("cash", 0),
                in_stock=bool(item.get("in_stock", True)),
            )
        )

    prefs = raw.get("preferences") or {}
    window = int(prefs.get("use_window_days", DEFAULT_USE_WINDOW))
    cat_pref = prefs.get("category_pref") or {}
    return account, products, window, cat_pref


def render_text(result: Dict[str, Any]) -> str:
    """把结果渲染成终端里看得懂的文本。"""
    acc = result["account"]
    lines: List[str] = []
    lines.append("")
    lines.append("  麦麦积分消防员 · 火情简报")
    lines.append("  " + "─" * 46)
    lines.append(f"  可用积分      {acc['available']:>8}")
    lines.append(f"  即将过期      {acc['expiring']:>8}   {acc['expiring_date']}")
    lines.append(f"  可兑换商品    {result['usable_products']:>8} 件 / 共 {result['product_pool']} 件")
    lines.append("")

    base = result["baseline"]
    lines.append("  对照：不看的话（只换一种商品）")
    lines.append(
        f"    消耗 {base['consumed']} 分，账面烧掉 {base['residual']} 分"
        f"（浪费率 {base['waste_rate'] * 100:.1f}%）"
    )
    lines.append(
        f"    但券大概率用不完 → 期望烧掉 {base['expected_waste']:.0f} 分"
        f"（二次浪费风险 {base['secondary_risk'] * 100:.0f}%）"
    )
    lines.append("")

    for plan in result["plans"]:
        lines.append(f"  [{plan['label']}]  综合评分 {plan['score']}")
        lines.append(f"    {plan['desc']}")
        if not plan["picks"]:
            lines.append("    （积分不足，没有可兑换方案）")
        for pick in plan["picks"]:
            risk_txt = (
                f"  二次浪费风险 {pick['secondary_risk'] * 100:.0f}%"
                if pick["secondary_risk"] > 0
                else ""
            )
            lines.append(
                f"    · {pick['name']} ×{pick['count']}"
                f"  -{pick['spent']}分  (有效期{pick['valid_days']}天){risk_txt}"
            )
        lines.append(
            f"    合计消耗 {plan['consumed']} 分，剩余 {plan['residual']} 分"
            f"（浪费率 {plan['waste_rate'] * 100:.1f}%）"
        )
        lines.append(f"    期望烧掉 {plan['expected_waste']:.0f} 分")
        lines.append("")

    h = result["headline"]
    lines.append("  " + "─" * 46)
    lines.append(f"  推荐：{h['recommend_label']}")
    lines.append(
        f"  相比随手换，少烧 {h['saved_points']:.0f} 分"
        f"（期望烧掉 {h['naive_expected_waste']:.0f} → {h['best_expected_waste']:.0f} 分）"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="麦麦积分消防员 · 兑换方案优化内核",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", "-i", default="examples/account_demo.json",
                        help="输入 JSON 路径（默认 examples/account_demo.json）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于程序消费")
    parser.add_argument("--out", "-o", default="", help="把 JSON 结果另存到文件")
    args = parser.parse_args(argv)

    try:
        account, products, window, cat_pref = load_input(args.input)
    except FileNotFoundError:
        print(f"找不到输入文件：{args.input}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"输入数据有问题：{exc}", file=sys.stderr)
        return 2

    result = optimize(account, products, window=window, cat_pref=cat_pref)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.out}", file=sys.stderr)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
