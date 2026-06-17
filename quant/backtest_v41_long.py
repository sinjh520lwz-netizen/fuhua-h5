#!/usr/bin/env python3
"""
JH v4.1 长周期回测 — 验证策略一致性
====================================
通过环境变量 JH_DAYS 控制回测天数
用法: JH_DAYS=120 python3 backtest_v41_long.py
"""
import json, os, sys, time
from collections import Counter
import numpy as np

# 从原脚本导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_v41_5min_real import *

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def run_long():
    days = int(os.environ.get('JH_DAYS', 120))
    tp_pct = float(os.environ.get('JH_TP', 5.0))
    sl_pct = float(os.environ.get('JH_SL', 4.0))
    min_score = float(os.environ.get('JH_MIN_SCORE', 6))
    hold_days = int(os.environ.get('JH_HOLD_DAYS', 3))
    
    # 覆盖原脚本全局变量
    import backtest_v41_5min_real as bt41
    bt41.tp_pct = tp_pct
    bt41.sl_pct = sl_pct
    bt41.min_score = min_score
    
    log_file = f'/tmp/bt_long_{days}d.log'
    sys.stdout = open(log_file, 'w', buffering=1)
    sys.stderr = sys.stdout
    
    print("=" * 72)
    print(f"  JH v4.1 长周期回测 | {days}个交易日 | TP+{tp_pct}%/SL-{sl_pct}% | 持有{hold_days}天")
    print("=" * 72)
    
    with open(f'{DATA_DIR}/all_klines_60d.json') as f:
        data = json.load(f)
    
    dc = Counter()
    for ci in data.values():
        for k in ci.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                dc[k[0]] += 1
    
    all_dates = sorted([d for d, c in dc.items() if c >= 2000])
    print(f"  {len(data)}只股票, {len(all_dates)}个交易日可用")
    
    # 取最后 days 天（跳过首尾极端值）
    btd = all_dates[5:-3][-days:]
    print(f"  回测{len(btd)}天: {btd[-1]} ~ {btd[0]}\n")
    
    bt41.bs.login()
    print("  Baostock OK")
    
    all_trades = []
    gaps = []
    t0 = time.time()
    
    # 每月报告
    last_month = ''
    month_stats = {}
    
    for di, date in enumerate(btd):
        month_key = date[:7]
        if month_key != last_month:
            if last_month and month_stats.get(last_month, {}).get('trades', 0) > 0:
                ms = month_stats[last_month]
                r = ms['return']
                n = ms['trades']
                print(f"  📊 {last_month}: {n}笔 收益{r:+.2f}% 均每笔{r/n:+.2f}%")
            last_month = month_key
            if month_key not in month_stats:
                month_stats[month_key] = {'trades': 0, 'return': 0}
        
        print(f"[{di+1}/{len(btd)}] {date} ({time.time()-t0:.0f}s)...")
        
        # TOP20成交额候选
        snap = []
        for code, info in data.items():
            for k in info.get('klines', []):
                if isinstance(k, list) and len(k) >= 6 and k[0] == date:
                    c = float(k[2])
                    o = float(k[1])
                    v = float(k[5])
                    snap.append({'code': code, 'name': info.get('name', ''), 'amount': c * v / 10000})
                    break
        snap.sort(key=lambda x: -x['amount'])
        cands = [s for s in snap[:20] if not s['code'].startswith(('688','689','300','301','4','8','920'))]
        
        # 14:30评分
        scored = []
        for s in cands:
            code = s['code']
            intra, price1430 = get_intraday_1430(code, date)
            if intra is None or price1430 <= 0:
                continue
            
            h = build_hist_klines(data, code, date)
            if len(h) < 30:
                continue
            h[-1]['open'] = intra['open']
            h[-1]['close'] = intra['close']
            h[-1]['high'] = intra['high']
            h[-1]['low'] = intra['low']
            h[-1]['volume'] = intra['volume']
            
            ind = quick_analyze(h)
            if not ind:
                continue
            chg = (intra['close'] / intra['open'] - 1) * 100 if intra['open'] > 0 else 0
            sc, _ = score_early_entry(ind, chg, 0)
            if sc and sc >= min_score:
                nm = next((x['name'] for x in cands if x['code'] == code), code)
                scored.append({'code': code, 'name': nm, 'score': sc, 'price_1430': price1430})
        
        scored.sort(key=lambda x: -x['score'])
        picks = scored[:5]
        if not picks:
            continue
        
        di_idx = all_dates.index(date)
        end_d = all_dates[min(di_idx + 3, len(all_dates) - 1)]
        
        for p in picks:
            code = p['code']
            price1430 = p['price_1430']
            
            min_p, max_p = get_1430_to_close(code, date)
            if min_p is None:
                continue
            
            p_low = price1430 * 0.99
            p_high = price1430 * 1.01
            actual_min = max(min_p, p_low)
            actual_max = min(max_p, p_high)
            
            if actual_min > actual_max:
                continue
            
            entry_price = round((actual_min + actual_max) / 2, 2)
            deviation = round(abs(entry_price / price1430 - 1) * 100, 2)
            
            bars = get_5min(code, date, end_d)
            if bars is None or len(bars) < 3:
                continue
            
            result = simulate(bars, date, entry_price, tp_pct, sl_pct, hold_days)
            if result is None:
                continue
            
            gap_pct = get_gap_1430_to_next_open(bars, date, entry_price) or 0
            
            trade = {
                'date': date, 'code': code, 'name': p['name'], 'score': p['score'],
                'score_price': price1430, 'entry_price': entry_price, 'deviation': deviation,
                'hit_type': result[0], 'return_pct': result[2], 'gap_pct': gap_pct
            }
            all_trades.append(trade)
            gaps.append(gap_pct)
            
            # 更新月统计
            month_stats.setdefault(date[:7], {'trades': 0, 'return': 0})
            month_stats[date[:7]]['trades'] += 1
            month_stats[date[:7]]['return'] += result[2]
            
            e = '🟢' if result[0] == 'TP' else ('🔴' if result[0] == 'SL' else '⚪')
            print(f"  {e} {p['name']:6s}({code}) 评分价{price1430:.2f} 买{entry_price:.2f}(偏{deviation:.2f}%) 分{p['score']:.0f}→{result[0]} {result[2]:+.2f}% 跳{gap_pct:+.2f}%")
        
        if len(bt41._cache) > 200:
            bt41._cache.clear()
    
    bt41.bs.logout()
    
    elapsed = time.time() - t0
    print(f"\n{'=' * 72}")
    print(f"  v4.1 长周期{len(btd)}天 | 耗时{elapsed:.0f}s")
    print(f"{'=' * 72}")
    
    if not all_trades:
        print("  无交易")
        return
    
    total = len(all_trades)
    tp = sum(1 for t in all_trades if t['hit_type'] == 'TP')
    sl = sum(1 for t in all_trades if t['hit_type'] == 'SL')
    hold = sum(1 for t in all_trades if t['hit_type'] == 'HOLD')
    tr = sum(t['return_pct'] for t in all_trades)
    wins = sum(1 for t in all_trades if t['return_pct'] > 0)
    avg_dev = np.mean([t['deviation'] for t in all_trades])
    
    print(f"\n  📊 总统计:")
    print(f"  交易:{total} | TP:{tp}({tp/total*100:.1f}%) SL:{sl}({sl/total*100:.1f}%) HOLD:{hold}({hold/total*100:.1f}%)")
    print(f"  胜率:{wins}/{total}={wins/total*100:.1f}% | 总收益:{tr:+.2f}% | 均每笔:{tr/total:+.2f}%")
    print(f"  买入价偏差: 均{avg_dev:.2f}%")
    
    ga = np.array(gaps)
    neg = sum(1 for g in gaps if g < 0)
    print(f"  隔夜跳空: 均{np.mean(ga):+.2f}% 低开{neg}/{len(gaps)}={neg/len(gaps)*100:.1f}%")
    
    # 按月份汇总
    print(f"\n  📅 月度统计:")
    for mk in sorted(month_stats.keys()):
        ms = month_stats[mk]
        if ms['trades'] > 0:
            r = ms['return']
            n = ms['trades']
            print(f"  {mk}: {n}笔 月收益{r:+.2f}% 均每笔{r/n:+.2f}%")
    
    # 分段统计（前一半 vs 后一半）
    mid = total // 2
    first_half = all_trades[:mid]
    second_half = all_trades[mid:]
    
    def stat_seg(seg, name):
        if not seg:
            return
        s_total = len(seg)
        s_tp = sum(1 for t in seg if t['hit_type'] == 'TP')
        s_sl = sum(1 for t in seg if t['hit_type'] == 'SL')
        s_tr = sum(t['return_pct'] for t in seg)
        s_win = sum(1 for t in seg if t['return_pct'] > 0)
        print(f"  {name}: {s_total}笔 胜率{s_win/s_total*100:.1f}% 收益{s_tr:+.2f}% TP={s_tp} SL={s_sl}")
    
    print(f"\n  🔄 一致性检验（前后半段对比）:")
    stat_seg(first_half, "前半段")
    stat_seg(second_half, "后半段")
    
    suffix = f"long_{days}d"
    with open(f'{DATA_DIR}/backtest_{suffix}_5min_bs.json', 'w') as f:
        json.dump({
            'total': total, 'tp': tp, 'sl': sl, 'hold': hold,
            'total_return': round(tr, 2),
            'win_rate': round(wins / total * 100, 1),
            'avg_deviation': round(avg_dev, 2),
            'days': len(btd),
            'trades': all_trades,
            'params': {'tp': tp_pct, 'sl': sl_pct, 'threshold': min_score, 'buy_time': '14:30', 'hold_days': hold_days}
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 结果保存到 backtest_{suffix}_5min_bs.json")
    # 强制保存磁盘缓存
    try:
        from backtest_v41_5min_real import _save_disk_cache
        _save_disk_cache(force=True)
        print(f"  💾 5分钟K线缓存已保存")
    except:
        pass
    sys.stdout.flush()

if __name__ == '__main__':
    run_long()
