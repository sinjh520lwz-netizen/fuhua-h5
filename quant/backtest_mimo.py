#!/usr/bin/env python3
"""
JH MiMo 选股策略回测 v2.5
MiMo版：使用screener_mimo的评分逻辑，阈值70分，全A股扫描
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

from screener_mimo import fetch_klines, quick_analyze, score_early_entry

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


def should_exclude(full_code):
    """排除科创板/创业板/北交所 (同screener_mimo.py逻辑)"""
    return (full_code.startswith('sz300') or full_code.startswith('sz301') or 
            full_code.startswith('sh688') or full_code[2:].startswith('920'))


def run_backtest():
    print("=" * 65)
    print("  JH MiMo 选股策略回测 v2.5 — 15个交易日")
    print("  使用MiMo评分逻辑，阈值70分，全A股扫描")
    print("=" * 65)

    # 从全A股列表读取
    list_file = os.path.join(DATA_DIR, 'a_stock_list.json')
    if not os.path.exists(list_file):
        print("[ERROR] a_stock_list.json 不存在")
        return

    with open(list_file) as f:
        all_stocks = json.load(f)

    # 排除ST、科创板/创业板/北交所
    codes = []
    for s in all_stocks:
        fc = s.get('full', '')
        name = s.get('name', '')
        if 'ST' in name:
            continue
        if should_exclude(fc):
            continue
        codes.append((str(s['code']), s['name']))

    print(f"标的池: {len(all_stocks)}只 → 过滤后 {len(codes)}只 | 拉取K线...")

    all_klines = {}
    for i, (code, name) in enumerate(codes):
        klines = fetch_klines(code, 120)
        if len(klines) >= 40:
            all_klines[code] = {'name': name, 'klines': klines}
        if (i + 1) % 100 == 0:
            print(f"  已加载 {i+1}/{len(codes)}...")
        time.sleep(0.02)

    print(f"有效标的: {len(all_klines)}只")

    sample_klines = list(all_klines.values())[0]['klines']
    trading_dates = get_trading_dates(sample_klines, 25)
    backtest_dates = trading_dates[-15:]

    print(f"回测日期: {backtest_dates[0]} ~ {backtest_dates[-1]}")
    print(f"{'=' * 65}")

    all_results = []
    daily_stats = []
    recent_codes = set()  # 7天去重

    for date in backtest_dates:
        day_picks = []
        for code, info in all_klines.items():
            if code in recent_codes:
                continue
            result = simulate_day(info['klines'], date)
            if result and result['score'] >= 70:  # MiMo版阈值70
                result['code'] = code
                result['name'] = info['name']
                day_picks.append(result)

        day_picks.sort(key=lambda x: x['score'], reverse=True)
        top5 = day_picks[:5]

        # 更新去重集合
        for p in top5:
            recent_codes.add(p['code'])
        if len(recent_codes) > 35:
            recent_codes = set(list(recent_codes)[-35:])

        if top5:
            t1_returns = [p.get('t1_return', 0) for p in top5 if 't1_return' in p]
            t3_returns = [p.get('t3_return', 0) for p in top5 if 't3_return' in p]
            max_returns = [p.get('max_return', 0) for p in top5 if 'max_return' in p]
            t1_avg = np.mean(t1_returns) if t1_returns else 0
            t3_avg = np.mean(t3_returns) if t3_returns else 0
            t1_win = len([r for r in t1_returns if r > 0])
            t3_win = len([r for r in t3_returns if r > 0])

            stat = {
                'date': date, 'picks_count': len(top5),
                't1_avg_return': round(t1_avg, 2),
                't3_avg_return': round(t3_avg, 2),
                't1_win_rate': round(t1_win / len(t1_returns) * 100, 1) if t1_returns else 0,
                't3_win_rate': round(t3_win / len(t3_returns) * 100, 1) if t3_returns else 0,
                'picks': top5,
            }
            daily_stats.append(stat)
            all_results.extend(top5)

            print(f"\n📅 {date} | 推荐{len(top5)}只 | T+1:{t1_avg:+.2f}% | T+3:{t3_avg:+.2f}% | T+1胜率:{stat['t1_win_rate']}%")
            for p in top5:
                t1 = f"{p.get('t1_return',0):+.1f}%" if 't1_return' in p else '--'
                t3 = f"{p.get('t3_return',0):+.1f}%" if 't3_return' in p else '--'
                mx = f"{p.get('max_return',0):+.1f}%" if 'max_return' in p else '--'
                print(f"  {p['name']:8s}({p['code']}) 分:{p['score']:5.1f} 入:{p['entry_price']:.2f} T+1:{t1:>7s} T+3:{t3:>7s} 最高:{mx:>7s}")

    # 汇总
    print(f"\n{'=' * 65}")
    print(f"  📊 MiMo回测汇总 v2.5")
    print(f"{'=' * 65}")

    if all_results:
        all_t1 = [r.get('t1_return', 0) for r in all_results if 't1_return' in r]
        all_t3 = [r.get('t3_return', 0) for r in all_results if 't3_return' in r]
        all_max = [r.get('max_return', 0) for r in all_results if 'max_return' in r]

        t1_wins = len([r for r in all_t1 if r > 0])
        t3_wins = len([r for r in all_t3 if r > 0])

        print(f"  总推荐: {len(all_results)}只次 | 回测: {len(daily_stats)}天")
        print(f"  T+1 胜率: {t1_wins}/{len(all_t1)} = {t1_wins/len(all_t1)*100:.1f}%")
        print(f"  T+1 均涨: {np.mean(all_t1):+.2f}%")
        print(f"  T+3 胜率: {t3_wins}/{len(all_t3)} = {t3_wins/len(all_t3)*100:.1f}%")
        print(f"  T+3 均涨: {np.mean(all_t3):+.2f}%")
        print(f"  5日最高均值: {np.mean(all_max):+.2f}%")

    output = {
        'backtest_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'version': 'JH MiMo Screener v2.5',
        'period': f'{backtest_dates[0]} ~ {backtest_dates[-1]}',
        'total_picks': len(all_results),
        'daily_stats': daily_stats,
    }
    bt_file = os.path.join(DATA_DIR, 'backtest_result_mimo.json')
    with open(bt_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n✅ 结果已保存: {bt_file}")
    return output


if __name__ == '__main__':
    run_backtest()
