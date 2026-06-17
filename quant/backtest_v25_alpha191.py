#!/usr/bin/env python3
"""JH v2.5 + Alpha191 混合回测 — 跨股票归一化Alpha191分数"""
import json, os, sys, math, time, argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ========== 导入 ==========
sys.path.insert(0, os.path.dirname(__file__))
from screener import (
    fetch_all_quotes_batch, fetch_market_sentiment, pre_filter,
    fetch_klines, quick_analyze, load_recent_codes,
    score_early_entry
)
from alpha191_engine import calc_alpha191_factors, score_alpha191_factors, hybrid_score, normalize_alpha_batch

# ========== 获取回测日期 ==========
def get_trading_dates(all_klines_dict, need_days=25):
    dates = set()
    for code, data in all_klines_dict.items():
        klines = data['klines']
        for k in klines[-need_days:]:
            if 'date' in k:
                dates.add(k['date'])
    return sorted(dates)[-need_days:]

def get_klines_up_to_date(klines, date_str):
    result = []
    for k in klines:
        kd = k.get('date', '')
        if kd <= date_str:
            result.append(k)
        else:
            break
    if len(result) < 30:
        return None
    return result

# ========== 单日回测（两阶段：先收集→再归一化→然后评分） ==========
def backtest_day_v2(all_klines, date, recent_codes, market_change, config):
    """
    两阶段：
      Phase 1: 收集所有股票的JH分 + Alpha原始值 + 未来价格
      Phase 2: 跨股票归一化Alpha191 → 混合评分 → 过滤
    """
    candidates = []
    for code, data in all_klines.items():
        if code in recent_codes:
            continue
        klines_all = data['klines']
        klines = get_klines_up_to_date(klines_all, date)
        if not klines:
            continue
        last = klines[-1]
        rt_change = last.get('change', 0)

        # JH v2.5评分（独立，不需要归一化）
        ind = quick_analyze(klines)
        if not ind:
            continue
        jh_score, jh_factors = score_early_entry(ind, rt_change, market_change)
        if jh_score < 40:  # v4.1阈值40
            continue

        # Alpha191原始因子值
        O = np.array([k['open'] for k in klines[-120:]])
        H = np.array([k['high'] for k in klines[-120:]])
        L = np.array([k['low'] for k in klines[-120:]])
        C = np.array([k['close'] for k in klines[-120:]])
        V = np.array([k['volume'] for k in klines[-120:]])
        alpha_raw = calc_alpha191_factors(O, H, L, C, V)

        # 未来收益
        entry_price = last['close']
        t1_price = None; t3_price = None; t5_price = None
        count1 = 0; count3 = 0; count5 = 0
        daily_returns = []; future_dates = []
        for k in klines_all:
            if k.get('date', '') > date:
                count1 += 1
                if count1 == 1: t1_price = k['close']
                if count1 == 3: t3_price = k['close']
                if count1 == 5: t5_price = k['close']
                if count1 <= 15:
                    daily_returns.append(round((k['close'] - entry_price) / entry_price * 100, 2))
                    future_dates.append(k['date'])
        f15_return = daily_returns[-1] if len(daily_returns) >= 15 else (daily_returns[-1] if daily_returns else None)

        candidates.append({
            'code': code,
            'name': data.get('name', ''),
            'jh_score': jh_score,
            'jh_factors': jh_factors,
            'alpha_raw': alpha_raw,
            'entry_price': entry_price,
            't1_return': round((t1_price - entry_price) / entry_price * 100, 2) if t1_price else None,
            't3_return': round((t3_price - entry_price) / entry_price * 100, 2) if t3_price else None,
            't5_return': round((t5_price - entry_price) / entry_price * 100, 2) if t5_price else None,
            'f15_return': f15_return,
            'daily_returns': daily_returns,
            'future_dates': future_dates,
        })

    if not candidates:
        return []

    # Phase 2: 跨股票归一化Alpha191
    all_alpha_raw = [c['alpha_raw'] for c in candidates if c['alpha_raw']]
    if not all_alpha_raw:
        return []
    all_alpha_norm = normalize_alpha_batch(all_alpha_raw)

    # 混合评分
    results = []
    for i, c in enumerate(candidates):
        alpha_norm = all_alpha_norm[i]
        alpha_score, alpha_details = score_alpha191_factors(alpha_norm)

        # 混合: JH×60% + Alpha191×40%
        mixed = c['jh_score'] * 0.6 + alpha_score * 0.4

        # 合并因子
        all_factors = {}
        for k, v in c['jh_factors'].items():
            all_factors[f'JH.{k}'] = v
        for k, v in alpha_details.items():
            all_factors[f'A.{k}'] = v

        if mixed < 50:
            continue

        results.append({
            'code': c['code'],
            'name': c['name'],
            'score': round(mixed, 1),
            'jh_score': round(c['jh_score'], 1),
            'alpha_score': round(alpha_score, 1),
            'entry_price': round(c['entry_price'], 2),
            'factors': all_factors,
            't1_return': c['t1_return'],
            't3_return': c['t3_return'],
            't5_return': c.get('t5_return'),
            'f15_return': c.get('f15_return'),
            'daily_returns': c.get('daily_returns', []),
            'future_dates': c.get('future_dates', []),
            'max_return': round(max(c.get('daily_returns', []) or [0]), 2),
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:10]


def run_backtest():
    config = {'name': 'JH v4.1 + Alpha191'}

    print("全A股批量行情...")
    # 从全A股列表读取（排除科创板/创业板/北交所）
    list_file = os.path.join(DATA_DIR, 'a_stock_list.json')
    if not os.path.exists(list_file):
        print("[ERROR] a_stock_list.json 不存在")
        return
    with open(list_file) as f:
        all_stocks = json.load(f)
    def should_exclude(fc, name):
        if 'ST' in name: return True
        if fc.startswith('sz300') or fc.startswith('sz301') or fc.startswith('sh688') or fc[2:].startswith('920'): return True
        return False
    codes = [(str(s['code']), s['name']) for s in all_stocks if not should_exclude(s.get('full',''), s.get('name',''))]
    codes = codes[:300]  # TOP300控制时间
    print(f"标的池: {len(codes)}只 | 拉取K线...")

    all_klines = {}
    for i, (c, n) in enumerate(codes):
        klines = fetch_klines(c, 120)
        if klines and len(klines) >= 40:
            all_klines[c] = {'code': c, 'name': n, 'klines': klines}
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(codes)} ({len(all_klines)}有效)")
        time.sleep(0.02)

    sample = list(all_klines.values())[0]
    dates = get_trading_dates(all_klines, 25)
    backtest_dates = dates[-15:]
    print(f"回测区间: {backtest_dates[0]} ~ {backtest_dates[-1]} ({len(backtest_dates)}天)")

    daily_stats = []
    all_results = []
    recent_codes = set()

    for date in backtest_dates:
        market_change = 0
        day_results = backtest_day_v2(all_klines, date, recent_codes, market_change, config)

        for r in day_results:
            recent_codes.add(r['code'])

        if day_results:
            t1_returns = [r['t1_return'] for r in day_results if r['t1_return'] is not None]
            t3_returns = [r['t3_return'] for r in day_results if r['t3_return'] is not None]
            t5_returns = [r['t5_return'] for r in day_results if r['t5_return'] is not None]
            f15_returns = [r['f15_return'] for r in day_results if r['f15_return'] is not None]
            t1_avg = np.mean(t1_returns) if t1_returns else 0
            t3_avg = np.mean(t3_returns) if t3_returns else 0
            t5_avg = np.mean(t5_returns) if t5_returns else 0
            f15_avg = np.mean(f15_returns) if f15_returns else 0
            t1_win = sum(1 for r in t1_returns if r > 0) / len(t1_returns) * 100 if t1_returns else 0
            t3_win = sum(1 for r in t3_returns if r > 0) / len(t3_returns) * 100 if t3_returns else 0
        else:
            t1_avg = t3_avg = t5_avg = f15_avg = t1_win = t3_win = 0

        t5_show = f"T+5:{t5_avg:+.2f}%" if t5_returns else ""
        f15_show = f"T+15:{f15_avg:+.2f}%" if f15_returns else ""
        print(f"  {date} | 推荐{len(day_results)}只 | T+1:{t1_avg:+.2f}% | T+3:{t3_avg:+.2f}% | {t5_show} | {f15_show}")
        for r in day_results[:3]:
            t1 = f"{r['t1_return']:+.1f}%" if r['t1_return'] is not None else "N/A"
            t3 = f"{r['t3_return']:+.1f}%" if r['t3_return'] is not None else "N/A"
            print(f"    {r['name']:6s}({r['code']}) 混合:{r['score']:.1f} JH:{r['jh_score']:.1f} A191:{r['alpha_score']:.1f} T+1:{t1} T+3:{t3}")

        daily_stats.append({
            'date': date,
            'picks_count': len(day_results),
            't1_avg_return': round(t1_avg, 2),
            't3_avg_return': round(t3_avg, 2),
            't1_win_rate': round(t1_win, 1),
            't3_win_rate': round(t3_win, 1),
        })
        all_results.extend(day_results)

    all_t1 = [r['t1_return'] for r in all_results if r['t1_return'] is not None]
    all_t3 = [r['t3_return'] for r in all_results if r['t3_return'] is not None]
    all_t5 = [r['t5_return'] for r in all_results if r['t5_return'] is not None]
    all_f15 = [r['f15_return'] for r in all_results if r['f15_return'] is not None]
    t1_win_rate = sum(1 for r in all_t1 if r > 0) / len(all_t1) * 100 if all_t1 else 0
    t3_win_rate = sum(1 for r in all_t3 if r > 0) / len(all_t3) * 100 if all_t3 else 0
    t5_win_rate = sum(1 for r in all_t5 if r > 0) / len(all_t5) * 100 if all_t5 else 0
    f15_win_rate = sum(1 for r in all_f15 if r > 0) / len(all_f15) * 100 if all_f15 else 0

    print("\n" + "=" * 50)
    print(f"  {config['name']} 回测汇总")
    print("=" * 50)
    print(f"  总推荐: {len(all_results)}只次 | {len(backtest_dates)}天")
    print(f"  T+1胜率: {t1_win_rate:.1f}% | T+1均涨: {np.mean(all_t1):+.2f}%" if all_t1 else "")
    print(f"  T+3胜率: {t3_win_rate:.1f}% | T+3均涨: {np.mean(all_t3):+.2f}%" if all_t3 else "")
    print(f"  T+5胜率: {t5_win_rate:.1f}% | T+5均涨: {np.mean(all_t5):+.2f}%" if all_t5 else "")
    print(f"  T+15胜率: {f15_win_rate:.1f}% | T+15均涨: {np.mean(all_f15):+.2f}%" if all_f15 else "")

    output = {
        'backtest_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'version': 'v4.1+Alpha191',
        'period': f'{backtest_dates[0]} ~ {backtest_dates[-1]}',
        'total_picks': len(all_results),
        'total_days': len(backtest_dates),
        't1_win_rate': round(t1_win_rate, 1),
        't1_avg_return': round(np.mean(all_t1), 2) if all_t1 else 0,
        't3_win_rate': round(t3_win_rate, 1),
        't3_avg_return': round(np.mean(all_t3), 2) if all_t3 else 0,
        't5_win_rate': round(t5_win_rate, 1),
        't5_avg_return': round(np.mean(all_t5), 2) if all_t5 else 0,
        'f15_win_rate': round(f15_win_rate, 1),
        'f15_avg_return': round(np.mean(all_f15), 2) if all_f15 else 0,
        'daily_stats': daily_stats,
    }

    out_file = os.path.join(DATA_DIR, 'backtest_result_v25_alpha191.json')
    with open(out_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n已保存: {out_file}")

if __name__ == '__main__':
    run_backtest()
