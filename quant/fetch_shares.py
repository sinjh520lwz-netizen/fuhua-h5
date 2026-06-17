#!/usr/bin/env python3
"""补齐总股本 - 仅查通过Step1+2+5但缺失的股票"""
import json, os, time, sys
from collections import defaultdict

DATA_DIR = '/var/www/html/h5/quant/data'

# 1. 找出通过Step1+2+5的股票
with open(os.path.join(DATA_DIR, 'all_klines_60d.json')) as f:
    all_data = json.load(f)

dc = defaultdict(int)
for info in all_data.values():
    for k in info.get('klines',[]):
        if isinstance(k,list) and len(k)>=6:
            dc[k[0]] += 1
dates = sorted([d for d,c in dc.items() if c>100])[-80:]

need = set()
for code, info in all_data.items():
    name = info.get('name','')
    if 'ST' in name or '*ST' in name: continue
    if code.startswith(('300','301','688','920')): continue
    klines = info.get('klines',[])
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
        need.add(code)
        break

# 2. 加载已有缓存
with open(os.path.join(DATA_DIR, 'total_shares.json')) as f:
    shares = json.load(f)

missing = [c for c in need if c not in shares]
print(f"PASS_1235={len(need)} HAVE={len([c for c in need if c in shares])} MISSING={len(missing)}", flush=True)

if not missing:
    print("DONE_ALL", flush=True)
    sys.exit(0)

# 3. 查询缺失的
import baostock as bs
bs.login()
for i, code in enumerate(missing):
    prefix = 'sh' if code.startswith('6') else 'sz'
    try:
        rs = bs.query_profit_data(code=f'{prefix}.{code}', year=2026, quarter=1)
        while rs.next():
            row = rs.get_row_data()
            if len(row) > 9 and row[9]:
                shares[code] = float(row[9])
    except:
        pass
    time.sleep(0.01)
    if (i+1) % 50 == 0 or (i+1) == len(missing):
        with open(os.path.join(DATA_DIR, 'total_shares.json'), 'w') as f:
            json.dump(shares, f)
        print(f"PROGRESS={i+1}/{len(missing)} TOTAL={len(shares)}", flush=True)

bs.logout()

final = [c for c in need if c in shares]
print(f"DONE={len(final)}/{len(need)}", flush=True)
