#!/usr/bin/env python3
"""
A股多因子量化系统 v1.0 - 主入口
"""
import os, sys, json, time
import numpy as np
import pandas as pd

CACHE_DIR = "/root/data/daily_cache_tdx"
OUTPUT_DIR = "/var/www/html/h5/quant/system/data/results"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TP=5.0; SL=4.0; HOLD=5; MIN_SCORE=6; TOP_N=5

def load():
    print("加载数据...")
    t0 = time.time()
    dfs = []
    for f in os.listdir(CACHE_DIR):
        if f.endswith('.pkl'):
            try:
                df = pd.read_pickle(os.path.join(CACHE_DIR, f))
                if len(df) >= 100: dfs.append(df)
            except: pass
    full = pd.concat(dfs, ignore_index=True).sort_values(['code','date']).reset_index(drop=True)
    for c in ['open','close','high','low','volume','pctChg']:
        if c in full.columns: full[c] = pd.to_numeric(full[c], errors='coerce')
    print(f"  {len(dfs)}只 {len(full)}行 {time.time()-t0:.1f}s")
    return full

def factors(full):
    print("计算因子...")
    t0 = time.time()
    g = full.groupby('code')
    
    # 动量
    full['mom_5'] = g['close'].transform(lambda x: x/x.shift(5)-1)
    full['mom_20'] = g['close'].transform(lambda x: x/x.shift(20)-1)
    full['rev_5'] = -full['mom_5']
    
    # 波动率
    full['volatility'] = g['pctChg'].transform(lambda x: x.rolling(20).std())
    
    # 量比
    full['turnover'] = g['volume'].transform(lambda x: x / x.rolling(5).mean())
    
    # 振幅
    full['amplitude'] = (full['high'] - full['low']) / full['close'].shift(1)
    
    # 价格位置
    full['pp'] = g['close'].transform(lambda x: (x-x.rolling(20).min())/(x.rolling(20).max()-x.rolling(20).min()))
    
    # MACD
    full['ema12'] = g['close'].transform(lambda x: x.ewm(span=12,adjust=False).mean())
    full['ema26'] = g['close'].transform(lambda x: x.ewm(span=26,adjust=False).mean())
    full['dif'] = full['ema12'] - full['ema26']
    full['dea'] = g['dif'].transform(lambda x: x.ewm(span=9,adjust=False).mean())
    full['macd'] = 2*(full['dif']-full['dea'])
    
    # RSI (向量化)
    delta = g['close'].transform(lambda x: x.diff())
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    ag = gain.groupby(full['code']).transform(lambda x: x.rolling(14).mean())
    al = loss.groupby(full['code']).transform(lambda x: x.rolling(14).mean())
    full['rsi'] = 100 - 100/(1 + ag/al.replace(0,np.nan))
    
    # 布林带
    full['boll_mid'] = g['close'].transform(lambda x: x.rolling(20).mean())
    full['boll_std'] = g['close'].transform(lambda x: x.rolling(20).std())
    full['boll_pos'] = (full['close']-full['boll_mid'])/(full['boll_std']*2).replace(0,np.nan)
    
    # 均线
    full['ma5'] = g['close'].transform(lambda x: x.rolling(5).mean())
    full['ma10'] = g['close'].transform(lambda x: x.rolling(10).mean())
    full['ma20'] = g['close'].transform(lambda x: x.rolling(20).mean())
    full['ma60'] = g['close'].transform(lambda x: x.rolling(60).mean())
    full['ma_align'] = ((full['ma5']>full['ma10']).astype(int)+(full['ma10']>full['ma20']).astype(int)+(full['ma20']>full['ma60']).astype(int))
    
    # 资金流向
    hl = (full['high']-full['low']).replace(0,np.nan)
    mf = ((full['close']-full['low'])-(full['high']-full['close']))/hl*full['volume']
    full['mf'] = mf.groupby(full['code']).transform(lambda x: x.rolling(20).sum()) / g['volume'].transform(lambda x: x.rolling(20).sum())
    
    print(f"  13因子完成 {time.time()-t0:.1f}s")
    return full

def score(day):
    s = pd.Series(50.0, index=day.index)
    m = day['mom_5']; s += np.where((m>=0.02)&(m<=0.05),8,np.where((m>0)&(m<0.02),4,np.where(m>0.05,-5,0)))
    t = day['turnover']; s += np.where((t>=1.3)&(t<=3),10,np.where((t>=1)&(t<1.3),5,np.where(t>5,-8,0)))
    s += np.where(day['macd']>0,6,-3)
    r = day['rsi']; s += np.where((r>=45)&(r<=65),6,np.where(r>75,-8,np.where(r<30,3,0)))
    bp = day['boll_pos']; s += np.where((bp>=-0.3)&(bp<=0.3),4,np.where(bp>0.8,-5,0))
    mf = day['mf']; s += np.where(mf>0.1,6,np.where(mf<-0.1,-4,0))
    s += np.where(day['ma_align']>=2,5,0)
    a = day['amplitude']; s += np.where((a>=0.02)&(a<=0.05),3,np.where(a>0.08,-5,0))
    s += np.where(day['rev_5']>0.03,4,0)
    return s.clip(0,100).round(1)

def backtest(full):
    print("回测...")
    t0 = time.time()
    dates = sorted(full['date'].unique())[60:]
    trades = []
    
    for di, d in enumerate(dates):
        day = full[full['date']==d]
        if len(day) < 100: continue
        
        day = day.copy()
        day['score'] = score(day)
        picks = day[day['score']>=MIN_SCORE].nlargest(TOP_N,'score')
        
        for _, p in picks.iterrows():
            fut = full[(full['code']==p['code'])&(full['date']>d)].head(HOLD)
            entry = p['close']; hit='HOLD'; ex=entry; ret=0.0
            for _, f in fut.iterrows():
                if (f['high']-entry)/entry*100>=TP: hit='TP'; ex=entry*(1+TP/100); ret=TP; break
                if (f['low']-entry)/entry*100<=-SL: hit='SL'; ex=entry*(1-SL/100); ret=-SL; break
                ex=f['close']; ret=(ex-entry)/entry*100
            trades.append({'date':d,'code':p['code'],'score':float(p['score']),'entry':round(entry,2),'exit':round(ex,2),'hit':hit,'ret':round(ret,2)})
        
        if (di+1)%100==0: print(f"  [{di+1}/{len(dates)}] {len(trades)}笔")
    
    print(f"  {len(trades)}笔 {time.time()-t0:.1f}s")
    return trades

def main():
    print("="*60)
    print("A股多因子量化系统 v1.0")
    print("="*60)
    
    full = load()
    full = factors(full)
    trades = backtest(full)
    
    total=len(trades); tp=sum(1 for t in trades if t['hit']=='TP'); sl=sum(1 for t in trades if t['hit']=='SL'); hd=total-tp-sl
    rets=[t['ret'] for t in trades]; tr=sum(rets)
    
    print(f"\n总交易:{total} TP:{tp} SL:{sl} HOLD:{hd}")
    print(f"胜率:{tp/total*100:.1f}% 总收益:{tr:+.2f}% 均收益:{np.mean(rets):+.2f}%")
    
    tdf=pd.DataFrame(trades); tdf['month']=tdf['date'].str[:7]
    m=tdf.groupby('month').agg(cnt=('ret','count'),tot=('ret','sum'),win=('hit',lambda x:(x=='TP').mean()*100))
    print("\n月度:")
    for mo,r in m.iterrows():
        b="+"*min(int(abs(r['tot'])),30) if r['tot']>0 else "-"*min(int(abs(r['tot'])),30)
        print(f"  {mo}: {r['cnt']:.0f}笔 {r['tot']:+.1f}% 胜{r['win']:.0f}% {b}")
    
    # 今日推荐
    today=full['date'].max()
    td=full[full['date']==today].copy()
    td['score']=score(td)
    top=td.nlargest(5,'score')[['code','close','pctChg','score']].to_dict('records')
    print(f"\n今日推荐({today}):")
    for t in top: print(f"  {t['code']} ¥{t['close']} 涨{t['pctChg']:.1f}% 评分{t['score']}")
    
    # 保存
    result={
        'version':'v1.0','updated_at':f'{today} 15:00:00',
        'params':{'tp':TP,'sl':SL,'hold':HOLD,'min_score':MIN_SCORE},
        'stats':{'total':total,'tp':tp,'sl':sl,'hold':hd,'win_rate':round(tp/total*100,1),'total_return':round(tr,2),'avg_return':round(np.mean(rets),2)},
        'monthly':m.to_dict('index'),'recommendations':top,'trades':trades[-100:],
        'factors':['momentum_5','momentum_20','reversal_5','volatility','turnover','amplitude','price_position','macd','rsi','bollinger','ma_alignment','money_flow'],
    }
    out=os.path.join(OUTPUT_DIR,'backtest_result.json')
    with open(out,'w',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(f"\n保存:{out}")
    print("[DONE]")

if __name__=='__main__':
    main()
