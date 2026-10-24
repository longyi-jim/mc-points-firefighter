#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦麦积分消防员 · 算法单元测试

重点只证明一件事：
**scripts/optimize.py 里的背包求解是精确解，不是拍脑袋的贪心。**

验证方法：在小规模随机数据上，把 DP 的解和"暴力枚举所有组合"的解对比。
暴力枚举在小数据上是绝对正确的（把所有可能性都试一遍），
如果 DP 每次都和它一致，说明 DP 没漏解。

运行：
    python tests/test_optimizer.py
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import itertools
import os
import random
import sys
import time
import unittest

# 让测试能直接导入 scripts/ 下的模块
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))

import optimize as opt  # noqa: E402


# ---------------------------------------------------------------------------
# 暴力穷举：小规模下的标准答案
# ---------------------------------------------------------------------------


def brute_force_best(
    products,
    capacity: int,
    value_fn,
) -> tuple[float, int]:
    """枚举所有可能的兑换组合，返回（最大总价值, 对应的消耗积分）。

    只在商品数少、限购小的时候用，组合数可控。
    """
    ranges = []
    for p in products:
        if not p.in_stock or p.points > capacity:
            ranges.append([0])
        else:
            ranges.append(list(range(0, min(p.limit, capacity // p.points) + 1)))

    best_value = 0.0
    best_spent = 0
    for combo in itertools.product(*ranges):
        spent = sum(c * p.points for c, p in zip(combo, products) if c)
        if spent > capacity:
            continue
        value = sum(c * value_fn(p) for c, p in zip(combo, products) if c)
        if value > best_value:
            best_value = value
            best_spent = spent
    return best_value, best_spent


def random_instance(rng: random.Random, n_products: int, max_points: int = 40):
    products = []
    for i in range(n_products):
        products.append(
            opt.Product(
                pid=f"P{i}",
                name=f"商品{i}",
                points=rng.randint(1, max_points),
                valid_days=rng.choice([3, 5, 7, 10, 14, 20, 30, 60]),
                limit=rng.randint(0, 3),
                category=rng.choice(["food", "drink", "snack", "dessert", "merch"]),
                in_stock=rng.random() > 0.1,
            )
        )
    return products


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestKnapsackIsExact(unittest.TestCase):
    """核心测试：DP 必须和暴力穷举结果一致。"""

    def test_max_coverage_matches_bruteforce(self) -> None:
        """「一分不浪费」策略：消耗积分必须等于暴力枚举的上界。"""
        rng = random.Random(20261024)
        vf = opt._make_value_fn("max_coverage", opt.DEFAULT_CATEGORY_PREF, 14)

        for case in range(300):
            products = random_instance(rng, n_products=rng.randint(1, 5))
            capacity = rng.randint(0, 60)

            selection = opt.knapsack(products, capacity, vf)
            spent = sum(
                cnt * next(p.points for p in products if p.pid == pid)
                for pid, cnt in selection.items()
            )
            _, best_spent = brute_force_best(products, capacity, vf)

            self.assertEqual(
                spent,
                best_spent,
                msg=f"第 {case} 组：DP 消耗 {spent} 分，暴力最优 {best_spent} 分",
            )

    def test_max_utility_matches_bruteforce(self) -> None:
        """「只换用得上的」策略：总效用必须等于暴力枚举的上界。"""
        rng = random.Random(1024)
        vf = opt._make_value_fn("max_utility", opt.DEFAULT_CATEGORY_PREF, 14)

        for case in range(300):
            products = random_instance(rng, n_products=rng.randint(1, 4))
            capacity = rng.randint(0, 60)

            selection = opt.knapsack(products, capacity, vf)
            by_id = {p.pid: p for p in products}
            value = sum(cnt * vf(by_id[pid]) for pid, cnt in selection.items())
            best_value, _ = brute_force_best(products, capacity, vf)

            self.assertAlmostEqual(
                value,
                best_value,
                places=6,
                msg=f"第 {case} 组：DP 效用 {value}，暴力最优 {best_value}",
            )


class TestConstraints(unittest.TestCase):
    """方案必须满足所有业务约束。"""

    def setUp(self) -> None:
        self.account = opt.Account(available=4200, expiring=1800, expiring_date="2026-11-30")
        self.products = [
            opt.Product(pid="A", name="A", points=320, valid_days=7, limit=2),
            opt.Product(pid="B", name="B", points=960, valid_days=30, limit=1),
            opt.Product(pid="C", name="C", points=500, valid_days=20, limit=5),
        ]

    def test_never_exceeds_capacity(self) -> None:
        for strategy in ("max_coverage", "max_utility", "balanced"):
            vf = opt._make_value_fn(strategy, opt.DEFAULT_CATEGORY_PREF, 14)
            selection = opt.knapsack(self.products, 1800, vf)
            spent = sum(
                cnt * next(p.points for p in self.products if p.pid == pid)
                for pid, cnt in selection.items()
            )
            self.assertLessEqual(spent, 1800, msg=f"{strategy} 超预算了")

    def test_respects_purchase_limit(self) -> None:
        vf = opt._make_value_fn("max_coverage", opt.DEFAULT_CATEGORY_PREF, 14)
        selection = opt.knapsack(self.products, 1800, vf)
        for pid, cnt in selection.items():
            limit = next(p.limit for p in self.products if p.pid == pid)
            self.assertLessEqual(cnt, limit, msg=f"{pid} 超过限购 {limit}")

    def test_skips_out_of_stock(self) -> None:
        products = [opt.Product(pid="X", name="X", points=100, in_stock=False)]
        vf = opt._make_value_fn("max_coverage", opt.DEFAULT_CATEGORY_PREF, 14)
        self.assertEqual(opt.knapsack(products, 1000, vf), {})

    def test_zero_capacity_returns_empty(self) -> None:
        vf = opt._make_value_fn("max_coverage", opt.DEFAULT_CATEGORY_PREF, 14)
        self.assertEqual(opt.knapsack(self.products, 0, vf), {})

    def test_no_affordable_product(self) -> None:
        products = [opt.Product(pid="BIG", name="大件", points=5000)]
        vf = opt._make_value_fn("max_coverage", opt.DEFAULT_CATEGORY_PREF, 14)
        self.assertEqual(opt.knapsack(products, 1800, vf), {})


class TestAccountGuard(unittest.TestCase):
    """账户数据兜底。"""

    def test_expiring_clamped_to_available(self) -> None:
        """即将过期积分不可能超过可用积分，超了就截平。"""
        acc = opt.Account(available=100, expiring=999)
        self.assertEqual(acc.expiring, 100)

    def test_negative_points_rejected(self) -> None:
        with self.assertRaises(ValueError):
            opt.Account(available=-1, expiring=0)

    def test_zero_point_product_rejected(self) -> None:
        with self.assertRaises(ValueError):
            opt.Product(pid="Z", name="零分", points=0)


class TestMetrics(unittest.TestCase):
    """期望浪费指标的算法。"""

    def test_expected_waste_counts_secondary_loss(self) -> None:
        """花光积分但券用不完，期望浪费应当大于 0。"""
        plan = opt.Plan(key="t", label="t", desc="t", capacity=1000)
        plan.picks = [
            opt.Pick(pid="A", name="A", points=1000, count=1, valid_days=1, risk=0.9)
        ]
        # 零头是 0，但 90% 的积分押在一张明天就过期的券上
        self.assertEqual(plan.residual, 0)
        self.assertAlmostEqual(plan.expected_waste, 900.0)
        self.assertLess(plan.score, 20)

    def test_full_validity_has_no_secondary_loss(self) -> None:
        plan = opt.Plan(key="t", label="t", desc="t", capacity=1000)
        plan.picks = [
            opt.Pick(pid="A", name="A", points=1000, count=1, valid_days=60, risk=0.0)
        ]
        self.assertAlmostEqual(plan.expected_waste, 0.0)
        self.assertEqual(plan.score, 100.0)

    def test_recommend_picks_lowest_expected_waste(self) -> None:
        """端到端：推荐的方案必须是期望浪费最少的那个。"""
        data = os.path.join(os.path.dirname(_HERE), "examples", "account_demo.json")
        account, products, window, prefs = opt.load_input(data)
        result = opt.optimize(account, products, window=window, cat_pref=prefs)

        wastes = {p["key"]: p["expected_waste"] for p in result["plans"]}
        recommend = result["headline"]["recommend"]
        self.assertEqual(
            wastes[recommend],
            min(wastes.values()),
            msg=f"推荐的 {recommend} 不是期望浪费最少的",
        )


class TestPerformance(unittest.TestCase):
    """真实规模下不能卡。"""

    def test_large_instance_finishes_fast(self) -> None:
        rng = random.Random(7)
        products = [
            opt.Product(
                pid=f"P{i}",
                name=f"商品{i}",
                points=rng.choice([260, 320, 420, 520, 640, 680, 720, 880, 960, 1140, 1580]),
                valid_days=rng.choice([7, 14, 21, 30, 45, 60, 90]),
                limit=rng.randint(1, 6),
            )
            for i in range(80)
        ]
        vf = opt._make_value_fn("balanced", opt.DEFAULT_CATEGORY_PREF, 14)
        start = time.perf_counter()
        opt.knapsack(products, 5000, vf)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, msg=f"5000 积分 × 80 商品耗时 {elapsed:.2f}s，太慢了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
