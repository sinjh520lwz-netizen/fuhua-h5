# -*- coding: utf-8 -*-
"""
回测引擎测试 - 验证核心计算正确性
===================================

使用已知数据测试：
- 总收益率、年化收益率
- 最大回撤
- 夏普比率
- 胜率、盈亏比
"""

import sys
import os
import numpy as np
import pandas as pd

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine, TradeRecord


def make_test_data():
    """构造已知的测试股票数据"""
    dates = pd.date_range('2024-01-02', periods=60, freq='B').strftime('%Y-%m-%d').tolist()

    # 股票A: 先涨后跌，涨10%后跌回原位
    close_a = [10.0] * 10 + [11.0] * 10 + [10.5] * 10 + [10.0] * 30  # 总共60天
    df_a = pd.DataFrame({
        'date': dates,
        'open': close_a,
        'close': close_a,
        'high': [c * 1.01 for c in close_a],
        'low': [c * 0.99 for c in close_a],
        'volume': [1000000] * 60,
        'vol': [1000000] * 60,
        'pctChg': [0] + [(close_a[i] - close_a[i-1]) / close_a[i-1] * 100 for i in range(1, 60)],
    })

    # 股票B: 稳定上涨，每天涨0.5%
    close_b = [10.0 * (1.005 ** i) for i in range(60)]
    df_b = pd.DataFrame({
        'date': dates,
        'open': close_b,
        'close': close_b,
        'high': [c * 1.01 for c in close_b],
        'low': [c * 0.99 for c in close_b],
        'volume': [800000] * 60,
        'vol': [800000] * 60,
        'pctChg': [0] + [(close_b[i] - close_b[i-1]) / close_b[i-1] * 100 for i in range(1, 60)],
    })

    return {'TEST_A': df_a, 'TEST_B': df_b}


def simple_strategy(date, stock_data, context):
    """
    简单测试策略：
    - 第5天买入TEST_B
    - 第30天卖出TEST_B
    - 第10天买入TEST_A
    - 第25天卖出TEST_A
    """
    dates = sorted(set().union(*(df.index.tolist() for df in stock_data.values())))
    if date not in dates:
        return []
    idx = dates.index(date)
    signals = []

    if idx == 5:
        signals.append({'code': 'TEST_B', 'action': 'buy', 'score': 80})
    if idx == 30 and 'TEST_B' in context.get('positions', {}):
        signals.append({'code': 'TEST_B', 'action': 'sell', 'score': 0})
    if idx == 10:
        signals.append({'code': 'TEST_A', 'action': 'buy', 'score': 70})
    if idx == 25 and 'TEST_A' in context.get('positions', {}):
        signals.append({'code': 'TEST_A', 'action': 'sell', 'score': 0})

    return signals


def test_basic_backtest():
    """测试基本回测流程"""
    print("=== 测试1: 基本回测流程 ===")
    engine = BacktestEngine(
        initial_capital=100000,
        take_profit=0.50,  # 设置高止盈避免提前触发
        stop_loss=-0.50,
        max_hold_days=100,
        commission=0.001,
        slippage=0.001,
        max_positions=5,
    )
    data = make_test_data()
    result = engine.run(simple_strategy, data, strategy_name="测试策略")

    print(f"  总交易次数: {result.total_trades}")
    print(f"  总收益率: {result.total_return:.2f}%")
    print(f"  年化收益率: {result.annual_return:.2f}%")
    print(f"  最大回撤: {result.max_drawdown:.2f}%")
    print(f"  夏普比率: {result.sharpe_ratio:.3f}")
    print(f"  胜率: {result.win_rate:.1f}%")
    print(f"  盈亏比: {result.profit_loss_ratio:.2f}")

    assert result.total_trades >= 2, f"应有至少2笔交易，实际{result.total_trades}"
    assert result.start_date != '', "起始日期不应为空"
    assert len(result.equity_curve) > 0, "收益曲线不应为空"
    print("  ✓ 基本回测通过\n")


def test_max_drawdown():
    """测试最大回撤计算"""
    print("=== 测试2: 最大回撤计算 ===")

    # 构造已知回撤: 100 -> 120 -> 96 (回撤20%)
    equity = [100, 110, 120, 115, 100, 96, 100, 110]
    dates = [f"2024-01-{i+1:02d}" for i in range(len(equity))]

    engine = BacktestEngine()
    result = engine._calc_metrics("回撤测试", [], equity, dates)

    # 理论最大回撤: (120-96)/120 = 20%
    expected_dd = 20.0
    print(f"  计算最大回撤: {result.max_drawdown:.2f}%")
    print(f"  期望最大回撤: {expected_dd:.2f}%")
    assert abs(result.max_drawdown - expected_dd) < 0.5, \
        f"最大回撤偏差过大: {result.max_drawdown:.2f}% vs {expected_dd:.2f}%"
    print("  ✓ 最大回撤计算正确\n")


def test_sharpe_ratio():
    """测试夏普比率计算"""
    print("=== 测试3: 夏普比率计算 ===")

    # 稳定收益序列：每天涨0.1%，共252天
    equity = [100 * (1.001 ** i) for i in range(252)]
    dates = [f"2024-{(i//30)+1:02d}-{(i%30)+1:02d}" for i in range(252)]

    engine = BacktestEngine(risk_free_rate=0.025)
    result = engine._calc_metrics("夏普测试", [], equity, dates)

    # 日均收益约0.1%，日标准差约0，夏普应该很高
    print(f"  夏普比率: {result.sharpe_ratio:.3f}")
    assert result.sharpe_ratio > 5.0, f"稳定上涨的夏普应>5，实际{result.sharpe_ratio:.3f}"
    print("  ✓ 夏普比率计算正确\n")


def test_win_rate_and_pl_ratio():
    """测试胜率和盈亏比"""
    print("=== 测试4: 胜率和盈亏比 ===")

    trades = [
        TradeRecord("A", "2024-01-01", 10.0, "2024-01-10", 11.0, 100, 100, 10.0, 9, "TP"),
        TradeRecord("B", "2024-01-02", 10.0, "2024-01-08", 10.5, 100, 50, 5.0, 6, "TP"),
        TradeRecord("C", "2024-01-03", 10.0, "2024-01-07", 9.5, 100, -50, -5.0, 4, "SL"),
        TradeRecord("D", "2024-01-04", 10.0, "2024-01-15", 9.0, 100, -100, -10.0, 11, "SL"),
    ]

    engine = BacktestEngine()
    equity = [10000, 10100, 10200, 10150, 10050]
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    result = engine._calc_metrics("胜率测试", trades, equity, dates)

    print(f"  胜率: {result.win_rate:.1f}% (期望50%)")
    print(f"  盈亏比: {result.profit_loss_ratio:.2f} (期望75/75=1.0)")
    assert abs(result.win_rate - 50.0) < 1, f"胜率应为50%"
    assert abs(result.profit_loss_ratio - 1.0) < 0.1, f"盈亏比应约为1.0"
    print("  ✓ 胜率和盈亏比正确\n")


def test_position_sizing():
    """测试仓位分配"""
    print("=== 测试5: 仓位分配 ===")
    from strategies.portfolio import PortfolioManager

    pm = PortfolioManager(total_capital=100000, max_positions=5)

    # 等权分配
    alloc = pm.allocate(['A', 'B', 'C', 'D', 'E'], method='equal')
    for code, info in alloc.items():
        print(f"  {code}: 权重={info['weight']:.2%}, 金额={info['target_amount']:.0f}")
    assert abs(sum(a['weight'] for a in alloc.values()) - 1.0) < 0.01, "权重之和应为1"
    print("  ✓ 等权分配正确\n")


def test_timing_strategy():
    """测试择时策略"""
    print("=== 测试6: 择时策略 ===")
    from strategies.timing_strategies import MATimingStrategy, VolatilityTimingStrategy

    # 构造上升趋势数据
    dates = pd.date_range('2024-01-02', periods=50, freq='B').strftime('%Y-%m-%d').tolist()
    close = [10 + i * 0.1 for i in range(50)]
    df = pd.DataFrame({'close': close}, index=dates)

    ma_strat = MATimingStrategy(ma_period=20)
    ratio = ma_strat.get_position_ratio(df, dates[-1])
    print(f"  上升趋势MA20仓位: {ratio:.1f} (期望1.0)")
    assert ratio == 1.0, "上升趋势应满仓"

    # 构造下降趋势
    close_down = [15 - i * 0.1 for i in range(50)]
    df_down = pd.DataFrame({'close': close_down}, index=dates)
    ratio_down = ma_strat.get_position_ratio(df_down, dates[-1])
    print(f"  下降趋势MA20仓位: {ratio_down:.1f} (期望0.0)")
    assert ratio_down == 0.0, "下降趋势应空仓"
    print("  ✓ 择时策略正确\n")


if __name__ == '__main__':
    print("\n" + "=" * 50)
    print("回测引擎测试")
    print("=" * 50 + "\n")

    test_basic_backtest()
    test_max_drawdown()
    test_sharpe_ratio()
    test_win_rate_and_pl_ratio()
    test_position_sizing()
    test_timing_strategy()

    print("=" * 50)
    print("全部测试通过! ✓")
    print("=" * 50)
