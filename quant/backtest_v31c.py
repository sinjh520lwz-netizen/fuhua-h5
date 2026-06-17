#!/usr/bin/env python3
"""
JH 选股策略回测 — v3.1c（趋势过滤版）
核心：买向上趋势，不抄底
"""
import json, os, sys, math, time
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import fetch_klines, quick_analyze

def get_trading_dates(klines, n=25):
    dates = [k['date'] for k in klines]
    return dates[-n:]

# ========== v3.1c 评分函数（趋势过滤版） ==========
def score_v31c(ind, rt_change=0, market_change=0):
    """趋势过滤评分 v3.1c — 买向上趋势，不抄底"""
    if not ind:
        return 0.0, {}

    score = 10.0
    factors = {}

    close = ind['close']
    ma5, ma10, ma20 = ind['ma5'], ind['ma10'], ind['ma20']
    ma60 = ind.get('ma60', np.nan)
    rsi14 = ind['rsi14']
    vr = ind['vol_ratio']
    mom5 = ind['mom_5d']
    mom10 = ind['mom_10d']
    ts = ind['trend_score']
    bo = ind.get('breakout', 50)
    boll_mid = ind.get('boll_mid', 0)
    boll_pos = ind.get('boll_pos', 50)

    # ========== 均线多头排列（核心趋势过滤） ==========
    if all([not np.isnan(x) for x in [ma5, ma10, ma20]]) and ma5 > ma10 > ma20:
        score += 2
        factors['均线多头'] = 2
    else:
        score -= 2
        factors['均线多头'] = -2

    # ========== 站上所有均线 ==========
    above_ma5 = not np.isnan(ma5) and close > ma5
    above_ma10 = not np.isnan(ma10) and close > ma10
    above_ma20 = not np.isnan(ma20) and close > ma20
    above_ma60 = not np.isnan(ma60) and close > ma60 if not np.isnan(ma60) else True
    if above_ma5 and above_ma10 and above_ma20 and above_ma60:
        score += 2
        factors['站上均线'] = 2
    else:
        score -= 3
        factors['站上均线'] = -3

    # ========== 动量方向 ==========
    if mom5 > 0 and mom10 > 0:
        score += 2
        factors['动量方向'] = 2
    else:
        score -= 4
        factors['动量方向'] = -4

    # ========== RSI 50-60 区间（多头但不是过热） ==========
    if not np.isnan(rsi14):
        if 50 <= rsi14 <= 60:
            score += 3
            factors['RSI'] = 3
        elif 45 <= rsi14 < 50:
            score += 1
            factors['RSI'] = 1
        elif 60 < rsi14 <= 70:
            score += 1
            factors['RSI'] = 1
        elif rsi14 > 75:
            score -= 5
            factors['RSI'] = -5
        elif rsi14 < 35:
            score -= 5
            factors['RSI'] = -5
        elif rsi14 > 70:
            score -= 2
            factors['RSI'] = -2
        elif rsi14 < 40:
            score -= 3
            factors['RSI'] = -3

    # ========== 量价配合（权重6） ==========
    if vr >= 1.3:
        s = min(6, round(vr * 3, 1))
        score += s
        factors['量价配合'] = round(s, 1)
    elif vr < 0.7:
        score -= 3
        factors['量价配合'] = -3
    elif vr < 0.85:
        score -= 1
        factors['量价配合'] = -1

    # ========== 相对强度 ==========
    rel = rt_change - market_change
    if rel > 2:
        score += 3
        factors['相对强度'] = 3
    elif rel > 1:
        score += 1.5
        factors['相对强度'] = 1.5
    elif rel < -2:
        score -= 2
        factors['相对强度'] = -2
    elif rel < -1:
        score -= 1
        factors['相对强度'] = -1

    # ========== 布林带位置 ==========
    if not np.isnan(boll_mid) and close > boll_mid:
        score += 3
        factors['布林位置'] = 3
    else:
        score -= 2
        factors['布林位置'] = -2

    # ========== 趋势强度 ==========
    if 50 <= ts <= 65:
        score += 2
        factors['趋势强度'] = 2
    elif ts > 75:
        score -= 3
        factors['趋势强度'] = -3
    elif ts < 40:
        score -= 3
        factors['趋势强度'] = -3

    # ========== 突破位置 ==========
    if 50 <= bo <= 70:
        score += 2
        factors['突破位置'] = 2
    elif bo > 90:
        score -= 4
        factors['突破位置'] = -4
    elif bo < 20:
        score -= 2
        factors['突破位置'] = -2

    # ========== 大盘惩罚 ==========
    if market_change < -2:
        score -= 8
    elif market_change < -1:
        score -= 3
    # ========== 硬性门槛（不满足直接扣重分） ==========
    # 必须站上MA5（起码今天有向上的意思）
    if not above_ma5:
        score -= 5
        factors['站上MA5'] = -5

    final = round(min(max(score, 0), 80), 1)
    return final, factors


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

    score, factors = score_v31c(ind, day_change, market_change)

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
    if len(future) >= 15:
        result['t15_price'] = future[14]['close']
        result['t15_return'] = round((future[14]['close'] / day_close - 1) * 100, 2)

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
    daily = result.get('daily_returns', [])
    if not daily:
        return 'hold', ''
    for i, r in enumerate(daily):
        if r <= -6:
            return 'stop_loss', f'T+{i+1}跌{r:.1f}%'
    for i, r in enumerate(daily):
        if r >= 5:
            return 'take_profit', f'T+{i+1}涨{r:.1f}%'
    t3 = result.get('t3_return') or result.get('t1_return', 0)
    if t3 > 3:
        return 'profit', f'收益{t3:.1f}%'
    elif t3 < -2:
        return 'loss', f'亏损{t3:.1f}%'
    else:
        return 'hold', f'持平{t3:.1f}%'


def run_backtest():
    print("=" * 65)
    print("  JH 选股策略回测 — v3.1c（趋势过滤版）")
    print("  热门股池 | 3个月")
    print("=" * 65)

    hot_file = os.path.join(os.path.dirname(__file__), 'data', 'ths_hot_list.json')
    if not os.path.exists(hot_file):
        print("[ERROR] ths_hot_list.json 不存在")
        return
    with open(hot_file) as f:
        data = json.load(f)
    codes = [(str(s['code']), s['name']) for s in data.get('data', {}).get('stock_list', [])
             if 'ST' not in s.get('name', '')]
    print(f"\n标的池: {len(codes)}只（热门股） | 拉取K线（120天）...")

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
    recent_codes = set()

    for date in backtest_dates:
        day_picks = []
        for code, info in all_klines.items():
            if code in recent_codes:
                continue
            result = simulate_day(info['klines'], date)
            if result and result['score'] >= 30:
                result['code'] = code
                result['name'] = info['name']
                day_picks.append(result)

        day_picks.sort(key=lambda x: x['score'], reverse=True)
        if day_picks:
            recent_codes.update(p['code'] for p in day_picks[:3])
            if len(day_picks) > 5:
                day_picks = day_picks[:5]

        for p in day_picks:
            all_results.append(p)

        if day_picks:
            t1s = [p.get('t1_return', 0) for p in day_picks if 't1_return' in p]
            t3s = [p.get('t3_return', 0) for p in day_picks if 't3_return' in p]
            t5s = [p.get('t5_return', 0) for p in day_picks if 't5_return' in p]
            info_str = f"📅 {date} | 推荐{len(day_picks)}只"
            if t1s: info_str += f" | T+1:{np.mean(t1s):+.2f}%"
            if t3s: info_str += f" | T+3:{np.mean(t3s):+.2f}%"
            if t5s: info_str += f" | T+5:{np.mean(t5s):+.2f}%"
            print(f"\n{info_str}")
            for p in day_picks:
                t1 = f"T+1:{p.get('t1_return', 0):+.1f}%" if 't1_return' in p else "T+1:--"
                t3 = f"T+3:{p.get('t3_return', 0):+.1f}%" if 't3_return' in p else "T+3:--"
                t5 = f"T+5:{p.get('t5_return', 0):+.1f}%" if 't5_return' in p else "T+5:--"
                t15 = f"T+15:{p.get('t15_return', 0):+.1f}%" if 't15_return' in p else "T+15:--"
                cls, reason = classify_trade(p)
                emoji = {'stop_loss': '🔴', 'take_profit': '🟢', 'profit': '✅', 'loss': '❌', 'hold': '⚪'}.get(cls, '⚪')
                sigs = ' | '.join([f"{k}:{v}" for k, v in sorted(p.get('factors', {}).items(), key=lambda x: -abs(x[1]))[:3]])
                print(f"  {p['name']:6s}({p['code']:6s}) 分:{p['score']:.0f} 入:{p['entry_price']:.2f} {t1} {t3} {t5} {t15} {emoji}{reason}")

        max_in_day = max([p.get('max_return', 0) for p in day_picks], default=0)
        if day_picks:
            avg_change = np.mean([p.get('change', 0) for p in day_picks])
            daily_stats.append({
                'date': date,
                'picks': len(day_picks),
                'best_max': round(max_in_day, 2),
                'avg_change': round(avg_change, 2),
            })

    # ========== 汇总 ==========
    print(f"\n{'=' * 65}")
    print(f"  📊 回测汇总 v3.1c")
    print(f"{'=' * 65}")

    if all_results:
        all_t1 = [r['t1_return'] for r in all_results if 't1_return' in r]
        all_t3 = [r['t3_return'] for r in all_results if 't3_return' in r]
        all_t5 = [r['t5_return'] for r in all_results if 't5_return' in r]
        all_f15 = [r['t15_return'] for r in all_results if 't15_return' in r]
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

        print(f"\n  📈 按分数段:")
        for low, high in [(70, 81), (60, 70), (50, 60), (40, 50)]:
            seg = [r for r in all_results if low <= r.get('score', 0) < high and 't1_return' in r]
            if seg:
                seg_t1_wins = len([r for r in seg if r['t1_return'] > 0])
                seg_t1_avg = np.mean([r['t1_return'] for r in seg])
                seg_t3 = [r.get('t3_return') for r in seg if r.get('t3_return') is not None]
                seg_t3_avg = np.mean(seg_t3) if seg_t3 else 0
                print(f"    {low}-{high}分: {len(seg)}只 | T+1胜率:{seg_t1_wins/len(seg)*100:.0f}% | T+1均:{seg_t1_avg:+.2f}% | T+3均:{seg_t3_avg:+.2f}%")


if __name__ == '__main__':
    run_backtest()
