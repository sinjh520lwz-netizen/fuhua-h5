#!/usr/bin/env python3
"""
JH 选股策略回测 v2.0
改进：小数评分 + 止损止盈标签 + 大盘过滤 + 去重
"""
import json, os, sys, math, time
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')

VERSION_FILE = os.path.join(SCRIPT_DIR, 'version.json')
try:
    with open(VERSION_FILE) as f:
        _VER = json.load(f)
        BT_VERSION = _VER.get('version', 'v2.5')
except:
    BT_VERSION = 'v2.5'
from screener import fetch_klines, quick_analyze, score_early_entry

def get_trading_dates(klines, n=25):
    dates = [k['date'] for k in klines]
    return dates[-n:]

def simulate_day(klines, target_date, market_change=0):
    dates = [k['date'] for k in klines]
    if target_date not in dates:
        return None
    idx = dates.index(target_date)
    if idx < 30:
        return None

    hist_klines = klines[:idx+1]
    ind = quick_analyze(hist_klines)
    if not ind:
        return None

    day_open = klines[idx]['open']
    day_close = klines[idx]['close']
    day_change = (day_close / day_open - 1) * 100

    score, factors = score_early_entry(ind, day_change, market_change)

    future = klines[idx+1:]
    result = {
        'date': target_date, 'score': score,
        'entry_price': day_close, 'change': round(day_change, 2),
        'factors': factors,
    }

    if len(future) >= 1:
        result['t1_price'] = future[0]['close']
        result['t1_return'] = round((future[0]['close'] / day_close - 1) * 100, 2)
    if len(future) >= 2:
        result['t2_price'] = future[1]['close']
        result['t2_return'] = round((future[1]['close'] / day_close - 1) * 100, 2)
    if len(future) >= 3:
        result['t3_price'] = future[2]['close']
        result['t3_return'] = round((future[2]['close'] / day_close - 1) * 100, 2)
    if len(future) >= 5:
        result['t5_price'] = future[4]['close']
        result['t5_return'] = round((future[4]['close'] / day_close - 1) * 100, 2)

    # 逐日收益（用于走势图，最多15天）
    result['daily_returns'] = []
    result['future_dates'] = []
    for f in future[:15]:
        result['daily_returns'].append(round((f['close'] / day_close - 1) * 100, 2))
        result['future_dates'].append(f['date'])

    if future:
        max_high = max(k['high'] for k in future[:15])
        min_low = min(k['low'] for k in future[:15])
        result['max_return'] = round((max_high / day_close - 1) * 100, 2)
        result['min_return'] = round((min_low / day_close - 1) * 100, 2)

    return result


def classify_trade(result):
    """止损/止盈/持有分类"""
    daily = result.get('daily_returns', [])
    if not daily:
        return 'hold', ''

    # 止损：任一天跌>6%（从5%放宽到6%）
    for i, r in enumerate(daily):
        if r <= -6:
            return 'stop_loss', f'T+{i+1}跌{r:.1f}%'

    # 止盈：任一天涨>5%
    for i, r in enumerate(daily):
        if r >= 5:
            return 'take_profit', f'T+{i+1}涨{r:.1f}%'

    # 最终收益
    t3 = result.get('t3_return') or result.get('t1_return', 0)
    if t3 > 3:
        return 'profit', f'收益{t3:.1f}%'
    elif t3 < -2:
        return 'loss', f'亏损{t3:.1f}%'
    else:
        return 'hold', f'持平{t3:.1f}%'


def run_backtest():
    print("=" * 65)
    print("  JH 选股策略回测 v2.0 — 15个交易日")
    print("  热门股池")
    print("=" * 65)

    hot_file = os.path.join(os.path.dirname(__file__), 'data', 'ths_hot_list.json')
    if not os.path.exists(hot_file):
        print("[ERROR] ths_hot_list.json 不存在")
        return
    with open(hot_file) as f:
        data = json.load(f)
    codes = [(str(s['code']), s['name']) for s in data.get('data', {}).get('stock_list', [])
             if 'ST' not in s.get('name', '')]
    print(f"\n标的池: {len(codes)}只（热门股） | 拉取K线...")

    all_klines = {}
    for i, (code, name) in enumerate(codes):
        klines = fetch_klines(code, 120)
        if len(klines) >= 40:
            all_klines[code] = {'name': name, 'klines': klines}
        if (i + 1) % 20 == 0:
            print(f"  已加载 {i+1}/{len(codes)}...")
            time.sleep(0.3)

    print(f"有效标的: {len(all_klines)}只")

    sample_klines = list(all_klines.values())[0]['klines']
    trading_dates = get_trading_dates(sample_klines, 90)
    backtest_dates = trading_dates[-60:]

    print(f"回测日期: {backtest_dates[0]} ~ {backtest_dates[-1]}")
    print(f"\n{'=' * 65}")

    all_results = []
    daily_stats = []
    recent_codes = set()  # 7天去重

    for date in backtest_dates:
        day_picks = []
        for code, info in all_klines.items():
            if code in recent_codes:
                continue
            result = simulate_day(info['klines'], date)
            if result and result['score'] >= 40:
                result['code'] = code
                result['name'] = info['name']
                day_picks.append(result)

        day_picks.sort(key=lambda x: x['score'], reverse=True)
        top5 = day_picks[:5]

        # 更新去重集合
        for p in top5:
            recent_codes.add(p['code'])
        # 移除超过7天的
        if len(recent_codes) > 35:
            recent_codes = set(list(recent_codes)[-35:])

        if top5:
            t1_returns = [p.get('t1_return', 0) for p in top5 if 't1_return' in p]
            t3_returns = [p.get('t3_return', 0) for p in top5 if 't3_return' in p]
            t5_returns = [p.get('t5_return', 0) for p in top5 if 't5_return' in p]
            f15_returns = []
            for p in top5:
                dr = p.get('daily_returns', [])
                f15_returns.append(dr[-1] if dr else None)
            f15_returns = [r for r in f15_returns if r is not None]

            max_returns = [p.get('max_return', 0) for p in top5 if 'max_return' in p]
            t1_avg = np.mean(t1_returns) if t1_returns else 0
            t3_avg = np.mean(t3_returns) if t3_returns else 0
            t5_avg = np.mean(t5_returns) if t5_returns else 0
            f15_avg = np.mean(f15_returns) if f15_returns else 0
            t1_win = len([r for r in t1_returns if r > 0])
            t3_win = len([r for r in t3_returns if r > 0])
            t5_win = len([r for r in t5_returns if r > 0])
            f15_win = len([r for r in f15_returns if r > 0])

            # 止损止盈统计
            stop_losses = sum(1 for p in top5 if classify_trade(p)[0] == 'stop_loss')
            take_profits = sum(1 for p in top5 if classify_trade(p)[0] == 'take_profit')

            stat = {
                'date': date, 'picks_count': len(top5),
                't1_avg_return': round(t1_avg, 2),
                't3_avg_return': round(t3_avg, 2),
                't5_avg_return': round(t5_avg, 2),
                'f15_avg_return': round(f15_avg, 2),
                't1_win_rate': round(t1_win / len(t1_returns) * 100, 1) if t1_returns else 0,
                't3_win_rate': round(t3_win / len(t3_returns) * 100, 1) if t3_returns else 0,
                't5_win_rate': round(t5_win / len(t5_returns) * 100, 1) if t5_returns else 0,
                'f15_win_rate': round(f15_win / len(f15_returns) * 100, 1) if f15_returns else 0,
                'stop_losses': stop_losses, 'take_profits': take_profits,
                'picks': top5,
            }
            daily_stats.append(stat)
            all_results.extend(top5)

            sl_tag = f" 🔴止损{stop_losses}" if stop_losses else ""
            tp_tag = f" 🟢止盈{take_profits}" if take_profits else ""
            t5_show = f"T+5:{t5_avg:+.2f}%" if t5_returns else ""
            f15_show = f"T+15:{f15_avg:+.2f}%" if f15_returns else ""
            print(f"\n📅 {date} | 推荐{len(top5)}只 | T+1:{t1_avg:+.2f}% | T+3:{t3_avg:+.2f}% | {t5_show} | {f15_show}{sl_tag}{tp_tag}")
            for p in top5:
                t1 = f"{p.get('t1_return',0):+.1f}%" if 't1_return' in p else '--'
                t3 = f"{p.get('t3_return',0):+.1f}%" if 't3_return' in p else '--'
                t5 = f"{p.get('t5_return',0):+.1f}%" if 't5_return' in p else '--'
                dr = p.get('daily_returns', [])
                f15 = f"{dr[-1]:+.1f}%" if dr else '--'
                mx = f"{p.get('max_return',0):+.1f}%" if 'max_return' in p else '--'
                cls, cls_tag = classify_trade(p)
                cls_emoji = {'stop_loss': '🔴', 'take_profit': '🟢', 'profit': '✅', 'loss': '❌', 'hold': '⚪'}.get(cls, '')
                print(f"  {p['name']:8s}({p['code']}) 分:{p['score']:5.1f} 入:{p['entry_price']:.2f} T+1:{t1:>7s} T+3:{t3:>7s} T+5:{t5:>7s} T+15:{f15:>7s} {cls_emoji}{cls_tag}")

    # 汇总
    print(f"\n{'=' * 65}")
    print(f"  📊 回测汇总 v2.0")
    print(f"{'=' * 65}")

    if all_results:
        all_t5 = [r.get('t5_return', 0) for r in all_results if 't5_return' in r]
        all_f15 = []
        for r in all_results:
            dr = r.get('daily_returns', [])
            all_f15.append(dr[-1] if dr else None)
        all_f15 = [v for v in all_f15 if v is not None]
        all_t1 = [r.get('t1_return', 0) for r in all_results if 't1_return' in r]
        all_t3 = [r.get('t3_return', 0) for r in all_results if 't3_return' in r]
        all_max = [r.get('max_return', 0) for r in all_results if 'max_return' in r]
        all_min = [r.get('min_return', 0) for r in all_results if 'min_return' in r]

        t1_wins = len([r for r in all_t1 if r > 0])
        t3_wins = len([r for r in all_t3 if r > 0])
        t5_wins = len([r for r in all_t5 if r > 0])
        f15_wins = len([r for r in all_f15 if r > 0])

        print(f"  总推荐: {len(all_results)}只次 | 回测: {len(daily_stats)}天")
        print(f"")
        print(f"  T+1 胜率: {t1_wins}/{len(all_t1)} = {t1_wins/len(all_t1)*100:.1f}% | 均: {np.mean(all_t1):+.2f}%")
        print(f"  T+3 胜率: {t3_wins}/{len(all_t3)} = {t3_wins/len(all_t3)*100:.1f}% | 均: {np.mean(all_t3):+.2f}%")
        print(f"  T+5 胜率: {t5_wins}/{len(all_t5)} = {t5_wins/len(all_t5)*100:.1f}% | 均: {np.mean(all_t5):+.2f}%" if all_t5 else "  T+5: 无数据")
        print(f"  T+15胜率: {f15_wins}/{len(all_f15)} = {f15_wins/len(all_f15)*100:.1f}% | 均: {np.mean(all_f15):+.2f}%" if all_f15 else "  T+15: 无数据")
        print(f"")
        print(f"  5日最高均值: {np.mean(all_max):+.2f}% | 最低均值: {np.mean(all_min):+.2f}%")
        print(f"  5日最高>3%: {len([r for r in all_max if r > 3])}/{len(all_max)} = {len([r for r in all_max if r > 3])/len(all_max)*100:.1f}%")

        # 止损止盈统计
        classifications = [classify_trade(r) for r in all_results]
        stop_count = sum(1 for c, _ in classifications if c == 'stop_loss')
        tp_count = sum(1 for c, _ in classifications if c == 'take_profit')
        profit_count = sum(1 for c, _ in classifications if c == 'profit')
        loss_count = sum(1 for c, _ in classifications if c == 'loss')
        print(f"")
        print(f"  📌 止损止盈统计:")
        print(f"     🔴 触发止损(跌>6%): {stop_count}次 = {stop_count/len(all_results)*100:.1f}%")
        print(f"     🟢 触发止盈(涨>5%): {tp_count}次 = {tp_count/len(all_results)*100:.1f}%")
        print(f"     ✅ 最终盈利(>3%): {profit_count}次 = {profit_count/len(all_results)*100:.1f}%")
        print(f"     ❌ 最终亏损(<-2%): {loss_count}次 = {loss_count/len(all_results)*100:.1f}%")
        print(f"     ⚪ 持平: {len(all_results)-stop_count-tp_count-profit_count-loss_count}次")

        # 按分数段
        print(f"\n  📈 按分数段:")
        for low, high in [(90, 101), (80, 90), (70, 80)]:
            seg = [r for r in all_results if low <= r.get('score', 0) < high and 't1_return' in r]
            if seg:
                seg_t1_wins = len([r for r in seg if r['t1_return'] > 0])
                seg_t1_avg = np.mean([r['t1_return'] for r in seg])
                seg_t3 = [r.get('t3_return') for r in seg if r.get('t3_return') is not None]
                seg_t3_avg = np.mean(seg_t3) if seg_t3 else 0
                print(f"    {low}-{high}分: {len(seg)}只 | T+1胜率:{seg_t1_wins/len(seg)*100:.0f}% | T+1均:{seg_t1_avg:+.2f}% | T+3均:{seg_t3_avg:+.2f}%")

    output = {
        'backtest_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'version': BT_VERSION,
        'period': f'{backtest_dates[0]} ~ {backtest_dates[-1]}',
        'total_picks': len(all_results),
        'daily_stats': daily_stats,
    }
    bt_file = os.path.join(os.path.dirname(__file__), 'data', 'backtest_result.json')
    with open(bt_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output


if __name__ == '__main__':
    run_backtest()
