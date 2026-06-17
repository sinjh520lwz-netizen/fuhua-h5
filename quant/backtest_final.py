#!/usr/bin/env python3
"""五步完整回测 — 从K线直接计算换手率和市值"""
import json, os, sys, time
from collections import defaultdict

DATA_DIR = '/var/www/html/h5/quant/data'

def run():
    t0 = time.time()
    
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        all_data = json.load(f)
    with open(os.path.join(DATA_DIR, 'total_shares.json')) as f:
        shares = json.load(f)
    
    dc = defaultdict(int)
    for info in all_data.values():
        for k in info.get('klines',[]):
            if isinstance(k,list) and len(k)>=6:
                dc[k[0]] += 1
    dates = sorted([d for d,c in dc.items() if c>100])
    test_dates = dates[-80:]
    
    print(f"📂 股票池: {len(all_data)}只, 有总股本: {len(shares)}只")
    print(f"📅 回测: {test_dates[0]}~{test_dates[-1]} ({len(test_dates)}天)", flush=True)
    
    # 预筛: 哪些股票在哪些天通过Step1+2+5
    pass_cache = {}  # {code: [dates]}
    for code, info in all_data.items():
        name = info.get('name','')
        if 'ST' in name or '*ST' in name: continue
        if code.startswith(('300','301','688','920')): continue
        if code not in shares: continue
        
        klines = info.get('klines',[])
        passed = []
        for di, date in enumerate(test_dates):
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
    
    print(f"  通过Step1+2+5的股票: {len(pass_cache)}只", flush=True)
    
    # 正式回测
    TP, SL = 5.0, 4.0
    trades = []
    day_count = 0
    
    for di, date in enumerate(test_dates):
        if di == 0: continue
        
        candidates = []
        for code, pd in pass_cache.items():
            if date not in pd['dates']: continue
            
            total_s = shares[code]
            if total_s <= 0: continue
            
            info = all_data[code]
            klines = info.get('klines',[])
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk: continue
            
            tc = float(tk[2]); tv = float(tk[5])
            
            # Step3: 换手率5-10%
            turn = tv * 100 / total_s * 100
            if not (5 <= turn <= 10): continue
            
            # Step4: 市值50-200亿
            mcap = tc * total_s / 100000000
            if not (50 <= mcap <= 200): continue
            
            chg = (tc/float(klines[ti-1][2])-1)*100
            
            candidates.append({
                'code':code,'name':pd['name'],'change':round(chg,2),
                'price':tc,'turnover':round(turn,2),'mcap':round(mcap,2),
            })
        
        if not candidates: continue
        day_count += 1
        candidates.sort(key=lambda x:abs(x['change']-4))
        
        for p in candidates[:3]:
            nxt = None
            for nd in test_dates[di:]:
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
            tp_p=ep*(1+TP/100); sl_p=ep*(1-SL/100)
            ht='HOLD'
            if nh>=tp_p: ht,ret='TP',TP
            elif nl<=sl_p or ret<=-SL: ht,ret='SL',-SL
            
            trades.append({
                'date':date,'next_date':nxt,'code':p['code'],'name':p['name'],
                'entry':round(ep,2),'exit':round(no,2),'return_pct':round(ret,2),
                'hit_type':ht,'change':p['change'],'turnover':p['turnover'],'mcap':p['mcap'],
            })
        
        if (di+1)%10==0:
            print(f"  日{di+1}/{len(test_dates)-1} 交易{len(trades)}笔", flush=True)
    
    total=len(trades)
    elapsed=time.time()-t0
    print(f'\n{"="*65}', flush=True)
    print(f'📊 五步完整回测 (耗时{elapsed:.0f}s)', flush=True)
    print(f'{"="*65}', flush=True)
    print(f'  区间: {test_dates[1]}~{test_dates[-1]} ({len(test_dates)-1}天)', flush=True)
    print(f'  有票天数: {day_count}天', flush=True)
    
    if total==0:
        print('\n❌ 无交易', flush=True)
        return
    
    tp_c=sum(1 for t in trades if t['hit_type']=='TP')
    sl_c=sum(1 for t in trades if t['hit_type']=='SL')
    hd_c=sum(1 for t in trades if t['hit_type']=='HOLD')
    wins=[t for t in trades if t['return_pct']>0]
    wr=len(wins)/total*100
    tr=sum(t['return_pct'] for t in trades)
    
    print(f'  总交易: {total}笔 ({total/max(day_count,1):.1f}笔/天)', flush=True)
    print(f'  TP+{TP}%: {tp_c} ({tp_c/total*100:.1f}%)', flush=True)
    print(f'  SL-{SL}%: {sl_c} ({sl_c/total*100:.1f}%)', flush=True)
    print(f'  HOLD: {hd_c} ({hd_c/total*100:.1f}%)', flush=True)
    print(f'  胜率: {wr:.1f}%', flush=True)
    print(f'  总收益: {tr:+.2f}%', flush=True)
    print(f'  平均单笔: {tr/total:+.2f}%', flush=True)
    max_w=max(t['return_pct'] for t in wins) if wins else 0
    max_l=min(t['return_pct'] for t in trades) if trades else 0
    print(f'  最大盈利: +{max_w:.2f}%  最大亏损: {max_l:.2f}%', flush=True)
    
    print(f'\n📋 交易明细 (最近15笔):', flush=True)
    for t in trades[-15:]:
        ic={'TP':'🟢','SL':'🔴','HOLD':'🟡'}.get(t['hit_type'],'⚪')
        print(f'  {ic} {t["date"]} {t["name"]}({t["code"]}) +{t["change"]}% → {t["return_pct"]:+.2f}% [{t["hit_type"]}]', flush=True)
    
    out=os.path.join(DATA_DIR,'backtest_standard_full.json')
    with open(out,'w') as f:
        json.dump({
            'range':f'{test_dates[1]}~{test_dates[-1]}','days':len(test_dates)-1,
            'days_with_picks':day_count,'total_trades':total,
            'tp':tp_c,'sl':sl_c,'hold':hd_c,
            'tp_pct':round(tp_c/total*100,1),'sl_pct':round(sl_c/total*100,1),
            'hold_pct':round(hd_c/total*100,1),
            'win_rate':round(wr,1),'total_return':round(tr,2),
            'avg_return':round(tr/total,2),
            'params':{'tp':TP,'sl':SL},
            'trades':trades[-50:],
        }, f, ensure_ascii=False, indent=2)
    print(f'\n💾 {out}', flush=True)

if __name__=='__main__':
    run()
