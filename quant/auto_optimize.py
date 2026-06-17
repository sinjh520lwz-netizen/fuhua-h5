#!/usr/bin/env python3
"""
自动迭代优化脚本 - auto_optimize.py
功能：
1. 读取最近N天的复盘结果
2. 分析因子表现
3. 动态调整因子权重
4. 调整评分阈值和止损线
5. 记录优化日志
"""
import json, os, sys, logging
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
VERSION_FILE = os.path.join(SCRIPT_DIR, 'version.json')
os.makedirs(DATA_DIR, exist_ok=True)

WEIGHTS_FILE = os.path.join(DATA_DIR, 'adaptive_weights.json')
WEIGHT_HISTORY_FILE = os.path.join(DATA_DIR, 'weight_history.json')
OPTIMIZE_LOG_FILE = os.path.join(DATA_DIR, 'optimize_log.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
PERFORMANCE_FILE = os.path.join(DATA_DIR, 'performance_summary.json')

# 基准权重（参考 screener.py 中的默认值）
BASE_WEIGHTS = {
    'ma_convergence': 10,  # 均线粘合
    'MACD': 15,            # MACD信号
    'RSI': 13,             # RSI信号
    '布林收窄': 3,         # 布林收窄
    '放量': 3,             # 放量
    '站上均线': 4,         # 站上均线
    '趋势': 12,            # 趋势
    '突破位置': 7,         # 突破位置
    '涨幅控制': 10,        # 涨幅控制
    '均线多头': 5,         # 均线多头
}

# 权重约束
WEIGHT_MIN = 2.0
WEIGHT_MAX = 25.0
MAX_ADJUST_PER_DAY = 2.0  # 单日最大调整幅度

# 学习率
LEARNING_RATE = 0.15

# 阈值参数
DEFAULT_SCORE_THRESHOLD = 70
DEFAULT_STOP_LOSS = -6
DEFAULT_TAKE_PROFIT = 5

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('auto_optimize')


def load_adaptive_weights():
    """加载当前自适应权重"""
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE) as f:
            return json.load(f)
    return BASE_WEIGHTS.copy()


def save_adaptive_weights(weights):
    """保存权重并记录历史"""
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(weights, f, indent=2, ensure_ascii=False)

    # 追加历史
    try:
        with open(WEIGHT_HISTORY_FILE) as f:
            wh = json.load(f)
    except:
        wh = []

    today = datetime.now().strftime('%Y-%m-%d')
    wh = [w for w in wh if w.get('date') != today]
    wh.append({
        'date': today,
        'weights': weights,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    wh = wh[-60:]
    with open(WEIGHT_HISTORY_FILE, 'w') as f:
        json.dump(wh, f, indent=2, ensure_ascii=False)


def load_recent_reviews(days=5):
    """加载最近N天的复盘结果"""
    reviews = []
    for i in range(days):
        d = datetime.now() - timedelta(days=i)
        fname = os.path.join(DATA_DIR, f"daily_review_{d.strftime('%Y%m%d')}.json")
        if os.path.exists(fname):
            with open(fname) as f:
                reviews.append(json.load(f))
    return reviews


def load_performance_history():
    """加载history.json中的performance记录"""
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE) as f:
        history = json.load(f)
    return history.get('performance', [])


def analyze_factor_trends(reviews):
    """
    分析因子在多日复盘中的表现趋势
    返回: {factor: {win_rate, avg_return, count, trend}}
    """
    factor_data = {}
    for review in reviews:
        fp = review.get('factor_performance', {})
        for fname, stats in fp.items():
            if fname not in factor_data:
                factor_data[fname] = {'win_rates': [], 'avg_returns': [], 'counts': []}
            factor_data[fname]['win_rates'].append(stats.get('win_rate', 50))
            factor_data[fname]['avg_returns'].append(stats.get('avg_return', 0))
            factor_data[fname]['counts'].append(stats.get('count', 0))

    result = {}
    for fname, data in factor_data.items():
        n = len(data['win_rates'])
        avg_wr = sum(data['win_rates']) / n
        avg_ret = sum(data['avg_returns']) / n
        total_count = sum(data['counts'])

        # 趋势：最近的胜率 vs 平均胜率
        recent_wr = data['win_rates'][0] if data['win_rates'] else 50
        trend = recent_wr - avg_wr  # >0 上升，<0 下降

        result[fname] = {
            'win_rate': round(avg_wr, 1),
            'avg_return': round(avg_ret, 2),
            'count': total_count,
            'trend': round(trend, 1),
            'sample_days': n,
        }

    return result


def optimize_weights(factor_trends, current_weights):
    """
    根据因子趋势调整权重
    """
    new_weights = current_weights.copy()
    adjustments = {}

    for fname, trend_data in factor_trends.items():
        if fname not in new_weights:
            # 尝试匹配（如 "JH.MACD" → "MACD"）
            clean_name = fname.replace('JH.', '').replace('A.', '')
            if clean_name in new_weights:
                fname = clean_name
            else:
                continue

        if trend_data['count'] < 2:
            continue  # 样本太少，不调整

        old_w = new_weights[fname]
        win_rate = trend_data['win_rate']
        trend = trend_data['trend']

        # 计算调整量
        # 胜率 > 55% → 上调 | 胜率 < 45% → 下调
        if win_rate >= 65:
            delta = +2.0
        elif win_rate >= 55:
            delta = +1.0
        elif win_rate >= 45:
            delta = 0
        elif win_rate >= 35:
            delta = -1.0
        else:
            delta = -2.0

        # 趋势修正：上升趋势额外+0.5，下降趋势额外-0.5
        if trend > 5:
            delta += 0.5
        elif trend < -5:
            delta -= 0.5

        # 限制单日调整幅度
        delta = max(-MAX_ADJUST_PER_DAY, min(MAX_ADJUST_PER_DAY, delta))

        if abs(delta) < 0.3:
            continue

        new_w = old_w + delta
        new_w = max(WEIGHT_MIN, min(WEIGHT_MAX, new_w))
        new_w = round(new_w, 1)

        new_weights[fname] = new_w
        adjustments[fname] = {
            'old': old_w, 'new': new_w, 'delta': round(delta, 1),
            'win_rate': win_rate, 'trend': trend,
            'reason': f"胜率{win_rate:.0f}% 趋势{trend:+.1f}",
        }

    return new_weights, adjustments


def normalize_weights(weights):
    """归一化权重使总和接近100%（允许±5%浮动）"""
    total = sum(weights.values())
    if total == 0:
        return weights
    # 如果偏差太大，归一化
    if abs(total - 100) > 10:
        factor = 100 / total
        return {k: round(max(WEIGHT_MIN, min(WEIGHT_MAX, v * factor)), 1)
                for k, v in weights.items()}
    return weights


def optimize_thresholds(reviews):
    """
    根据整体胜率调整阈值和止损线
    返回: {score_threshold, stop_loss, take_profit, adjustments}
    """
    if not reviews:
        return {'score_threshold': DEFAULT_SCORE_THRESHOLD,
                'stop_loss': DEFAULT_STOP_LOSS,
                'take_profit': DEFAULT_TAKE_PROFIT,
                'adjustments': []}

    # 计算最近N天的整体胜率
    all_returns = []
    all_stocks = []
    for review in reviews:
        for s in review.get('stocks', []):
            if s.get('return_pct') is not None:
                all_returns.append(s['return_pct'])
                all_stocks.append(s)

    if not all_returns:
        return {'score_threshold': DEFAULT_SCORE_THRESHOLD,
                'stop_loss': DEFAULT_STOP_LOSS,
                'take_profit': DEFAULT_TAKE_PROFIT,
                'adjustments': []}

    total = len(all_returns)
    wins = sum(1 for r in all_returns if r > 0)
    stop_losses = sum(1 for s in all_stocks if s.get('stop_loss', False))
    overall_win_rate = wins / total * 100
    stop_loss_rate = stop_losses / total * 100
    avg_return = sum(all_returns) / total

    # 加载当前配置
    config = load_performance_config()
    score_threshold = config.get('score_threshold', DEFAULT_SCORE_THRESHOLD)
    stop_loss = config.get('stop_loss', DEFAULT_STOP_LOSS)
    take_profit = config.get('take_profit', DEFAULT_TAKE_PROFIT)

    adjustments = []

    # 阈值调整
    if overall_win_rate < 40:
        # 胜率很低，大幅提高门槛
        delta = +3
        adjustments.append(f"胜率{overall_win_rate:.0f}%过低 → 阈值+3")
    elif overall_win_rate < 48:
        delta = +2
        adjustments.append(f"胜率{overall_win_rate:.0f}%偏低 → 阈值+2")
    elif overall_win_rate > 65:
        delta = -2
        adjustments.append(f"胜率{overall_win_rate:.0f}%优秀 → 阈值-2")
    elif overall_win_rate > 58:
        delta = -1
        adjustments.append(f"胜率{overall_win_rate:.0f}%良好 → 阈值-1")
    else:
        delta = 0

    score_threshold = max(60, min(85, score_threshold + delta))

    # 止损调整
    if stop_loss_rate > 30:
        stop_loss -= 1  # 放宽止损线
        adjustments.append(f"止损率{stop_loss_rate:.0f}%过高 → 止损线放宽至{stop_loss}%")
    elif stop_loss_rate < 10 and stop_loss < -4:
        stop_loss += 1  # 收紧止损线
        adjustments.append(f"止损率{stop_loss_rate:.0f}%极低 → 止损线收紧至{stop_loss}%")

    stop_loss = max(-10, min(-3, stop_loss))

    return {
        'score_threshold': score_threshold,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'overall_win_rate': round(overall_win_rate, 1),
        'stop_loss_rate': round(stop_loss_rate, 1),
        'avg_return': round(avg_return, 2),
        'sample_count': total,
        'adjustments': adjustments,
    }


def load_performance_config():
    """加载当前阈值配置"""
    if os.path.exists(PERFORMANCE_FILE):
        with open(PERFORMANCE_FILE) as f:
            return json.load(f)
    return {
        'score_threshold': DEFAULT_SCORE_THRESHOLD,
        'stop_loss': DEFAULT_STOP_LOSS,
        'take_profit': DEFAULT_TAKE_PROFIT,
    }


def save_performance_config(config):
    """保存阈值配置"""
    config['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(PERFORMANCE_FILE, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def run_optimization(lookback_days=5):
    """
    主优化流程
    """
    now = datetime.now()
    log.info(f"===== 自动迭代优化 {now.strftime('%Y-%m-%d %H:%M')} =====")

    # 1. 加载最近复盘数据
    reviews = load_recent_reviews(lookback_days)
    log.info(f"加载了 {len(reviews)} 天复盘数据")

    if not reviews:
        log.warning("无复盘数据，跳过优化")
        return None

    # 2. 加载当前权重
    current_weights = load_adaptive_weights()
    log.info(f"当前权重: {json.dumps(current_weights, ensure_ascii=False)}")

    # 3. 分析因子趋势
    factor_trends = analyze_factor_trends(reviews)
    log.info(f"分析了 {len(factor_trends)} 个因子")

    # 4. 优化权重
    new_weights, weight_adjustments = optimize_weights(factor_trends, current_weights)
    new_weights = normalize_weights(new_weights)

    # 5. 优化阈值
    threshold_result = optimize_thresholds(reviews)

    # 6. 保存权重
    save_adaptive_weights(new_weights)

    # 7. 保存阈值配置
    config = load_performance_config()
    config['score_threshold'] = threshold_result['score_threshold']
    config['stop_loss'] = threshold_result['stop_loss']
    config['take_profit'] = threshold_result['take_profit']
    save_performance_config(config)

    # 8. 记录优化日志
    log_entry = {
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M:%S'),
        'type': 'optimize',
        'lookback_days': lookback_days,
        'previous_weights': current_weights,
        'new_weights': new_weights,
        'weight_adjustments': weight_adjustments,
        'threshold_result': threshold_result,
        'factor_trends': {
            k: {'win_rate': v['win_rate'], 'avg_return': v['avg_return'],
                'count': v['count'], 'trend': v['trend']}
            for k, v in factor_trends.items()
        },
    }

    try:
        with open(OPTIMIZE_LOG_FILE) as f:
            opt_log = json.load(f)
    except:
        opt_log = []
    opt_log.append(log_entry)
    opt_log = opt_log[-60:]
    with open(OPTIMIZE_LOG_FILE, 'w') as f:
        json.dump(opt_log, f, indent=2, ensure_ascii=False)

    # 9. 打印优化结果
    log.info(f"\n===== 优化结果 =====")

    if weight_adjustments:
        log.info(f"\n  权重调整 ({len(weight_adjustments)} 项):")
        for fname, adj in sorted(weight_adjustments.items(), key=lambda x: -abs(x[1]['delta'])):
            direction = "↑" if adj['delta'] > 0 else "↓"
            log.info(f"    {direction} {fname}: {adj['old']} → {adj['new']} ({adj['delta']:+.1f}) [{adj['reason']}]")
    else:
        log.info("  权重无显著变化")

    log.info(f"\n  阈值调整:")
    log.info(f"    评分阈值: {threshold_result['score_threshold']}")
    log.info(f"    止损线: {threshold_result['stop_loss']}%")
    log.info(f"    整体胜率: {threshold_result['overall_win_rate']}%")
    log.info(f"    止损率: {threshold_result['stop_loss_rate']}%")
    for adj in threshold_result['adjustments']:
        log.info(f"    → {adj}")

    log.info(f"\n  最终权重: {json.dumps(new_weights, ensure_ascii=False)}")

    # 10. 自迭代：版本号+1
    version_info = bump_version()
    log.info(f"  版本: {version_info.get('version','?')} 迭代{version_info.get('iteration','?')}次")

    return {
        'weights': new_weights,
        'weight_adjustments': weight_adjustments,
        'threshold_result': threshold_result,
        'factor_trends': factor_trends,
    }


def bump_version(force_bump=False):
    """自迭代后版本号+1，每10次迭代升一次minor版本"""
    try:
        with open(VERSION_FILE) as f:
            v = json.load(f)
    except:
        v = {"major": 2, "minor": 5, "iteration": 0}
    v['iteration'] = v.get('iteration', 0) + 1
    if v['iteration'] >= 10 or force_bump:
        v['minor'] = v.get('minor', 5) + 1
        v['iteration'] = 0
    major, minor = v.get('major', 2), v.get('minor', 5)
    v['version'] = f"v{major}.{minor}"
    v['full_name'] = f"JH Screener v{major}.{minor}"
    v['strategy'] = f"埋伏策略v{major}.{minor}（11因子评分，AI仅在关键时点运行）"
    v['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(VERSION_FILE, 'w') as f:
        json.dump(v, f, indent=2, ensure_ascii=False)
    log.info(f"版本已升级: v{major}.{minor-1} → v{major}.{minor}（迭代{v.get('iteration', '?')}次）" if v['iteration'] == 0 else f"版本: v{major}.{minor} 迭代{v.get('iteration', '?')}次")
    return v


def get_current_config():
    """供外部调用：获取当前最优配置"""
    return {
        'weights': load_adaptive_weights(),
        'thresholds': load_performance_config(),
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='自动迭代优化')
    parser.add_argument('--days', type=int, default=5, help='回看天数')
    args = parser.parse_args()
    result = run_optimization(lookback_days=args.days)
    if result:
        print(f"\n优化完成")
    else:
        print("\n无数据可优化")
