#!/usr/bin/env python3
"""
JH 因子挖掘引擎 v2 — 优化版
一次遍历全部股票+日期，同时计算所有35个因子
然后批量检验每个因子的预测能力
"""
import json, os, sys, math, time
import numpy as np
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
RESULT_DIR = os.path.join(DATA_DIR, 'factor_mining')
os.makedirs(RESULT_DIR, exist_ok=True)

def load_all():
    fpath = os.path.join(DATA_DIR, 'all_klines_60d.json')
    with open(fpath) as f:
        return json.load(f)

def get_dates(d):
    dc = defaultdict(int)
    for info in d.values():
        for k in info.get('klines', []):
            if isinstance(k, list) and len(k) >= 6:
                dc[k[0]] += 1
    return sorted([dt for dt, c in sorted(dc.items(), key=lambda x:-x[1]) if c > 100 and dt >= '2026-03-01'])

def compute_factors(hist_plus_today):
    """一次性计算所有因子"""
    h = hist_plus_today
    n = len(h)
    if n < 25:
        return None
    
    C = np.array([x['close'] for x in h])
    O = np.array([x['open'] for x in h])
    H = np.array([x['high'] for x in h])
    L = np.array([x['low'] for x in h])
    V = np.array([x['volume'] for x in h])
    R = np.diff(C) / C[:-1]
    
    cp = C[-1]
    f = {}
    
    # 动量 (6)
    if n >= 6: f['mom_5d'] = cp / C[-6] - 1
    if n >= 21: f['mom_20d'] = cp / C[-21] - 1
    if 'mom_5d' in f and 'mom_20d' in f: f['mom_reversal'] = f['mom_5d'] - f['mom_20d']
    if n >= 21:
        wm = sum((20-i)/210.0 * (C[-20+i]/C[-21+i]-1) for i in range(20) if C[-21+i] > 0)
        f['w_momentum'] = wm
    if n >= 21:
        path = sum(abs(C[-i]-C[-i-1]) for i in range(1,21))
        net = abs(cp - C[-21])
        f['path_momentum'] = net / path if path > 1e-10 else 0
    if n >= 11:
        f['mom_accel'] = (cp/C[-6]-1) - (C[-6]/C[-11]-1)
    
    # 波动 (4)
    if len(R) >= 20:
        f['volatility_20'] = float(np.std(R[-20:]))
        vol5 = float(np.std(R[-5:])) if len(R) >= 5 else 0
        f['vol_ratio'] = vol5 / f['volatility_20'] if f['volatility_20'] > 1e-10 else 0
        amp = [(H[-i]-L[-i])/C[-i] for i in range(1,21)]
        f['avg_amplitude'] = float(np.mean(amp))
        std_r = float(np.std(R[-20:]))
        if std_r > 1e-10:
            f['skewness_20'] = float(np.mean((R[-20:]-np.mean(R[-20:]))**3) / (std_r**3))
    
    # 量价 (6)
    if n >= 21:
        c21 = C[-21:-1]; v21 = V[-21:-1]
        if np.std(c21) > 1e-10 and np.std(v21) > 1e-10:
            f['vp_corr'] = float(np.corrcoef(c21, v21)[0,1])
        up_v = [V[-i] for i in range(1,21) if C[-i] > C[-i-1]]
        dn_v = [V[-i] for i in range(1,21) if C[-i] < C[-i-1]]
        f['vol_asymmetry'] = (np.mean(up_v) if up_v else 0) / (np.mean(dn_v) if dn_v else 1)
    if n >= 25:
        f['vol_momentum'] = float(np.mean(V[-5:]) / np.mean(V[-20:]))
    if n >= 6:
        f['volume_change'] = float(V[-1] / np.mean(V[-6:-1])) if np.mean(V[-6:-1]) > 0 else 1
    if n >= 11:
        obv = sum((1 if C[-i]>C[-i-1] else -1) * V[-i]/V[-i-1] if V[-i-1]>0 else (1 if C[-i]>C[-i-1] else -1) for i in range(1,11))
        f['obv_direction'] = obv / 10
    if n >= 6 and np.mean(V[-6:-1]) > 0:
        f['vol_price_diverge'] = (cp/C[-6]-1) * (1 - V[-1]/np.mean(V[-6:-1]))
    
    # 技术形态 (5)
    if n >= 21:
        h20 = np.max(H[-21:-1]); l20 = np.min(L[-21:-1])
        f['price_position'] = (cp-l20)/(h20-l20) if h20>l20 else 0.5
        ma20 = np.mean(C[-21:-1])
        f['ma_deviation'] = (cp-ma20)/ma20 if ma20>0 else 0
    if n >= 25:
        f['ma_slope'] = (np.mean(C[-5:])-np.mean(C[-25:-5]))/np.mean(C[-25:-5]) if np.mean(C[-25:-5])>0 else 0
    if n >= 2:
        f['gap'] = O[-1]/C[-2]-1 if C[-2]>0 else 0
    if H[-1] > L[-1]:
        us = (H[-1]-max(O[-1],C[-1]))/(H[-1]-L[-1])
        ls = (min(O[-1],C[-1])-L[-1])/(H[-1]-L[-1])
        f['shadow_ratio'] = us - ls
    
    # 统计 (5)
    if len(R) >= 20:
        f['max_daily_return'] = float(np.max(R[-20:]))
        f['min_daily_return'] = float(np.min(R[-20:]))
        f['limit_up_count'] = int(sum(1 for r in R[-20:] if r >= 0.095))
        if np.std(R[-20:]) > 1e-10:
            f['info_ratio_20'] = float(np.mean(R[-20:]) / np.std(R[-20:]))
    if n >= 21:
        path = sum(abs(C[-i]-C[-i-1]) for i in range(1,21))
        f['efficiency_ratio'] = abs(cp-C[-21])/path if path>1e-10 else 0
    if n >= 22:
        h20 = np.max(H[-21:-1]); l20 = np.min(L[-21:-1])
        nh = sum(1 for i in range(1,21) if H[-i]>=h20)
        nl = sum(1 for i in range(1,21) if L[-i]<=l20)
        f['hl_asymmetry'] = (nh-nl)/20
    
    # 复合 (4)
    if 'mom_5d' in f and 'vol_momentum' in f: f['vp_compound'] = f['mom_5d'] * f['vol_momentum']
    if 'volatility_20' in f and 'mom_5d' in f: f['low_vol_reversal'] = f['volatility_20'] * f['mom_5d'] * (-1)
    if 'mom_20d' in f and 'efficiency_ratio' in f:
        f['trend_quality'] = (1 if f['mom_20d']>0 else -1) * f['efficiency_ratio'] * abs(f['mom_20d'])
    if n >= 21:
        f['abnormal_vp'] = (cp/np.mean(C[-21:-1])-1) * (V[-1]/np.mean(V[-21:-1])-1)
    
    # 额外 (5)
    if len(R) >= 14:
        g = sum(r for r in R[-14:] if r>0); ls = sum(-r for r in R[-14:] if r<0)
        ag = g/14; al = ls/14
        f['rsi_14'] = 100 - 100/(1+ag/al) if al>0 else 100
    if n >= 21:
        ma = np.mean(C[-21:-1]); std = np.std(C[-21:-1])
        f['boll_pos'] = (cp-(ma-2*std))/(4*std) if std>1e-10 else 0.5
    if n >= 24:
        ma5=np.mean(C[-5:]); ma10=np.mean(C[-10:]); ma20=np.mean(C[-20:])
        all_ma = [v for v in [ma5,ma10,ma20] if v>0]
        f['ma_convergence'] = float(np.std(all_ma)/np.mean(all_ma)) if len(all_ma)>=2 and np.mean(all_ma)>0 else 999
    if n >= 6:
        vwap = sum(C[-i]*V[-i] for i in range(1,6))/sum(V[-i] for i in range(1,6)) if sum(V[-i] for i in range(1,6))>0 else cp
        f['vwap_deviation'] = (cp-vwap)/vwap if vwap>0 else 0
    if n >= 11:
        f['vol_accel'] = float(np.mean(V[-5:])/np.mean(V[-10:-5])) if np.mean(V[-10:-5])>0 else 1
    
    return f

def main():
    print("="*70)
    print("  JH 因子挖掘引擎 v2 (高效版)")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    print("\n加载数据...")
    all_klines = load_all()
    print(f"  {len(all_klines)}只股票")
    
    dates = get_dates(all_klines)
    print(f"  {len(dates)}个交易日: {dates[0]} ~ {dates[-1]}")
    
    # 一次遍历：对每个股票-日期，同时计算全部因子，记录T+1收益
    data_rows = []  # [{date, code, factors:{}}, {t1_return}]
    
    total_stock_days = 0
    t0 = time.time()
    
    for si, (code, info) in enumerate(all_klines.items()):
        name = info.get('name', '')
        if 'ST' in name or '*ST' in name or '退' in name:
            continue
        
        klines = info.get('klines', [])
        klen = len(klines)
        
        for di in range(25, klen - 1):
            today_k = klines[di]
            tomorrow_k = klines[di+1]
            
            if not isinstance(today_k, list) or len(today_k) < 6:
                continue
            if not isinstance(tomorrow_k, list) or len(tomorrow_k) < 6:
                continue
            
            today_date = today_k[0]
            if today_date not in dates:
                continue
            
            # 构建到今日的历史数据
            hist = []
            for k in klines[max(0, di-60):di+1]:
                if isinstance(k, list) and len(k) >= 6:
                    hist.append({
                        'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                        'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5]),
                    })
            
            if len(hist) < 25:
                continue
            
            # 过滤低价/高价
            close = float(today_k[2])
            if close <= 0 or close > 500:
                continue
            
            # 计算全部因子
            factors = compute_factors(hist)
            if not factors:
                continue
            
            # T+1收益
            t1_close = float(tomorrow_k[2])
            t1_return = (t1_close - close) / close
            
            today_open = float(today_k[1])
            day_change = (close / today_open - 1) if today_open > 0 else 0
            
            row = {
                'code': code,
                'name': name,
                'date': today_date,
                'close': close,
                'change': day_change,
                'amount': close * float(today_k[5]) / 10000,
                't1_return': t1_return,
                **factors,
            }
            data_rows.append(row)
            total_stock_days += 1
        
        if (si + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"  进度: {si+1}/{len(all_klines)}只股票, {total_stock_days}条样本, {elapsed:.0f}s")
    
    elapsed = time.time() - t0
    print(f"\n数据采集完成: {total_stock_days}条样本, 耗时{elapsed:.0f}s")
    
    # ===== 检验每个因子 =====
    # 所有因子名（除基础字段外）
    factor_names = [k for k in data_rows[0].keys() if k not in ('code','name','date','close','change','amount','t1_return')]
    print(f"\n发现 {len(factor_names)} 个因子: {', '.join(factor_names)}")
    
    results = []
    for fn in factor_names:
        filtered = []
        for r in data_rows:
            v = r.get(fn)
            if v is not None and np.isfinite(v) and abs(v) < 1000:
                filtered.append(r)
        if len(filtered) < 100:
            results.append({'factor': fn, 'samples': len(filtered), 'error': '样本不足'})
            continue
        
        vals = np.array([r[fn] for r in filtered])
        rets = np.array([r['t1_return'] for r in filtered])
        
        # Rank IC
        rank_v = np.argsort(np.argsort(vals))
        rank_r = np.argsort(np.argsort(rets))
        rank_ic = np.corrcoef(rank_v, rank_r)[0, 1]
        
        # 五分位胜率
        n = len(vals)
        sorted_idx = np.argsort(vals)
        q_size = n // 5
        q_wins = []
        for q in range(5):
            start = q * q_size
            end = min((q+1) * q_size, n)
            if end > start:
                q_wins.append(float(np.mean(rets[sorted_idx[start:end]] > 0)))
        
        top_win = max(q_wins[4], q_wins[3])  # 高因子值的胜率
        bot_win = max(q_wins[0], q_wins[1])   # 低因子值的胜率
        top_mean = float(np.mean(rets[sorted_idx[-q_size:]])) if q_size > 0 else 0
        bot_mean = float(np.mean(rets[sorted_idx[:q_size]])) if q_size > 0 else 0
        
        # 判断方向
        if q_wins[4] > q_wins[0]:
            direction = 1  # 正向
            win_spread = q_wins[4] - q_wins[0]
            best_wins = q_wins[4]
        else:
            direction = -1  # 反向
            win_spread = q_wins[0] - q_wins[4]
            best_wins = q_wins[0]
        
        # 单调性
        mono = 0
        if len(q_wins) >= 4:
            up = sum(1 for i in range(1, len(q_wins)) if q_wins[i] > q_wins[i-1])
            down = sum(1 for i in range(1, len(q_wins)) if q_wins[i] < q_wins[i-1])
            mono = max(up, down) / (len(q_wins) - 1)
        
        results.append({
            'factor': fn,
            'samples': n,
            'rank_ic': round(rank_ic, 4),
            'top5_win': round(q_wins[4]*100, 1) if len(q_wins)>=5 else 0,
            'q4_win': round(q_wins[3]*100, 1) if len(q_wins)>=4 else 0,
            'q3_win': round(q_wins[2]*100, 1) if len(q_wins)>=3 else 0,
            'q2_win': round(q_wins[1]*100, 1) if len(q_wins)>=2 else 0,
            'bot5_win': round(q_wins[0]*100, 1) if len(q_wins)>=1 else 0,
            'win_spread': round(win_spread*100, 1),
            'direction': direction,
            'top_mean': round(top_mean*100, 2),
            'bot_mean': round(bot_mean*100, 2),
            'monotonic': round(mono, 2),
        })
    
    # 按胜率差降序
    results.sort(key=lambda x: x.get('win_spread', 0), reverse=True)
    
    print(f"\n{'='*70}")
    print(f"  🏆 因子有效性排行（按胜率差降序）")
    print(f"{'='*70}")
    print(f"  {'#':>3} {'因子名':<22s} {'IC':>8} {'胜率差':>7} {'Q5胜率':>7} {'Q1胜率':>7} {'方向':>4} {'样本':>7} {'单调':>5}")
    print(f"  {'-'*3} {'-'*22} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*4} {'-'*7} {'-'*5}")
    
    for i, r in enumerate(results[:25]):
        d = '正' if r.get('direction', 0) > 0 else '反'
        print(f"  {i+1:>3} {r['factor']:<22s} {r.get('rank_ic',0):>+8.4f} {r.get('win_spread',0):>7.1f} "
              f"{r.get('top5_win',0):>7.1f} {r.get('bot5_win',0):>7.1f} {d:>4} {r.get('samples',0):>7d} {r.get('monotonic',0):>5.2f}")
    
    # 筛选最佳因子（胜率差>3% 且 样本>500）
    best = [r for r in results if r.get('win_spread',0) > 3 and r.get('samples',0) > 500]
    
    print(f"\n{'='*70}")
    print(f"  ✅ 有效因子（胜率差>3%, 样本>500）: {len(best)}个")
    print(f"{'='*70}")
    for r in best:
        d = '+' if r['direction'] > 0 else '-'
        print(f"  {r['factor']:<22s} IC={r['rank_ic']:+7.4f} 胜差={r['win_spread']:>5.1f}% "
              f"Q5={r['top5_win']:>5.1f}% Q1={r['bot5_win']:>5.1f}% 方向={d} 样本={r['samples']}")
    
    # 保存
    out_path = os.path.join(RESULT_DIR, f'factor_ranking_{datetime.now().strftime("%Y%m%d_%H%M")}.json')
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

if __name__ == '__main__':
    main()
