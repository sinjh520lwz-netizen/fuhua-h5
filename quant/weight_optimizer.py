#!/usr/bin/env python3
"""
JH 权重自迭代优化器 v1.0
每日收盘后根据历史推荐的实际表现，自动调整评分因子权重

逻辑：
1. 统计每个因子在"赢"和"输"的推荐中出现的频率
2. 赢的推荐中高频出现的因子 → 权重上调
3. 输的推荐中高频出现的因子 → 权重下调
4. 用指数移动平均（EMA）平滑调整，避免单日剧烈波动
5. 设置上下限，防止权重失控
"""
import json, os, sys
from datetime import datetime, timedelta
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
WEIGHTS_FILE = os.path.join(DATA_DIR, 'adaptive_weights.json')
BACKTEST_FILE = os.path.join(DATA_DIR, 'backtest_result.json')

# 基准权重
BASE_WEIGHTS = {
    'ma_convergence': 10,  # 均线粘合
    'MACD': 13,            # MACD信号
    'RSI': 15,             # RSI信号
    '布林收窄': 4,         # 布林收窄
    '放量': 5,             # 放量
    '站上均线': 8,         # 站上均线
    '趋势': 12,            # 趋势
    '突破位置': 7,         # 突破位置
    '涨幅控制': 10,        # 涨幅控制
    '均线多头': 3,         # 均线多头
}

# 权重上下限（防止失控）
WEIGHT_MIN = 3
WEIGHT_MAX = 25

# 学习率（每次调整的幅度，越小越稳）
LEARNING_RATE = 0.1


def load_adaptive_weights():
    """加载自适应权重，首次使用基准权重"""
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return BASE_WEIGHTS.copy()


def save_adaptive_weights(weights):
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)
    # 追加权重历史记录
    WHISTORY_FILE = os.path.join(DATA_DIR, 'weight_history.json')
    try:
        with open(WHISTORY_FILE) as f:
            wh = json.load(f)
    except:
        wh = []
    today = datetime.now().strftime('%Y-%m-%d')
    # 移除今日旧记录
    wh = [w for w in wh if w.get('date') != today]
    wh.append({
        'date': today,
        'weights': weights,
        'total_samples': len(load_adaptive_weights()),
    })
    wh = wh[-30:]
    with open(WHISTORY_FILE, 'w') as f:
        json.dump(wh, f, indent=2, ensure_ascii=False)


def analyze_factor_performance():
    """
    从回测数据中分析每个因子的预测能力
    返回: {factor_name: {'win_corr': float, 'loss_corr': float, 'net_score': float}}
    """
    if not os.path.exists(BACKTEST_FILE):
        return {}

    with open(BACKTEST_FILE) as f:
        bt = json.load(f)

    all_picks = []
    for s in bt.get('daily_stats', []):
        all_picks.extend(s.get('picks', []))

    if not all_picks:
        return {}

    # 分赢家和输家
    winners = []  # T+3涨>3% 或 T+1涨>2%
    losers = []   # T+3跌>2% 或 T+1跌>3%

    for p in all_picks:
        t1 = p.get('t1_return', 0)
        t3 = p.get('t3_return')
        if isinstance(t3, str):
            t3 = None

        is_winner = (t3 is not None and t3 > 3) or (t1 > 2)
        is_loser = (t3 is not None and t3 < -2) or (t1 < -3)

        factors = p.get('factors', {})
        if is_winner:
            winners.append(factors)
        elif is_loser:
            losers.append(factors)

    if not winners and not losers:
        return {}

    # 统计每个因子在赢家/输家中的出现次数和平均贡献
    factor_stats = {}
    all_factors = set()
    for f in winners + losers:
        all_factors.update(f.keys())

    for fname in all_factors:
        win_count = sum(1 for f in winners if fname in f)
        loss_count = sum(1 for f in losers if fname in f)
        win_total = len(winners) if winners else 1
        loss_total = len(losers) if losers else 1

        # 赢家中出现频率 - 输家中出现频率 = 净预测力
        win_rate = win_count / win_total
        loss_rate = loss_count / loss_total
        net_score = win_rate - loss_rate

        factor_stats[fname] = {
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': round(win_rate, 3),
            'loss_rate': round(loss_rate, 3),
            'net_score': round(net_score, 3),
        }

    return factor_stats


def optimize_weights():
    """主优化流程"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 权重自迭代优化器启动...")

    # 加载当前权重
    current_weights = load_adaptive_weights()

    # 分析因子表现
    factor_stats = analyze_factor_performance()
    if not factor_stats:
        print("  无回测数据，跳过优化")
        return current_weights

    print(f"  分析了 {sum(s['win_count']+s['loss_count'] for s in factor_stats.values())} 条因子记录")

    # 调整权重
    new_weights = current_weights.copy()
    adjustments = {}

    for fname, stats in factor_stats.items():
        if fname not in new_weights:
            continue

        net = stats['net_score']
        old_w = new_weights[fname]

        # 净预测力 > 0 → 上调，< 0 → 下调
        delta = net * LEARNING_RATE * old_w  # 按比例调整
        new_w = old_w + delta

        # 限制范围
        new_w = max(WEIGHT_MIN, min(WEIGHT_MAX, new_w))
        new_weights[fname] = round(new_w, 1)

        if abs(delta) > 0.1:
            adjustments[fname] = {
                'old': old_w, 'new': round(new_w, 1), 'delta': round(delta, 1),
                'net_score': net, 'win_rate': stats['win_rate'],
            }

    # 保存
    save_adaptive_weights(new_weights)

    # 打印调整详情
    if adjustments:
        print(f"\n  权重调整详情:")
        for fname, adj in sorted(adjustments.items(), key=lambda x: -abs(x[1]['delta'])):
            direction = "↑" if adj['delta'] > 0 else "↓"
            print(f"    {direction} {fname}: {adj['old']} → {adj['new']} ({adj['delta']:+.1f}) "
                  f"[赢率:{adj['win_rate']:.0%} 净:{adj['net_score']:+.2f}]")
    else:
        print("  本次无显著调整")

    print(f"\n  最终权重: {json.dumps(new_weights, ensure_ascii=False)}")
    return new_weights


def get_current_weights():
    """供screener调用，返回当前最优权重"""
    return load_adaptive_weights()


if __name__ == '__main__':
    optimize_weights()
