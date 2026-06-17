#!/usr/bin/env python3
"""
JH 因子交互挖掘 — 找最有预测力的因子组合
核心：两个中等因子的条件组合，可能强于任何单因子
非线性：A高×B中 > A高+B高 才行
"""
import json, os, sys, time
import numpy as np
from collections import defaultdict
from datetime import datetime
from itertools import combinations

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

def compute_factors(hist):
    """同因子挖掘v2一样的函数，但加入更多因子"""
    h = hist; n = len(h)
    if n < 25: return None
    C = np.array([x['close'] for x in h])
    O = np.array([x['open'] for x in h])
    H = np.array([x['high'] for x in h])
    L = np.array([x['low'] for x in h])
    V = np.array([x['volume'] for x in h])
    R = np.diff(C) / C[:-1]
    cp = C[-1]; f = {}
    
    # 已有36个因子（简写，仅保持兼容）
    if n >= 6: f['mom_5d'] = cp/C[-6]-1
    if n >= 21: f['mom_20d'] = cp/C[-21]-1
    if 'mom_5d' in f and 'mom_20d' in f: f['mom_reversal'] = f['mom_5d'] - f['mom_20d']
    if n >= 21: f['path_momentum'] = abs(cp-C[-21])/sum(abs(C[-i]-C[-i-1]) for i in range(1,21)) if sum(abs(C[-i]-C[-i-1]) for i in range(1,21))>1e-10 else 0
    if n >= 11: f['mom_accel'] = (cp/C[-6]-1) - (C[-6]/C[-11]-1)
    if len(R) >= 20: 
        f['volatility_20']=float(np.std(R[-20:]))
        vol5=float(np.std(R[-5:])) if len(R)>=5 else 0
        f['vol_ratio']=vol5/f['volatility_20'] if f['volatility_20']>1e-10 else 0
        f['avg_amplitude']=float(np.mean([(H[-i]-L[-i])/C[-i] for i in range(1,21)]))
        std_r=float(np.std(R[-20:]))
        if std_r>1e-10: f['skewness_20']=float(np.mean((R[-20:]-np.mean(R[-20:]))**3)/(std_r**3))
    if n>=21:
        c21,v21=C[-21:-1],V[-21:-1]
        if np.std(c21)>1e-10 and np.std(v21)>1e-10: f['vp_corr']=float(np.corrcoef(c21,v21)[0,1])
        uv=[V[-i] for i in range(1,21) if C[-i]>C[-i-1]]; dv=[V[-i] for i in range(1,21) if C[-i]<C[-i-1]]
        f['vol_asymmetry']=(np.mean(uv) if uv else 0)/(np.mean(dv) if dv else 1)
    if n>=25: f['vol_momentum']=float(np.mean(V[-5:])/np.mean(V[-20:]))
    if n>=6: f['volume_change']=float(V[-1]/np.mean(V[-6:-1])) if np.mean(V[-6:-1])>0 else 1
    if n>=11:
        obv=sum((1 if C[-i]>C[-i-1] else -1)*V[-i]/V[-i-1] if V[-i-1]>0 else (1 if C[-i]>C[-i-1] else -1) for i in range(1,11))
        f['obv_direction']=obv/10
    if n>=6 and np.mean(V[-6:-1])>0: f['vol_price_diverge']=(cp/C[-6]-1)*(1-V[-1]/np.mean(V[-6:-1]))
    if n>=21:
        h20=np.max(H[-21:-1]);l20=np.min(L[-21:-1])
        f['price_position']=(cp-l20)/(h20-l20) if h20>l20 else 0.5
        ma20=np.mean(C[-21:-1])
        f['ma_deviation']=(cp-ma20)/ma20 if ma20>0 else 0
    if n>=25: f['ma_slope']=(np.mean(C[-5:])-np.mean(C[-25:-5]))/np.mean(C[-25:-5]) if np.mean(C[-25:-5])>0 else 0
    if n>=2: f['gap']=O[-1]/C[-2]-1 if C[-2]>0 else 0
    if H[-1]>L[-1]:
        us=(H[-1]-max(O[-1],C[-1]))/(H[-1]-L[-1]); ls=(min(O[-1],C[-1])-L[-1])/(H[-1]-L[-1])
        f['shadow_ratio']=us-ls
    if len(R)>=20:
        f['max_daily_return']=float(np.max(R[-20:])); f['min_daily_return']=float(np.min(R[-20:]))
        f['limit_up_count']=int(sum(1 for r in R[-20:] if r>=0.095))
        if np.std(R[-20:])>1e-10: f['info_ratio_20']=float(np.mean(R[-20:])/np.std(R[-20:]))
        path=sum(abs(C[-i]-C[-i-1]) for i in range(1,21))
        f['efficiency_ratio']=abs(cp-C[-21])/path if path>1e-10 else 0
        nh=sum(1 for i in range(1,21) if H[-i]>=h20); nl=sum(1 for i in range(1,21) if L[-i]<=l20)
        f['hl_asymmetry']=(nh-nl)/20
    if 'mom_5d' in f and 'vol_momentum' in f: f['vp_compound']=f['mom_5d']*f['vol_momentum']
    if 'volatility_20' in f and 'mom_5d' in f: f['low_vol_reversal']=f['volatility_20']*f['mom_5d']*(-1)
    if 'mom_20d' in f and 'efficiency_ratio' in f: f['trend_quality']=(1 if f['mom_20d']>0 else -1)*f['efficiency_ratio']*abs(f['mom_20d'])
    if n>=21: f['abnormal_vp']=(cp/np.mean(C[-21:-1])-1)*(V[-1]/np.mean(V[-21:-1])-1)
    if len(R)>=14:
        g=sum(r for r in R[-14:] if r>0); ls2=sum(-r for r in R[-14:] if r<0)
        ag,al=g/14,ls2/14; f['rsi_14']=100-100/(1+ag/al) if al>0 else 100
    if n>=21:
        ma=np.mean(C[-21:-1]);std=np.std(C[-21:-1])
        f['boll_pos']=(cp-(ma-2*std))/(4*std) if std>1e-10 else 0.5
    if n>=24:
        m5=np.mean(C[-5:]);m10=np.mean(C[-10:]);m20=np.mean(C[-20:])
        all_ma=[v for v in [m5,m10,m20] if v>0]
        f['ma_convergence']=float(np.std(all_ma)/np.mean(all_ma)) if len(all_ma)>=2 and np.mean(all_ma)>0 else 999
    if n>=6:
        vwap=sum(C[-i]*V[-i] for i in range(1,6))/sum(V[-i] for i in range(1,6)) if sum(V[-i] for i in range(1,6))>0 else cp
        f['vwap_deviation']=(cp-vwap)/vwap if vwap>0 else 0
    if n>=11: f['vol_accel']=float(np.mean(V[-5:])/np.mean(V[-10:-5])) if np.mean(V[-10:-5])>0 else 1
    
    # ===== 新加交互型因子 =====
    # 37. 量价背离强化 (price_position 和 vp_corr 的交互)
    if 'price_position' in f and 'vp_corr' in f:
        f['vp_divergence_boost'] = f['price_position'] * f['vp_corr'] * (-1)
    
    # 38. 均线支撑强度 (ma_deviation 和 volatility_20 的交互)
    if 'ma_deviation' in f and 'volatility_20' in f:
        f['ma_support'] = f['ma_deviation'] / (f['volatility_20'] + 0.01)
    
    # 39. 低波动趋势 (volatility_20 和 mom_20d 的交互)
    if 'volatility_20' in f and 'mom_20d' in f:
        f['low_vol_trend'] = f['mom_20d'] / (f['volatility_20'] + 0.01)
    
    # 40. 量价确认信号 (vol_momentum 和 price_position 的交互)
    if 'vol_momentum' in f and 'price_position' in f:
        f['vp_confirm'] = f['vol_momentum'] * (1 - f['price_position'])
    
    # 41. 短期强度综合 (mom_5d 和 gap 的结合)
    if 'mom_5d' in f and 'gap' in f:
        f['short_strength'] = f['mom_5d'] * (1 + f['gap']) if f['gap'] > 0 else f['mom_5d']
    
    # 42. RSI趋势背离 (rsi_14 和 ma_deviation 的背离检测)
    if 'rsi_14' in f and 'ma_deviation' in f:
        rsi_norm = (f['rsi_14'] - 50) / 50
        ma_norm = np.tanh(f['ma_deviation'] * 10)
        f['rsi_ma_divergence'] = rsi_norm - ma_norm  # 正=RSI比价格更强
    
    # 43. 缩量企稳 (vol_momentum 和 price_position 的反向组合)
    if 'vol_momentum' in f and 'price_position' in f:
        f['vol_steady'] = (1 - f['vol_momentum']) * (1 - f['price_position'])
    
    # 44. 三条件收紧 (均线粘合+布林收窄+量缩的复合)
    # 用 ma_convergence + boll_pos + 量的组合
    if 'ma_convergence' in f and 'vol_momentum' in f:
        is_tight = f['ma_convergence'] < 8
        is_quiet = 0.8 < f['vol_momentum'] < 1.5
        f['setup_quality'] = (1.0 if is_tight else 0) + (1.0 if is_quiet else 0)
    
    # 45. AUROC — 上涨日占比的波动调整
    if len(R) >= 20:
        up_ratio = sum(1 for r in R[-20:] if r > 0) / 20
        vol_norm = f['volatility_20'] / np.mean([abs(r) for r in R[-20:]]) if np.mean([abs(r) for r in R[-20:]]) > 0 else 1
        f['up_ratio_adj'] = up_ratio / (vol_norm + 0.5)
    
    return f

def main():
    print("="*70)
    print("  JH 因子交互挖掘引擎")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  目标: 找到因子组合条件,使胜率>55%")
    print("="*70)
    
    print("\n加载数据...")
    all_klines = load_all()
    print(f"  {len(all_klines)}只股票")
    dates = get_dates(all_klines)
    print(f"  {len(dates)}个交易日")
    
    # 采集数据
    all_rows = []
    t0 = time.time()
    for si, (code, info) in enumerate(all_klines.items()):
        name = info.get('name','')
        if 'ST' in name or '*ST' in name or '退' in name: continue
        klines = info.get('klines',[])
        for di in range(25, len(klines)-1):
            tk = klines[di]; nk = klines[di+1]
            if not isinstance(tk,list) or len(tk)<6 or not isinstance(nk,list) or len(nk)<6: continue
            if tk[0] not in dates: continue
            hist = []
            for k in klines[max(0,di-60):di+1]:
                if isinstance(k,list) and len(k)>=6:
                    hist.append({'date':k[0],'open':float(k[1]),'close':float(k[2]),'high':float(k[3]),'low':float(k[4]),'volume':float(k[5])})
            if len(hist)<25: continue
            close=float(tk[2])
            if close<=0 or close>500: continue
            factors = compute_factors(hist)
            if not factors: continue
            t1_close=float(nk[2])
            all_rows.append({**factors, 't1_win': 1 if t1_close > close else 0, 't1_return': (t1_close-close)/close})
        
        if (si+1)%500==0: print(f"  进度: {si+1}/{len(all_klines)}, {len(all_rows)}样本, {time.time()-t0:.0f}s")
    
    print(f"\n采集完成: {len(all_rows)}条")
    
    # ===== 因子交互挖掘 =====
    factor_names = [k for k in all_rows[0].keys() if k not in ('t1_win','t1_return')]
    print(f"  共{len(factor_names)}个因子")
    
    overall_win = np.mean([r['t1_win'] for r in all_rows])
    print(f"  全样本T+1胜率: {overall_win*100:.1f}%")
    
    # 挖掘两因子条件组合
    print(f"\n{'='*70}")
    print("  挖掘两因子条件组合...")
    print(f"{'='*70}")
    
    # 对每个因子，先计算其百分位
    factor_values = {}
    for fn in factor_names:
        vals = []
        for r in all_rows:
            v = r.get(fn)
            if v is not None and np.isfinite(v) and abs(v) < 1000:
                vals.append(v)
        factor_values[fn] = np.array(vals) if vals else np.array([])
    
    condition_results = []
    
    # 测试每个因子单独的分段
    for fn in factor_names:
        vals = []; wins = []
        for r in all_rows:
            v = r.get(fn)
            if v is not None and np.isfinite(v) and abs(v) < 1000:
                vals.append(v); wins.append(r['t1_win'])
        if len(vals) < 500: continue
        vals = np.array(vals); wins = np.array(wins)
        
        # 10等分
        idx = np.argsort(vals)
        q = len(vals)//10
        for qi in range(10):
            s,e = qi*q, min((qi+1)*q, len(vals))
            if s >= e: continue
            w = np.mean(wins[idx[s:e]])
            condition_results.append({
                'type': 'single',
                'name': fn,
                'condition': f'{fn}_q{qi+1}',
                'range': f'q{qi+1}',
                'win_rate': round(w*100,1),
                'lift': round(w/overall_win, 3),
                'samples': e-s,
            })
    
    # 测试两因子条件组合
    factor_list = [fn for fn in factor_names if len(factor_values.get(fn, [])) >= 1000]
    tested = 0
    best_results = []
    
    # 对每一对因子，预计算百分位
    for f1 in factor_list:
        for f2 in factor_list:
            if f1 >= f2: continue
            tested += 1
            
            # 收集双因子有效行
            pairs = []
            for r in all_rows:
                v1 = r.get(f1); v2 = r.get(f2)
                if v1 is not None and v2 is not None and np.isfinite(v1) and np.isfinite(v2) and abs(v1) < 1000 and abs(v2) < 1000:
                    pairs.append((v1, v2, r['t1_win']))
            if len(pairs) < 500: continue
            
            v1_a = np.array([x[0] for x in pairs])
            v2_a = np.array([x[1] for x in pairs])
            w_a = np.array([x[2] for x in pairs])
            
            p33_1, p67_1 = np.percentile(v1_a, [33, 67])
            p33_2, p67_2 = np.percentile(v2_a, [33, 67])
            
            for r1, r1n in [(0, 'low'), (1, 'mid'), (2, 'high')]:
                for r2, r2n in [(0, 'low'), (1, 'mid'), (2, 'high')]:
                    if r1 == 0: m1 = v1_a < p33_1
                    elif r1 == 1: m1 = (v1_a >= p33_1) & (v1_a <= p67_1)
                    else: m1 = v1_a > p67_1
                    
                    if r2 == 0: m2 = v2_a < p33_2
                    elif r2 == 1: m2 = (v2_a >= p33_2) & (v2_a <= p67_2)
                    else: m2 = v2_a > p67_2
                    
                    combo = m1 & m2
                    if combo.sum() < 100: continue
                    
                    w_rate = w_a[combo].mean()
                    
                    if w_rate > overall_win + 0.03:
                        best_results.append({
                            'type': 'pair', 
                            'condition': f'{f1}_{r1n}∩{f2}_{r2n}',
                            'win_rate': round(w_rate*100, 1),
                            'lift': round(w_rate/overall_win, 3),
                            'samples': int(combo.sum()),
                        })
            
            if tested % 200 == 0:
                print(f"    已测 {tested} 对因子组合...")
    
    # 合并单因子和双因子结果
    condition_results.extend(best_results)
    
    # 排序
    condition_results.sort(key=lambda x: -x['win_rate'])
    
    print(f"\n测试了 {tested} 个条件组合")
    print(f"\n{'='*70}")
    print(f"  🏆 最优条件组合 TOP 20（按胜率）")
    print(f"{'='*70}")
    print(f"  {'#':>3} {'条件':<35s} {'胜率':>6} {'提升':>8} {'样本':>8}")
    print(f"  {'-'*3} {'-'*35} {'-'*6} {'-'*8} {'-'*8}")
    
    for i, r in enumerate(condition_results[:30]):
        print(f"  {i+1:>3} {r['condition']:<35s} {r['win_rate']:>6.1f}% {r['lift']:>+8.2f}x {r['samples']:>8d}")
    
    # 找出胜率>55%的组合
    best_conditions = [r for r in condition_results if r['win_rate'] > 55 and r['samples'] > 200]
    if best_conditions:
        print(f"\n{'='*70}")
        print(f"  ✅ 胜率>55%的条件组合: {len(best_conditions)}个")
        print(f"{'='*70}")
        for r in best_conditions[:20]:
            print(f"  {r['condition']:<35s} {r['win_rate']:>5.1f}% 样本={r['samples']} 提升={r['lift']:.2f}x")
    else:
        print(f"\n❌ 没有找到胜率>55%的条件组合")
        print(f"   最优单条件: {condition_results[0]['condition']} = {condition_results[0]['win_rate']}%")
        print(f"   最优两因子: {condition_results[0]['condition'] if condition_results[0]['type']=='pair' else (condition_results[1]['condition'] if len(condition_results)>1 and condition_results[1]['type']=='pair' else '?')}")
    
    # 保存
    out_path = os.path.join(RESULT_DIR, f'interaction_mining_{datetime.now().strftime("%Y%m%d_%H%M")}.json')
    with open(out_path, 'w') as f:
        json.dump(condition_results[:100], f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

if __name__ == '__main__':
    main()
