#!/usr/bin/env python3
"""参数优化 — 自动对比多组参数"""
import json, os, time
from collections import defaultdict

DATA_DIR = '/var/www/html/h5/quant/data'

def backtest(tp, sl, turn_min, turn_max, mcap_min=50, mcap_max=200):
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        all_data = json.load(f)
    with open(os.path.join(DATA_DIR, 'total_shares.json')) as f:
        shares = json.load(f)
    
    dc = defaultdict(int)
    for info in all_data.values():
        for k in info.get('klines',[]):
            if isinstance(k,list) and len(k)>=6:
                dc[k[0]] += 1
    dates = sorted([d for d,c in dc.items() if c>100])[-80:]
    
    # 预筛Step1+2+5
    pass_cache = {}
    for code, info in all_data.items():
        name = info.get('name','')
        if 'ST' in name or '*ST' in name: continue
        if code.startswith(('300','301','688','920')): continue
        if code not in shares: continue
        klines = info.get('klines',[])
        passed = []
        for di, date in enumerate(dates):
            if di == 0: continue
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk or ti<1: continue
            pc = float(klines[ti-1][2]); tc = float(tk[2]); tv = float(tk[5])
            if pc<=0: continue
            chg = (tc/pc-1)*100
            if not (3<=chg<=5): continue
            vols = [float(klines[j][5]) for j in range(max(0,ti-6),ti)]
            av = sum(vols)/len(vols) if vols else 1
            if not (tv/av>1 if av>0 else False): continue
            lu = 0
            for j in range(max(0,ti-21),ti):
                if j>0 and float(klines[j][2])>=float(klines[j-1][2])*1.095:
                    lu+=1
            if not (lu>0): continue
            passed.append(date)
        if passed:
            pass_cache[code] = {'name': name, 'dates': set(passed)}
    
    trades = []
    day_count = 0
    for di, date in enumerate(dates):
        if di == 0: continue
        candidates = []
        for code, pd in pass_cache.items():
            if date not in pd['dates']: continue
            total_s = shares[code]
            if total_s <= 0: continue
            klines = all_data[code].get('klines',[])
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk: continue
            tc = float(tk[2]); tv = float(tk[5])
            turn = tv * 100 / total_s * 100
            if not (turn_min <= turn <= turn_max): continue
            mcap = tc * total_s / 100000000
            if not (mcap_min <= mcap <= mcap_max): continue
            chg = (tc/float(klines[ti-1][2])-1)*100
            candidates.append({'code':code,'name':pd['name'],'change':round(chg,2),'price':tc})
        
        if not candidates: continue
        day_count += 1
        candidates.sort(key=lambda x:abs(x['change']-4))
        for p in candidates[:3]:
            nxt = None
            for nd in dates[di:]:
                if nd!=date and any(k[0]==nd for k in all_data.get(p['code'],{}).get('klines',[])):
                    nxt=nd; break
            if not nxt: continue
            nk = None
            for k in all_data.get(p['code'],{}).get('klines',[]):
                if isinstance(k,list) and len(k)>=6 and k[0]==nxt:
                    nk=k; break
            if not nk: continue
            ep=p['price']; no=float(nk[1]); nh=float(nk[3]); nl=float(nk[4])
            if ep<=0: continue
            ret=(no/ep-1)*100
            tp_p=ep*(1+tp/100); sl_p=ep*(1-sl/100)
            ht='HOLD'
            if nh>=tp_p: ht,ret='TP',tp
            elif nl<=sl_p or ret<=-sl: ht,ret='SL',-sl
            trades.append({'return_pct':round(ret,2),'hit_type':ht})
    
    total=len(trades)
    if total==0: return {'trades':0,'win_rate':0,'total_return':0,'avg_return':0,'tp_pct':0,'sl_pct':0}
    tp_c=sum(1 for t in trades if t['hit_type']=='TP')
    sl_c=sum(1 for t in trades if t['hit_type']=='SL')
    wins=[t for t in trades if t['return_pct']>0]
    tr=sum(t['return_pct'] for t in trades)
    return {
        'trades':total,'win_rate':round(len(wins)/total*100,1),
        'total_return':round(tr,2),'avg_return':round(tr/total,2),
        'tp_pct':round(tp_c/total*100,1),'sl_pct':round(sl_c/total*100,1),
        'trades_pd':day_count,
    }

param_sets = [
    # 原版
    {'tp':5,'sl':4,'turn_min':5,'turn_max':10,'name':'标准5步(原版)'},
    # 放宽换手
    {'tp':5,'sl':4,'turn_min':3,'turn_max':8,'name':'换手3-8'},
    {'tp':5,'sl':4,'turn_min':4,'turn_max':12,'name':'换手4-12'},
    {'tp':5,'sl':4,'turn_min':3,'turn_max':10,'name':'换手3-10'},
    # 调TP/SL
    {'tp':6,'sl':3,'turn_min':5,'turn_max':10,'name':'TP+6/SL-3'},
    {'tp':6,'sl':4,'turn_min':5,'turn_max':10,'name':'TP+6/SL-4'},
    {'tp':4,'sl':3,'turn_min':5,'turn_max':10,'name':'TP+4/SL-3'},
    # 放宽换手+调TP/SL
    {'tp':6,'sl':3,'turn_min':3,'turn_max':8,'name':'综合6/3+换手3-8'},
    {'tp':5,'sl':3,'turn_min':3,'turn_max':8,'name':'综合5/3+换手3-8'},
    {'tp':6,'sl':3.5,'turn_min':3,'turn_max':10,'name':'综合6/3.5+换手3-10'},
]

print(f"{'参数名':<16} {'交易':<6} {'日均':<6} {'胜率':<8} {'总收益':<10} {'单笔':<8} {'TP%':<8} {'SL%':<8}")
print('-'*80)
for ps in param_sets:
    r = backtest(ps['tp'], ps['sl'], ps['turn_min'], ps['turn_max'])
    dpd = round(r['trades']/max(r['trades_pd'],1),1) if r['trades_pd'] else 0
    print(f"{ps['name']:<16} {r['trades']:<6} {dpd:<6} {r['win_rate']:<7}% {r['total_return']:<+9}% {r['avg_return']:<+7}% {r['tp_pct']:<7}% {r['sl_pct']:<7}%")
