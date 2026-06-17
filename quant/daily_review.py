#!/usr/bin/env python3
"""
每日复盘脚本 - daily_review.py
功能：
1. 读取当天推荐数据
2. 获取推荐股票的收盘价和涨跌幅
3. 计算T+0收益（推荐价 vs 收盘价）
4. 统计胜率、平均收益
5. 保存复盘结果到 data/daily_review_YYYYMMDD.json
6. 生成Markdown日报
"""
import json, os, sys, time, logging
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
REPORT_DIR = os.path.join(DATA_DIR, 'review_reports')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

RECOMMENDATIONS_FILE = os.path.join(DATA_DIR, 'recommendations.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'history.json')
OPTIMIZE_LOG_FILE = os.path.join(DATA_DIR, 'optimize_log.json')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger('daily_review')


def fetch_realtime(code):
    """腾讯实时行情 - 获取收盘价"""
    import urllib.request
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = urllib.request.urlopen(req, timeout=10).read().decode('gbk')
        vals = data.split('"')[1].split('~')
        return {
            'name': vals[1],
            'price': float(vals[3]),
            'prev_close': float(vals[4]),
            'change': float(vals[32]),
            'high': float(vals[33]),
            'low': float(vals[34]),
            'volume': float(vals[36]) if vals[36] else 0,
            'amount': float(vals[37]) if vals[37] else 0,
        }
    except Exception as e:
        log.warning(f"获取 {code} 行情失败: {e}")
        return None


def load_recommendations():
    """加载当天推荐数据"""
    if not os.path.exists(RECOMMENDATIONS_FILE):
        log.error(f"推荐文件不存在: {RECOMMENDATIONS_FILE}")
        return None
    with open(RECOMMENDATIONS_FILE) as f:
        return json.load(f)


def load_history_recommendations(date_str=None):
    """从history.json加载指定日期的推荐"""
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE) as f:
        history = json.load(f)
    if date_str:
        return [r for r in history.get('recommendations', []) if r.get('date') == date_str]
    return history.get('recommendations', [])


def run_daily_review(target_date=None):
    """
    执行每日复盘
    target_date: 'YYYY-MM-DD' 格式，默认今天
    """
    now = datetime.now()
    today = target_date or now.strftime('%Y-%m-%d')
    today_compact = today.replace('-', '')

    log.info(f"===== 每日复盘 {today} =====")

    # 1. 加载推荐数据
    rec_data = load_recommendations()
    if not rec_data:
        log.error("无推荐数据，复盘终止")
        return None

    rec_date = rec_data.get('date', today)
    top_picks = rec_data.get('top_picks', [])
    all_stocks = rec_data.get('all_stocks', [])

    if not top_picks:
        log.warning("无推荐股票")
        return None

    log.info(f"推荐日期: {rec_date}, 推荐 {len(top_picks)} 只")

    # 2. 获取每只推荐股票的收盘数据
    reviewed = []
    for stock in top_picks:
        code = stock['code']
        name = stock['name']
        entry_price = stock['price']  # 推荐时的价格（买入价）
        score = stock.get('score', 0)
        factors = stock.get('factors', {})
        signals = stock.get('signals', [])

        log.info(f"  复盘 {name}({code}) 推荐价:{entry_price}")

        rt = fetch_realtime(code)
        time.sleep(0.15)  # 限速

        if not rt:
            log.warning(f"  {name}({code}) 行情获取失败，跳过")
            reviewed.append({
                'code': code, 'name': name,
                'entry_price': entry_price,
                'close_price': None,
                'return_pct': None,
                'status': 'data_error',
                'score': score, 'factors': factors, 'signals': signals,
            })
            continue

        close_price = rt['price']
        # T+0收益 = (收盘价 - 买入价) / 买入价 * 100
        t0_return = (close_price - entry_price) / entry_price * 100 if entry_price > 0 else 0

        # 判定胜负
        is_win = t0_return > 0
        stop_loss = t0_return <= -6  # 止损线-6%
        take_profit = t0_return >= 5  # 止盈线+5%

        reviewed.append({
            'code': code,
            'name': name,
            'entry_price': entry_price,
            'close_price': close_price,
            'return_pct': round(t0_return, 2),
            'is_win': is_win,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'status': 'stop_loss' if stop_loss else ('take_profit' if take_profit else ('win' if is_win else 'loss')),
            'score': score,
            'factors': factors,
            'signals': signals,
            'change_today': rt.get('change', 0),
        })

        emoji = "🟢" if is_win else "🔴"
        log.info(f"  {emoji} {name}: {entry_price} → {close_price} ({t0_return:+.2f}%)")

    # 3. 统计
    valid = [r for r in reviewed if r['close_price'] is not None]
    if not valid:
        log.error("无有效复盘数据")
        return None

    total = len(valid)
    wins = sum(1 for r in valid if r['is_win'])
    losses = total - wins
    stop_loss_count = sum(1 for r in valid if r['stop_loss'])
    take_profit_count = sum(1 for r in valid if r['take_profit'])
    avg_return = sum(r['return_pct'] for r in valid) / total
    max_return = max(r['return_pct'] for r in valid)
    min_return = min(r['return_pct'] for r in valid)
    win_rate = wins / total * 100

    # 因子分析：哪些因子对应的股票赢/输
    factor_performance = {}
    for r in valid:
        for fname, fval in r.get('factors', {}).items():
            if fname not in factor_performance:
                factor_performance[fname] = {'wins': 0, 'losses': 0, 'total_return': 0, 'count': 0}
            fp = factor_performance[fname]
            fp['count'] += 1
            fp['total_return'] += r['return_pct']
            if r['is_win']:
                fp['wins'] += 1
            else:
                fp['losses'] += 1

    for fname, fp in factor_performance.items():
        fp['win_rate'] = round(fp['wins'] / fp['count'] * 100, 1) if fp['count'] > 0 else 0
        fp['avg_return'] = round(fp['total_return'] / fp['count'], 2) if fp['count'] > 0 else 0

    stats = {
        'total': total,
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 2),
        'max_return': round(max_return, 2),
        'min_return': round(min_return, 2),
        'stop_loss_count': stop_loss_count,
        'stop_loss_rate': round(stop_loss_count / total * 100, 1),
        'take_profit_count': take_profit_count,
    }

    log.info(f"\n===== 复盘统计 =====")
    log.info(f"  总数: {total} | 胜: {wins} | 负: {losses}")
    log.info(f"  胜率: {win_rate:.1f}% | 平均收益: {avg_return:+.2f}%")
    log.info(f"  最大盈利: {max_return:+.2f}% | 最大亏损: {min_return:+.2f}%")
    log.info(f"  止损: {stop_loss_count}只 | 止盈: {take_profit_count}只")

    # 4. 保存复盘结果
    review_result = {
        'review_date': today,
        'recommendation_date': rec_date,
        'review_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'market_change': rec_data.get('market_change', 0),
        'stats': stats,
        'factor_performance': factor_performance,
        'stocks': reviewed,
    }

    review_file = os.path.join(DATA_DIR, f'daily_review_{today_compact}.json')
    with open(review_file, 'w') as f:
        json.dump(review_result, f, indent=2, ensure_ascii=False)
    log.info(f"复盘结果已保存: {review_file}")

    # 5. 更新 history.json 中的 performance 字段
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except:
        history = {'recommendations': [], 'performance': []}

    perf_entry = {
        'date': today,
        'recommendation_date': rec_date,
        'stats': stats,
        'stocks': [{
            'code': r['code'], 'name': r['name'],
            'entry_price': r['entry_price'],
            'close_price': r['close_price'],
            'return_pct': r['return_pct'],
            'is_win': r['is_win'],
        } for r in valid],
    }
    history.setdefault('performance', [])
    # 移除同日旧记录
    history['performance'] = [p for p in history['performance'] if p.get('date') != today]
    history['performance'].append(perf_entry)
    # 只保留30天
    history['performance'] = history['performance'][-30:]
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    # 6. 追加优化日志
    try:
        with open(OPTIMIZE_LOG_FILE) as f:
            opt_log = json.load(f)
    except:
        opt_log = []

    opt_log.append({
        'date': today,
        'type': 'review',
        'stats': stats,
        'factor_performance': {
            k: {'win_rate': v['win_rate'], 'avg_return': v['avg_return'], 'count': v['count']}
            for k, v in factor_performance.items()
        },
    })
    opt_log = opt_log[-60:]
    with open(OPTIMIZE_LOG_FILE, 'w') as f:
        json.dump(opt_log, f, indent=2, ensure_ascii=False)

    # 7. 生成Markdown日报
    report_md = generate_review_report(today, rec_date, stats, reviewed, factor_performance, rec_data)
    report_file = os.path.join(REPORT_DIR, f'review_{today_compact}.md')
    with open(report_file, 'w') as f:
        f.write(report_md)
    log.info(f"日报已保存: {report_file}")

    return review_result


def generate_review_report(today, rec_date, stats, stocks, factor_perf, rec_data):
    """生成Markdown格式复盘日报"""
    lines = []
    lines.append(f"# 📊 每日复盘日报 {today}")
    lines.append(f"")
    lines.append(f"> 推荐日期: {rec_date} | 复盘时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append(f"> 大盘涨跌: {rec_data.get('market_change', 0):+.2f}%")
    lines.append(f"")

    # 整体统计
    lines.append(f"## 📈 整体统计")
    lines.append(f"")
    emoji = "🟢" if stats['win_rate'] >= 50 else "🔴"
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 推荐总数 | {stats['total']} |")
    lines.append(f"| {emoji} 胜率 | **{stats['win_rate']:.1f}%** ({stats['wins']}/{stats['total']}) |")
    lines.append(f"| 平均收益 | {stats['avg_return']:+.2f}% |")
    lines.append(f"| 最大盈利 | {stats['max_return']:+.2f}% |")
    lines.append(f"| 最大亏损 | {stats['min_return']:+.2f}% |")
    lines.append(f"| 止损次数 | {stats['stop_loss_count']} ({stats['stop_loss_rate']:.1f}%) |")
    lines.append(f"| 止盈次数 | {stats['take_profit_count']} |")
    lines.append(f"")

    # 个股明细
    lines.append(f"## 📋 个股明细")
    lines.append(f"")
    lines.append(f"| 排名 | 股票 | 代码 | 评分 | 买入价 | 收盘价 | 收益 | 状态 |")
    lines.append(f"|------|------|------|------|--------|--------|------|------|")
    for i, s in enumerate(stocks):
        if s['close_price'] is None:
            lines.append(f"| {i+1} | {s['name']} | {s['code']} | {s['score']} | {s['entry_price']} | N/A | N/A | ⚠️ 数据异常 |")
            continue
        ret = s['return_pct']
        if s['stop_loss']:
            status = "🛑 止损"
        elif s['take_profit']:
            status = "🎯 止盈"
        elif s['is_win']:
            status = "🟢 盈利"
        else:
            status = "🔴 亏损"
        lines.append(f"| {i+1} | {s['name']} | {s['code']} | {s['score']} | {s['entry_price']} | {s['close_price']} | {ret:+.2f}% | {status} |")
    lines.append(f"")

    # 因子分析
    if factor_perf:
        lines.append(f"## 🔍 因子表现分析")
        lines.append(f"")
        lines.append(f"| 因子 | 出现次数 | 胜率 | 平均收益 | 评价 |")
        lines.append(f"|------|----------|------|----------|------|")
        sorted_factors = sorted(factor_perf.items(), key=lambda x: -x[1]['win_rate'])
        for fname, fp in sorted_factors:
            if fp['win_rate'] >= 60:
                eval_emoji = "⭐ 优秀"
            elif fp['win_rate'] >= 50:
                eval_emoji = "✅ 良好"
            elif fp['win_rate'] >= 40:
                eval_emoji = "⚠️ 一般"
            else:
                eval_emoji = "❌ 差"
            lines.append(f"| {fname} | {fp['count']} | {fp['win_rate']:.1f}% | {fp['avg_return']:+.2f}% | {eval_emoji} |")
        lines.append(f"")

    # 策略建议
    lines.append(f"## 💡 策略建议")
    lines.append(f"")
    if stats['win_rate'] >= 60:
        lines.append(f"- ✅ 胜率{stats['win_rate']:.1f}%表现优秀，可适当放宽准入门槛")
    elif stats['win_rate'] >= 50:
        lines.append(f"- ⚠️ 胜率{stats['win_rate']:.1f}%尚可，继续观察")
    else:
        lines.append(f"- 🔴 胜率{stats['win_rate']:.1f}%偏低，需提高门槛或优化因子权重")

    if stats['stop_loss_rate'] > 30:
        lines.append(f"- 🔴 止损率{stats['stop_loss_rate']:.1f}%过高，建议放宽止损线")
    elif stats['stop_loss_rate'] < 10:
        lines.append(f"- ✅ 止损率{stats['stop_loss_rate']:.1f}%可控")

    if stats['avg_return'] < 0:
        lines.append(f"- ⚠️ 平均收益为负({stats['avg_return']:+.2f}%)，建议减少推荐数量")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*自动生成 by JH量化系统 v2.2*")

    return '\n'.join(lines)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='每日复盘')
    parser.add_argument('--date', type=str, default=None, help='复盘日期 YYYY-MM-DD')
    args = parser.parse_args()
    result = run_daily_review(target_date=args.date)
    if result:
        print(f"\n复盘完成: 胜率{result['stats']['win_rate']:.1f}% 平均收益{result['stats']['avg_return']:+.2f}%")
    else:
        print("\n复盘失败或无数据")
