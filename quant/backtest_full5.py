#!/usr/bin/env python3
"""五步完整回测 — 从K线直接计算换手率和市值"""
import json, os, sys, time
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

def run():
    print("=" * 65)
    print("📊 五步标准化选股 · 完整历史回测（K线直接计算）")
    print("=" * 65)
    t0 = time.time()
    
    # 加载K线
    with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
        all_data = json.load(f)
    
    # 交易日
    dc = defaultdict(int)
    for info in all_data.values():
        for k in info.get('klines',[]):
            if isinstance(k,list) and len(k)>=6:
                dc[k[0]] += 1
    dates = sorted([d for d,c in dc.items() if c>100])
    test_dates = dates[-80:]
    print(f"📂 股票池: {len(all_data)}只")
    print(f"📅 区间: {test_dates[0]}~{test_dates[-1]} ({len(test_dates)}天)")
    
    # 第一阶段：找出哪些股票通过前三步（涨幅、量比、涨停）
    # 同时收集它们的基本数据
    stock_data = {}  # {code: {klines, passes_count, ...}}
    
    print("\n🔍 第一阶段: 扫描通过Step1+2+5的股票...")
    
    for code, info in all_data.items():
        name = info.get('name','')
        if 'ST' in name or '*ST' in name: continue
        if code.startswith(('300','301','688','920')): continue
        
        klines = info.get('klines',[])
        passed_dates = []
        
        for di, date in enumerate(test_dates):
            if di == 0: continue
            
            # 找当天K线
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk or ti<1: continue
            
            pc = float(klines[ti-1][2]); tc = float(tk[2]); tv = float(tk[5])
            if pc<=0: continue
            
            # Step1: 涨幅3-5%
            chg = (tc/pc-1)*100
            if not (3<=chg<=5): continue
            
            # Step2: 量比>1
            vols = [float(klines[j][5]) for j in range(max(0,ti-6),ti)]
            av = sum(vols)/len(vols) if vols else 1
            if not (tv/av>1 if av>0 else False): continue
            
            # Step5: 近20天涨停
            lu = 0
            for j in range(max(0,ti-21),ti):
                if j>0 and float(klines[j][2])>=float(klines[j-1][2])*1.095:
                    lu+=1
            if not (lu>0): continue
            
            passed_dates.append(date)
        
        if passed_dates:
            stock_data[code] = {
                'name': name,
                'passed_dates': passed_dates,
            }
    
    print(f"   结果: {len(stock_data)}只股票至少有一天通过前三步")
    
    # 第二阶段：获取这些股票的总股本
    print(f"\n📦 第二阶段: 获取总股本 ({len(stock_data)}只)...")
    
    shares_cache = {}
    if os.path.exists(os.path.join(DATA_DIR, 'total_shares.json')):
        with open(os.path.join(DATA_DIR, 'total_shares.json')) as f:
            shares_cache = json.load(f)
    print(f"   缓存已有: {len(shares_cache)}只")
    
    need_shares = [c for c in stock_data if c not in shares_cache]
    print(f"   还需查询: {len(need_shares)}只")
    
    if need_shares:
        import baostock as bs
        bs.login()
        for i, code in enumerate(need_shares):
            prefix = 'sh' if code.startswith('6') else 'sz'
            try:
                rs = bs.query_profit_data(code=f'{prefix}.{code}', year=2026, quarter=1)
                while rs.next():
                    row = rs.get_row_data()
                    if len(row)>9 and row[9]:
                        shares_cache[code] = float(row[9])
            except:
                pass
            if (i+1)%20==0:
                print(f"     进度: {i+1}/{len(need_shares)} ({len(shares_cache)}个)")
                # 每20只存一次
                with open(os.path.join(DATA_DIR, 'total_shares.json'),'w') as f:
                    json.dump(shares_cache, f)
            time.sleep(0.1)

        bs.logout()
        with open(os.path.join(DATA_DIR, 'total_shares.json'),'w') as f:
            json.dump(shares_cache, f)
    
    print(f"   总股本: {len([c for c in stock_data if c in shares_cache])}/{len(stock_data)}只有数据")
    
    # 第三阶段：完整5步回测
    print(f"\n📈 第三阶段: 完整5步回测...")
    
    TP, SL = 5.0, 4.0
    trades = []
    day_count = 0
    skipped_no_shares = 0
    
    for di, date in enumerate(test_dates):
        if di == 0: continue
        
        candidates = []
        for code, sd in stock_data.items():
            # 只查当天有通过的股票
            if date not in sd['passed_dates']:
                continue
            if code not in shares_cache:
                skipped_no_shares += 1
                continue
            
            total_shares = shares_cache[code]
            if total_shares <= 0:
                continue
            
            info = all_data[code]
            klines = info.get('klines',[])
            
            tk = None; ti = -1
            for i,k in enumerate(klines):
                if isinstance(k,list) and len(k)>=6 and k[0]==date:
                    tk=k; ti=i; break
            if not tk or ti<1: continue
            
            tc = float(tk[2]); tv = float(tk[5])
            
            # Step3: 换手率5-10%
            turn = tv * 100 / total_shares * 100
            if not (5 <= turn <= 10): continue
            
            # Step4: 市值50-200亿
            mcap = tc * total_shares / 100000000
            if not (50 <= mcap <= 200): continue
            
            pc = float(klines[ti-1][2])
            chg = (tc/pc-1)*100
            
            candidates.append({
                'code':code,'name':sd['name'],'change':round(chg,2),
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
            print(f"   日{di+1}/{len(test_dates)-1} 交易{len(trades)}笔")
    
    # 结果
    total=len(trades)
    elapsed=time.time()-t0
    print(f'\n{"="*65}')
    print(f'📊 五步完整回测 (耗时{elapsed:.0f}s)')
    print(f'{"="*65}')
    print(f'  区间: {test_dates[1]}~{test_dates[-1]} ({len(test_dates)-1}天)')
    print(f'  有票天数: {day_count}天, 缺股本跳过: {skipped_no_shares}次')
    
    if total==0:
        print('\n❌ 无交易')
        return
    
    tp_c=sum(1 for t in trades if t['hit_type']=='TP')
    sl_c=sum(1 for t in trades if t['hit_type']=='SL')
    hd_c=sum(1 for t in trades if t['hit_type']=='HOLD')
    wins=[t for t in trades if t['return_pct']>0]
    wr=len(wins)/total*100
    tr=sum(t['return_pct'] for t in trades)
    
    print(f'  总交易: {total}笔 ({total/max(day_count,1):.1f}笔/天)')
    print(f'  TP+{TP}%: {tp_c} ({tp_c/total*100:.1f}%)')
    print(f'  SL-{SL}%: {sl_c} ({sl_c/total*100:.1f}%)')
    print(f'  HOLD: {hd_c} ({hd_c/total*100:.1f}%)')
    print(f'  胜率: {wr:.1f}%')
    print(f'  总收益: {tr:+.2f}%')
    print(f'  平均单笔: {tr/total:+.2f}%')
    max_w=max(t['return_pct'] for t in wins) if wins else 0
    max_l=min(t['return_pct'] for t in trades) if trades else 0
    print(f'  最大盈利: +{max_w:.2f}%  最大亏损: {max_l:.2f}%')
    
    print(f'\n📋 交易明细 (最近20笔):')
    for t in trades[-20:]:
        ic={'TP':'🟢','SL':'🔴','HOLD':'🟡'}.get(t['hit_type'],'⚪')
        print(f'  {ic} {t["date"]} {t["name"]}({t["code"]}) +{t["change"]}% → {t["return_pct"]:+.2f}% [{t["hit_type"]}] 换手{t["turnover"]}% 市值{t["mcap"]}亿')
    
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
    print(f'\n💾 {out}')

if __name__=='__main__':
    run()
