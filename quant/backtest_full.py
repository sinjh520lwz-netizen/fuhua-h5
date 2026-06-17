#!/usr/bin/env python3
"""
JH 全A股历史回测 — 和实盘扫描流程完全一致
每天：遍历全部3024只 → 用当日K线数据模拟实时行情 → 过滤 → 取成交额TOP200 → 评分
"""
import json, os, sys, time, math
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def load_all_klines():
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f:
        return json.load(f)

def get_trading_dates(all_klines_dict, days_needed=80):
    """提取所有股票中最常见的交易日（找出哪些天有数据）"""
    date_counts = defaultdict(int)
    for code, info in all_klines_dict.items():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                date_counts[k[0]] += 1
    # 取最多股票有数据的天
    sorted_dates = sorted(date_counts.items(), key=lambda x: -x[1])
    # 取最活跃的日期
    top_dates = [d for d, c in sorted_dates if c > 100 and d >= '2024-01-01']
    return sorted(top_dates)[-days_needed:]

def get_stock_data_on_date(info, date):
    """从K线中提取某日的行情数据"""
    klines = info.get('klines', [])
    for k in klines:
        if isinstance(k, list) and len(k) >= 6 and k[0] == date:
            close = float(k[2])
            open_p = float(k[1])
            high = float(k[3])
            low = float(k[4])
            volume = float(k[5])
            change = (close / open_p - 1) * 100 if open_p > 0 else 0
            # 腾讯K线volume是 股(not 手)，金额=价×量/10000(万元)
            amount = close * volume / 10000
            return {
                'close': close, 'open': open_p, 'high': high, 'low': low,
                'volume': volume, 'amount': amount, 'change': change,
            }
    return None

def collect_market_snapshot(all_klines_dict, date):
    """模拟 fetch_all_quotes_batch() — 遍历全部股票，获取当日行情"""
    snapshot = []
    for code, info in all_klines_dict.items():
        name = info.get('name', '')
        sd = get_stock_data_on_date(info, date)
        if not sd:
            continue
        
        # 和实盘完全相同的过滤条件
        price = sd['close']
        if price <= 0 or price > 500:
            continue
        if 'ST' in name or '*ST' in name:
            continue
        amount = sd['amount']
        change = sd['change']
        if amount < 5000:  # 成交额<5000万
            continue
        if change <= 0 or change > 7:  # 涨幅范围（和实盘一样）
            continue
        if name.endswith('退') or '退市' in name:
            continue
        
        snapshot.append({
            'code': code, 'name': name,
            'price': price, 'change': change,
            'amount': amount, 'volume': sd['volume'],
            'high': sd['high'], 'low': sd['low'],
        })
    
    snapshot.sort(key=lambda x: x['amount'], reverse=True)
    return snapshot

def build_hist_klines(all_klines_dict, code, date):
    """从全量数据中提取到指定日期的历史K线（用于技术指标计算）"""
    info = all_klines_dict.get(code)
    if not info:
        return []
    result = []
    for k in info.get('klines', []):
        if isinstance(k, list) and len(k) >= 6:
            result.append({
                'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5])
            })
            if k[0] == date:
                break
    return result

def backtest_strategy(all_klines_dict, trading_dates, strategy='v4.0', threshold=40):
    """
    回测指定策略
    strategy: 'v4.0' 或 'v3.1c' 或 'v5.0'
    """
    if strategy == 'v3.1c':
        from backtest_v31c import score_v31c
        score_fn = score_v31c
        version_name = 'v3.1c(趋势过滤)'
    elif strategy == 'v5.0':
        from backtest_v50 import score_v50
        score_fn = score_v50
        version_name = 'v5.0(均线粘合)'
    elif strategy == 'v6.0':
        from backtest_v60 import score_v60
        score_fn = score_v60
        version_name = 'v6.0(多因子融合)'
    elif strategy == 'v7.0':
        from backtest_v70 import score_v70
        score_fn = score_v70
        version_name = 'v7.0(规则分层)'
    elif strategy == 'v8.0':
        from backtest_v80 import score_v80
        score_fn = score_v80
        version_name = 'v8.0(三力共振)'
    elif strategy == 'v8.5':
        from backtest_v85 import score_v85
        score_fn = score_v85
        version_name = 'v8.5(双确认)'
    elif strategy == 'v9.0':
        from backtest_v90 import score_v90
        score_fn = score_v90
        version_name = 'v9.0(强势延续)'
    elif strategy == 'v12.0':
        from cross_sectional_score import score_tp_sl
        score_fn = score_tp_sl
        version_name = 'v12.0(条件单TP+5%/SL-6%)'
    else:
        score_fn = score_early_entry
        version_name = 'v4.0(趋势反转)'
    
    all_results = []
    daily_stats = []
    recent_codes = set()
    
    backtest_dates = trading_dates  # 365个交易日（约1年）
    
    is_ranking_mode = (strategy in ('v10.0', 'v11.0'))
    
    print(f"\n{'=' * 65}")
    print(f"  {version_name} 回测 | {len(backtest_dates)}天 | 阈值{threshold}")
    print(f"  日期范围: {backtest_dates[0]} ~ {backtest_dates[-1]}")
    print(f"{'=' * 65}")
    
    for di, date in enumerate(backtest_dates):
        # 第1步：获取所有股票在当日的行情快照
        snapshot = collect_market_snapshot(all_klines_dict, date)
        if not snapshot:
            continue
        
        # 第2步：取成交额TOP200（和screener.py一致）
        top_stocks = snapshot[:200]
        
        # 第3步：对每只股票评分
        day_picks = []
        for s in top_stocks:
            code = s['code']
            # 去重：跳过7天内已推荐的股票
            if code in recent_codes:
                continue
            
            hist = build_hist_klines(all_klines_dict, code, date)
            if len(hist) < 30:
                continue
            
            ind = quick_analyze(hist)
            if not ind:
                continue
            
            score, factors = score_fn(ind, s['change'], 0)
            
            # 排名模式（v10.0）：只过滤硬淘汰（score≤5）
            # vXS模式：全部收集，后续做横截面排名
            # 非排名模式：用绝对阈值过滤
            if is_ranking_mode:
                if score < 6:
                    continue
            elif strategy == 'vXS':
                pass  # 全部收集，稍后统一排名
            else:
                if score < threshold:
                    continue
            
            # 获取未来收益（需要K线有后续数据）
            result = {
                'date': date, 'code': code, 'name': s['name'],
                'score': round(score, 1),
                'entry_price': s['price'], 'change': round(s['change'], 2),
                'amount': round(s['amount'], 0),
                'factors': dict(list(factors.items())[:5]),
                'indicators': ind,
            }
            
            # 查找未来的K线数据
            info = all_klines_dict.get(code, {})
            all_k = info.get('klines', [])
            idx = -1
            for i, k in enumerate(all_k):
                if isinstance(k, list) and k[0] == date:
                    idx = i
                    break
            if idx >= 0:
                future = all_k[idx+1:]
                day_close = s['price']
                
                if len(future) >= 1:
                    f1 = future[0]
                    f1_close = float(f1[2]) if isinstance(f1, list) else 0
                    result['t1_return'] = round((f1_close/day_close - 1)*100, 2)
                if len(future) >= 3:
                    f3 = future[2]
                    f3_close = float(f3[2]) if isinstance(f3, list) else 0
                    result['t3_return'] = round((f3_close/day_close - 1)*100, 2)
                if len(future) >= 5:
                    f5 = future[4]
                    f5_close = float(f5[2]) if isinstance(f5, list) else 0
                    result['t5_return'] = round((f5_close/day_close - 1)*100, 2)
                if len(future) >= 10:
                    f10 = future[9]
                    f10_close = float(f10[2]) if isinstance(f10, list) else 0
                    result['t15_return'] = round((f10_close/day_close - 1)*100, 2)
                elif len(future) >= 3:
                    result['t15_return'] = result.get('t3_return', 0)
                
                # 逐日收益
                daily_returns = []
                for k in future[:15]:
                    k_close = float(k[2]) if isinstance(k, list) else 0
                    daily_returns.append(round((k_close/day_close - 1)*100, 2))
                result['daily_returns'] = daily_returns
                
                if future:
                    max_h = max(float(k[3]) if isinstance(k, list) else 0 for k in future[:15])
                    min_l = min(float(k[4]) if isinstance(k, list) else 0 for k in future[:15])
                    result['max_return'] = round((max_h/day_close - 1)*100, 2)
                    result['min_return'] = round((min_l/day_close - 1)*100, 2)
                
                # TP/SL模拟（条件单：盘中触发模式，用最高/最低价）
                sl_pct = 4  # 默认止损4%
                if strategy in ('v10.0', 'v12.0'): sl_pct = 4
                tp_hit = False; sl_hit = False; exit_day = None
                # 用盘中最高最低价判断是否触发
                for di_idx in range(min(3, len(daily_returns))):
                    if di_idx < len(future):
                        k = future[di_idx]
                        if isinstance(k, list) and len(k) >= 5:
                            k_high = float(k[3])
                            k_low = float(k[4])
                            high_ret = (k_high / day_close - 1) * 100
                            low_ret = (k_low / day_close - 1) * 100
                            if high_ret >= 5:
                                tp_hit = True; exit_day = di_idx + 1; break
                            if low_ret <= -sl_pct:
                                sl_hit = True; exit_day = di_idx + 1; break
                if not tp_hit and not sl_hit and len(daily_returns) >= 3:
                    exit_day = 3
                # 没触发的T+3收盘出场
                exit_ret = daily_returns[min(exit_day or 3, len(daily_returns)-1)] if daily_returns else 0
                if tp_hit: exit_ret = 5
                elif sl_hit: exit_ret = -sl_pct
                result['tp_sl'] = {
                    'tp_hit': tp_hit, 'sl_hit': sl_hit,
                    'sl_pct': sl_pct, 'exit_day': exit_day or 3,
                    'exit_return': round(exit_ret, 2),
                }
            
            day_picks.append(result)
        
        # 第4步：排名模式 vs vXS旧横截面 vs 绝对阈值模式
        if is_ranking_mode and day_picks:
            # v10.0排名模式：分数降序排序，取前8只
            day_picks.sort(key=lambda x: x['score'], reverse=True)
            top_n = min(len(day_picks), max(3, int(len(day_picks) * 0.1)))
            top_n = max(top_n, min(5, len(day_picks)))
            day_picks = day_picks[:top_n]
            recent_codes.update(p['code'] for p in day_picks[:3])
        elif strategy == 'vXS' and day_picks:
            # 旧横截面排名：用3因子（涨幅/乖离率/5日动量）重新排名
            n = len(day_picks)
            changes = np.array([p['change'] for p in day_picks])
            ma5_devs = np.array([p['indicators'].get('ma5_deviation', 0) if isinstance(p.get('indicators'), dict) else 0 
                                for p in day_picks])
            mom5s = np.array([p['indicators'].get('mom_5d', 0) if isinstance(p.get('indicators'), dict) else 0 
                             for p in day_picks])
            
            # 对每个因子转成排名百分位
            def rank_pct(arr):
                mask = np.isfinite(arr)
                r = np.zeros(n)
                if mask.sum() > 1:
                    s = np.argsort(np.argsort(arr[mask]))
                    r[mask] = s / (mask.sum() - 1)
                return r
            
            r_change = rank_pct(changes)
            r_ma5 = rank_pct(ma5_devs)
            r_mom5 = rank_pct(mom5s)
            
            # 平均排名分（转成0-100）
            xs_scores = (r_change + r_ma5 + r_mom5) / 3 * 100
            
            for i, p in enumerate(day_picks):
                p['xs_score'] = round(float(xs_scores[i]), 1)
                p['score'] = p['xs_score']
            
            # 选前10%，最少3只最多8只
            day_picks.sort(key=lambda x: x['xs_score'], reverse=True)
            top_n = min(len(day_picks), max(3, int(len(day_picks) * 0.1)))
            top_n = max(top_n, min(5, len(day_picks)))
            day_picks = [p for p in day_picks[:top_n] if p['xs_score'] >= 30]
            recent_codes.update(p['code'] for p in day_picks[:3])
        elif day_picks:
            day_picks.sort(key=lambda x: x['score'], reverse=True)
            if day_picks:
                recent_codes.update(p['code'] for p in day_picks[:3])
                if len(day_picks) > 5:
                    day_picks = day_picks[:5]
        
        all_results.extend(day_picks)
        
        # 打印每天结果
        if day_picks:
            t1s = [p.get('t1_return', 0) for p in day_picks if 't1_return' in p]
            t3s = [p.get('t3_return', 0) for p in day_picks if 't3_return' in p]
            info_s = f"📅 {date} | {len(day_picks)}只"
            if t1s: info_s += f" | T+1:{np.mean(t1s):+.2f}%"
            if t3s: info_s += f" | T+3:{np.mean(t3s):+.2f}%"
            print(f"\n{info_s}")
            for p in day_picks[:3]:
                t1 = f"T+1:{p.get('t1_return',0):+.1f}%" if 't1_return' in p else "T+1:--"
                t3 = f"T+3:{p.get('t3_return',0):+.1f}%" if 't3_return' in p else "T+3:--"
                t5 = f"T+5:{p.get('t5_return',0):+.1f}%" if 't5_return' in p else "T+5:--"
                a = f"额:{p['amount']:.0f}万"
                sigs = '|'.join([f"{k}:{v:.0f}" for k,v in sorted(
                [(k,v) for k,v in p.get('factors',{}).items() if isinstance(v, (int, float))],
                key=lambda x:-abs(x[1]))[:3]])
                print(f"  {p['name']:6s}({p['code']}) 分:{p['score']:.0f} {t1} {t3} {t5} {a} {sigs}")
        
        if (di+1) % 10 == 0:
            print(f"  进度: {di+1}/{len(backtest_dates)}天 → 已出{len(all_results)}票", flush=True)
    
    # ========== 汇总 ==========
    print(f"\n{'=' * 65}")
    print(f"  📊 汇总 {version_name}")
    print(f"{'=' * 65}")
    
    if not all_results:
        print("  无推荐")
        return
    
    all_t1 = [r['t1_return'] for r in all_results if 't1_return' in r]
    all_t3 = [r['t3_return'] for r in all_results if 't3_return' in r]
    all_t5 = [r['t5_return'] for r in all_results if 't5_return' in r]
    all_f15 = [r.get('t15_return', 0) for r in all_results if 't15_return' in r]
    all_max = [r.get('max_return', 0) for r in all_results if 'max_return' in r]
    
    t1_w = len([r for r in all_t1 if r > 0]) if all_t1 else 0
    t3_w = len([r for r in all_t3 if r > 0]) if all_t3 else 0
    t5_w = len([r for r in all_t5 if r > 0]) if all_t5 else 0
    f15_w = len([r for r in all_f15 if r > 0]) if all_f15 else 0
    
    print(f"  总推荐: {len(all_results)}只次 | {len(set(r['date'] for r in all_results))}天")
    print(f"")
    if all_t1: print(f"  T+1 胜率: {t1_w}/{len(all_t1)} = {t1_w/len(all_t1)*100:.1f}% | 均: {np.mean(all_t1):+.2f}%")
    if all_t3: print(f"  T+3 胜率: {t3_w}/{len(all_t3)} = {t3_w/len(all_t3)*100:.1f}% | 均: {np.mean(all_t3):+.2f}%")
    if all_t5: print(f"  T+5 胜率: {t5_w}/{len(all_t5)} = {t5_w/len(all_t5)*100:.1f}% | 均: {np.mean(all_t5):+.2f}%")
    if all_f15: print(f"  T+15胜率: {f15_w}/{len(all_f15)} = {f15_w/len(all_f15)*100:.1f}% | 均: {np.mean(all_f15):+.2f}%")
    if all_max: print(f"  5日最高均值: {np.mean(all_max):+.2f}%")
    
    # 止损统计（任一天跌>6%）
    stops = sum(1 for r in all_results if any(d <= -6 for d in r.get('daily_returns', [])))
    profits = sum(1 for r in all_results if any(d >= 5 for d in r.get('daily_returns', [])))
    print(f"")
    print(f"  📌 止损止盈:")
    print(f"     🔴 触发止损(跌>6%): {stops}次/{len(all_results)} = {stops/len(all_results)*100:.1f}%")
    print(f"     🟢 触发止盈(涨>5%): {profits}次/{len(all_results)} = {profits/len(all_results)*100:.1f}%")
    
    # TP/SL条件单模拟（v11.0）
    if strategy in ('v10.0', 'v12.0'):
        tp_sl_data = [r['tp_sl'] for r in all_results if 'tp_sl' in r]
        if tp_sl_data:
            tp_wins = sum(1 for t in tp_sl_data if t['tp_hit'])
            sl_losses = sum(1 for t in tp_sl_data if t['sl_hit'])
            no_hit = sum(1 for t in tp_sl_data if not t['tp_hit'] and not t['sl_hit'])
            avg_exit_day = np.mean([t['exit_day'] for t in tp_sl_data]) if tp_sl_data else 0
            sl_pct = tp_sl_data[0]['sl_pct'] if tp_sl_data else 6
            # 计算总收益
            total_pnl = tp_wins * 5 - sl_losses * sl_pct + sum(t['exit_return'] for t in tp_sl_data if not t['tp_hit'] and not t['sl_hit'])
            avg_pnl = total_pnl / len(tp_sl_data) if tp_sl_data else 0
            print(f"")
            print(f"  ⚡ 条件单模拟(止盈+5%/止损-{sl_pct}%):")
            print(f"     🟢 止盈: {tp_wins}次 = {tp_wins/len(tp_sl_data)*100:.1f}%")
            print(f"     🔴 止损: {sl_losses}次 = {sl_losses/len(tp_sl_data)*100:.1f}%")
            print(f"     ⚪ T+3平: {no_hit}次 = {no_hit/len(tp_sl_data)*100:.1f}%")
            print(f"     💰 总收益: {total_pnl:+.1f}% | 均每笔: {avg_pnl:+.2f}%")
            print(f"     均持仓: {avg_exit_day:.1f}天")
            if total_pnl > 0:
                print(f"     ✅ 策略总体盈利!")
    
    # 平均最大收益
    avg_max = np.mean([r.get('max_return', 0) for r in all_results]) if all_results else 0
    avg_min = np.mean([r.get('min_return', 0) for r in all_results]) if all_results else 0
    print(f"     均最大收益: {avg_max:+.2f}% | 均最大回撤: {avg_min:+.2f}%")
    
    return all_results


if __name__ == '__main__':
    print("加载全A股K线数据...")
    all_klines = load_all_klines()
    print(f"  已加载 {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines, 600)
    print(f"  交易日: {trading_dates[0]} ~ {trading_dates[-1]} ({len(trading_dates)}天)")
    
    # 回测 v10.0（横截面排名多因子）
    r1 = backtest_strategy(all_klines, trading_dates, 'v10.0', threshold=30)

    # 回测 v12.0（条件单TP+5%/SL-6%）
    r2 = backtest_strategy(all_klines, trading_dates, 'v12.0', threshold=30)

    # 双版对比
    print(f"\n{'='*65}")
    print(f"  🏆 v10.0 vs v12.0 条件单对比")
    print(f"{'='*65}")

    D = lambda r, k: [x[k] for x in r if k in x] if r else []
    ss = []
    for r, nm in [(r1,'v10.0多因子'),(r2,'v12.0条件单')]:
        if not r: ss.append((nm, None)); continue
        t1 = D(r, 't1_return')
        t3 = D(r, 't3_return')
        t5 = D(r, 't5_return')
        stops = sum(1 for x in r if any(d <= -6 for d in x.get('daily_returns', [])))
        amax = np.mean([x.get('max_return',0) for x in r]) if r else 0
        # 条件单统计
        tp_sl = [x['tp_sl'] for x in r if 'tp_sl' in x]
        tp_w = sum(1 for t in tp_sl if t['tp_hit'])
        sl_l = sum(1 for t in tp_sl if t['sl_hit'])
        ss.append((nm, {'n':len(r), 't1':t1, 't3':t3, 't5':t5, 'stops':stops, 'amax':amax, 'tp_w':tp_w, 'sl_l':sl_l, 'tp_sl_n':len(tp_sl)}))

    for label, key in [('样本数','n'),('T+1','t1'),('T+3','t3'),('T+5','t5')]:
        row = f"  {label:<8}"
        for nm, s in ss:
            if not s: row += f"  {'-':>20}"; continue
            if key == 'n': row += f"  {s['n']:>20}"; continue
            d = s[key]
            w = sum(1 for x in d if x > 0)
            n = len(d)
            r_str = f"  {w}/{n}={w/n*100:5.1f}%" if n else "  -"
            row += f" {r_str:>20}"
        print(row)
    row = f"  {'止损率':<8}"
    for nm, s in ss:
        if not s: row += f"  {'-':>20}"
        else: row += f"  {s['stops']}/{s['n']}={s['stops']/s['n']*100:5.1f}%"
    print(row)
    row = f"  {'均最高':<8}"
    for nm, s in ss:
        if not s: row += f"  {'-':>20}"
        else: row += f"  {s['amax']:>+8.2f}%  "
    print(row)
    # 条件单对比
    print(f"\n  ⚡ 条件单结果(止盈+5%/止损-6%):")
    for nm, s in ss:
        if not s or s['tp_sl_n'] == 0: continue
        wr = s['tp_w']/s['tp_sl_n']*100
        print(f"  {nm:20s}: TP{s['tp_w']}/{s['tp_sl_n']}={wr:5.1f}% | SL{s['sl_l']}/{s['tp_sl_n']}={s['sl_l']/s['tp_sl_n']*100:5.1f}%")
