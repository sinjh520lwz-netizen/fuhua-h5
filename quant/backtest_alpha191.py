#!/usr/bin/env python3
"""
Alpha191 + JH 混合策略回测 v1.0
使用 alpha191_engine.py 的混合评分引擎
"""
import json, os, sys, time
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from alpha191_engine import (
    fetch_klines, quick_analyze, score_jh_factors,
    calc_alpha191_factors, normalize_alpha_batch,
    score_alpha191_factors, hybrid_score
)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def get_trading_dates(klines, n=25):
    dates = [k['date'] for k in klines]
    return dates[-(n+1):-1]


def simulate_day(all_klines_data, target_date):
    """对单只股票在某天进行混合评分回测"""
    results = []
    raw_for_alpha = []
    stock_infos = []

    for code, info in all_klines_data.items():
        klines = info['klines']
        dates = [k['date'] for k in klines]
        if target_date not in dates:
            continue
        idx = dates.index(target_date)
        if idx < 30:
            continue

        hist_klines = klines[:idx+1]
        ind = quick_analyze(hist_klines)
        if not ind:
            continue

        day_open = klines[idx]['open']
        day_close = klines[idx]['close']
        day_change = (day_close / day_open - 1) * 100

        # JH评分
        jh_score, jh_factors = score_jh_factors(ind, day_change, 0)

        # Alpha191原始因子
        alpha_raw = calc_alpha191_factors(ind['_O'], ind['_H'], ind['_L'], ind['_C'], ind['_V'])

        raw_for_alpha.append(alpha_raw)
        stock_infos.append({
            'code': code, 'name': info['name'],
            'ind': ind, 'day_close': day_close, 'day_change': day_change,
            'jh_score': jh_score, 'jh_factors': jh_factors,
            'alpha_raw': alpha_raw,
            'klines': klines, 'idx': idx,
        })

    if not raw_for_alpha:
        return []

    # 批量标准化Alpha191
    all_alpha_norm = normalize_alpha_batch(raw_for_alpha)

    day_results = []
    for i, si in enumerate(stock_infos):
        alpha_norm = all_alpha_norm[i]
        alpha_score, alpha_details = score_alpha191_factors(alpha_norm)
        hybrid, all_factors = hybrid_score(si['jh_score'], si['jh_factors'], alpha_score, alpha_details)

        if hybrid < 55:
            continue

        klines = si['klines']
        idx = si['idx']
        day_close = si['day_close']

        future = klines[idx+1:]
        result = {
            'code': si['code'], 'name': si['name'],
            'score': hybrid,
            'jh_score': si['jh_score'],
            'alpha_score': alpha_score,
            'entry_price': day_close,
            'change': round(si['day_change'], 2),
            'factors': {k: round(v, 1) for k, v in all_factors.items()},
        }

        if len(future) >= 1:
            result['t1_return'] = round((future[0]['close'] / day_close - 1) * 100, 2)
        if len(future) >= 2:
            result['t2_return'] = round((future[1]['close'] / day_close - 1) * 100, 2)
        if len(future) >= 3:
            result['t3_return'] = round((future[2]['close'] / day_close - 1) * 100, 2)
        if len(future) >= 5:
            result['t5_return'] = round((future[4]['close'] / day_close - 1) * 100, 2)

        if future:
            max_high = max(k['high'] for k in future[:5])
            min_low = min(k['low'] for k in future[:5])
            result['max_return'] = round((max_high / day_close - 1) * 100, 2)
            result['min_return'] = round((min_low / day_close - 1) * 100, 2)

        day_results.append(result)

    day_results.sort(key=lambda x: x['score'], reverse=True)
    return day_results[:5]


def run_backtest():
    print("=" * 65)
    print("  Alpha191 + JH 混合策略回测 v1.0 — 15个交易日")
    print("=" * 65)

    hot_file = os.path.join(DATA_DIR, 'ths_hot_list.json')
    if os.path.exists(hot_file):
        with open(hot_file) as f:
            data = json.load(f)
        codes = [(str(s['code']), s['name']) for s in data.get('data', {}).get('stock_list', [])
                 if 'ST' not in s.get('name', '')]
    else:
        codes = [('601138', '工业富联'), ('600519', '贵州茅台')]

    print(f"\n标的池: {len(codes)}只 | 拉取K线...")

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
    trading_dates = get_trading_dates(sample_klines, 25)
    backtest_dates = trading_dates[-15:]

    print(f"回测日期: {backtest_dates[0]} ~ {backtest_dates[-1]}")
    print(f"\n{'=' * 65}")

    daily_stats = []
    all_results = []
    recent_codes = set()

    for date in backtest_dates:
        # 过滤掉近期去重的股票
        filtered = {k: v for k, v in all_klines.items() if k not in recent_codes}
        day_picks = simulate_day(filtered, date)

        # 更新去重
        for p in day_picks:
            recent_codes.add(p['code'])
        if len(recent_codes) > 35:
            recent_codes = set(list(recent_codes)[-35:])

        if day_picks:
            t1_returns = [p.get('t1_return', 0) for p in day_picks if 't1_return' in p]
            t3_returns = [p.get('t3_return', 0) for p in day_picks if 't3_return' in p]
            t1_avg = np.mean(t1_returns) if t1_returns else 0
            t3_avg = np.mean(t3_returns) if t3_returns else 0
            t1_win = len([r for r in t1_returns if r > 0])
            t3_win = len([r for r in t3_returns if r > 0])

            stat = {
                'date': date, 'picks_count': len(day_picks),
                't1_avg_return': round(t1_avg, 2),
                't3_avg_return': round(t3_avg, 2),
                't1_win_rate': round(t1_win / len(t1_returns) * 100, 1) if t1_returns else 0,
                't3_win_rate': round(t3_win / len(t3_returns) * 100, 1) if t3_returns else 0,
                'picks': day_picks,
            }
            daily_stats.append(stat)
            all_results.extend(day_picks)

            print(f"\n📅 {date} | 推荐{len(day_picks)}只 | T+1:{t1_avg:+.2f}% | T+3:{t3_avg:+.2f}% | T+1胜率:{stat['t1_win_rate']}%")
            for p in day_picks:
                t1 = f"{p.get('t1_return',0):+.1f}%" if 't1_return' in p else '--'
                t3 = f"{p.get('t3_return',0):+.1f}%" if 't3_return' in p else '--'
                mx = f"{p.get('max_return',0):+.1f}%" if 'max_return' in p else '--'
                print(f"  {p['name']:8s}({p['code']}) 混合:{p['score']:5.1f} JH:{p.get('jh_score',0):5.1f} A191:{p.get('alpha_score',0):5.1f} T+1:{t1:>7s} T+3:{t3:>7s} 最高:{mx:>7s}")

    # 汇总
    print(f"\n{'=' * 65}")
    print(f"  📊 Alpha191+JH 混合策略回测汇总")
    print(f"{'=' * 65}")

    if all_results:
        all_t1 = [r.get('t1_return', 0) for r in all_results if 't1_return' in r]
        all_t3 = [r.get('t3_return', 0) for r in all_results if 't3_return' in r]
        all_max = [r.get('max_return', 0) for r in all_results if 'max_return' in r]
        all_min = [r.get('min_return', 0) for r in all_results if 'min_return' in r]

        t1_wins = len([r for r in all_t1 if r > 0])
        t3_wins = len([r for r in all_t3 if r > 0])

        print(f"\n  总推荐: {len(all_results)}只次 | 回测: {len(daily_stats)}天")
        print(f"\n  T+1 胜率: {t1_wins}/{len(all_t1)} = {t1_wins/len(all_t1)*100:.1f}%")
        print(f"  T+1 均涨: {np.mean(all_t1):+.2f}% | 最大盈: {max(all_t1):+.2f}% | 最大亏: {min(all_t1):+.2f}%")
        print(f"\n  T+3 胜率: {t3_wins}/{len(all_t3)} = {t3_wins/len(all_t3)*100:.1f}%")
        print(f"  T+3 均涨: {np.mean(all_t3):+.2f}% | 最大盈: {max(all_t3):+.2f}% | 最大亏: {min(all_t3):+.2f}%")
        print(f"\n  5日最高均值: {np.mean(all_max):+.2f}% | 最低均值: {np.mean(all_min):+.2f}%")
    else:
        print("  ⚠️ 无有效回测结果")

    output = {
        'backtest_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'version': 'Alpha191+JH v1.0',
        'strategy': 'Alpha191(20因子)×40% + JH v2.2(11因子)×60%',
        'period': f'{backtest_dates[0]} ~ {backtest_dates[-1]}',
        'total_picks': len(all_results),
        'total_days': len(daily_stats),
        'summary': {},
        'daily_stats': daily_stats,
    }

    if all_results:
        all_t1 = [r.get('t1_return', 0) for r in all_results if 't1_return' in r]
        all_t3 = [r.get('t3_return', 0) for r in all_results if 't3_return' in r]
        all_max = [r.get('max_return', 0) for r in all_results if 'max_return' in r]
        all_min = [r.get('min_return', 0) for r in all_results if 'min_return' in r]
        t1_wins = len([r for r in all_t1 if r > 0])
        t3_wins = len([r for r in all_t3 if r > 0])

        output['summary'] = {
            'total_recommendations': len(all_results),
            'backtest_days': len(daily_stats),
            't1_win_rate': round(t1_wins / len(all_t1) * 100, 1) if all_t1 else 0,
            't1_avg_return': round(float(np.mean(all_t1)), 2) if all_t1 else 0,
            't1_max_return': round(max(all_t1), 2) if all_t1 else 0,
            't1_min_return': round(min(all_t1), 2) if all_t1 else 0,
            't3_win_rate': round(t3_wins / len(all_t3) * 100, 1) if all_t3 else 0,
            't3_avg_return': round(float(np.mean(all_t3)), 2) if all_t3 else 0,
            't3_max_return': round(max(all_t3), 2) if all_t3 else 0,
            't3_min_return': round(min(all_t3), 2) if all_t3 else 0,
            'max_return_avg': round(float(np.mean(all_max)), 2) if all_max else 0,
            'min_return_avg': round(float(np.mean(all_min)), 2) if all_min else 0,
        }

    bt_file = os.path.join(DATA_DIR, 'backtest_result_alpha191.json')
    with open(bt_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 结果已保存: {bt_file}")
    return output


if __name__ == '__main__':
    run_backtest()
