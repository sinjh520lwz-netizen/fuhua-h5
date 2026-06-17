"""
盘中预测龙虎榜策略: 14:30检测异常→14:50买入→TP/SL/到期卖
信号不依赖龙虎榜API，完全靠盘中特征判断
"""
import json, math
from collections import defaultdict

klines_all = json.load(open('data/all_klines_extended.json'))
buy_data = json.load(open('data/v2/lhb_buy_ext.json'))
sell_data = json.load(open('data/v2/lhb_sell_ext.json'))

jg_events = set()
for r in buy_data:
    d, code = r['TRADE_DATE'][:10], r['SECURITY_CODE']
    if '机构' in str(r.get('OPERATEDEPT_NAME','')):
        if (r.get('BUY',0) or 0) - (r.get('SELL',0) or 0) > 0:
            jg_events.add((code, d))

def is_valid(code):
    return not any(code.startswith(p) for p in ['688','689','300','301','920','8'])

def get_features(code, date):
    ks = klines_all.get(code, {}).get('klines', [])
    if not ks: return None
    dl = [k[0] for k in ks]
    try: idx = dl.index(date)
    except: return None
    if idx < 20: return None
    o, c, h, l, v = float(ks[idx][1]), float(ks[idx][2]), float(ks[idx][3]), float(ks[idx][4]), float(ks[idx][5])
    if o <= 0 or h <= l: return None
    vol20 = sum(float(ks[i][5]) for i in range(idx-19, idx+1)) / 20
    vol5 = sum(float(ks[i][5]) for i in range(idx-4, idx+1)) / 5
    prev_c = float(ks[idx-1][2])
    prev5_c = float(ks[idx-5][2])
    ma5 = sum(float(ks[i][2]) for i in range(idx-4, idx+1)) / 5
    ma20 = sum(float(ks[i][2]) for i in range(idx-19, idx+1)) / 20
    return {
        'chg': (c - prev_c) / prev_c * 100 if prev_c > 0 else 0,
        'vol_ratio': v / vol20 if vol20 > 0 else 0,
        'vol_ratio5': v / vol5 if vol5 > 0 else 0,
        'close_pos': (c - l) / (h - l) * 100,
        'body': abs(c - o) / (h - l) * 100,
        'chg5': (c - prev5_c) / prev5_c * 100 if prev5_c > 0 else 0,
    }

all_dates = sorted(set(d for stock in klines_all.values() for k in stock.get('klines', []) for d in [k[0]]))
eval_dates = [d for d in all_dates if '2025-12' <= d <= '2026-06']
all_codes = [c for c in klines_all.keys() if is_valid(c)]

print(f"预计算特征({len(all_codes)}只, {len(eval_dates)}天)...")
feat_cache = {}
for d in eval_dates:
    for code in all_codes:
        f = get_features(code, d)
        if f: feat_cache[(code, d)] = f

# 预测准确率
print(f"\n=== 预测准确率 ===")
for label, filters in [
    ('chg3+vol1.5', {'chg': 3, 'vol_ratio': 1.5}),
    ('chg4+vol2', {'chg': 4, 'vol_ratio': 2}),
    ('chg4+vol2+pos70', {'chg': 4, 'vol_ratio': 2, 'close_pos': 70}),
    ('chg3+vol2+pos65+body60', {'chg': 3, 'vol_ratio': 2, 'close_pos': 65, 'body': 60}),
]:
    tp_s = fp_s = fn_s = 0
    for d in eval_dates:
        sigs = set(c for c in all_codes if (c,d) in feat_cache and all(feat_cache[(c,d)].get(k,0) >= v for k,v in filters.items()))
        jg = set(c for c,dd in jg_events if dd == d and is_valid(c))
        tp_s += len(sigs & jg); fp_s += len(sigs - jg); fn_s += len(jg - sigs)
    prec = tp_s/(tp_s+fp_s)*100 if tp_s+fp_s > 0 else 0
    rec = tp_s/(tp_s+fn_s)*100 if tp_s+fn_s > 0 else 0
    print(f"  {label:<30} 精准{prec:.0f}% 召回{rec:.0f}% TP{tp_s} FP{fp_s} FN{fn_s}")

def purged_kfold(dates, n=8, embargo=3):
    fs = len(dates) // n
    folds = [dates[i*fs:(i+1)*fs if i < n-1 else len(dates)] for i in range(n)]
    result = []
    for i in range(n):
        train = []
        for j in range(n):
            if j == i: continue
            if j == i-1: train.extend(folds[j][:-embargo] if embargo < len(folds[j]) else [])
            elif j == i+1: train.extend(folds[j][embargo:] if embargo < len(folds[j]) else [])
            else: train.extend(folds[j])
        result.append((train, folds[i]))
    return result

def bt(dates, filters, hold=3, tp=5, sl=-3, max_pos=3, pos_pct=0.3, rr=False):
    sh_map = {k[0]: k for k in klines_all.get('000001', {}).get('klines', [])}
    capital = 15000; positions = []; trades = []; tv = []
    for d in dates:
        sdl = sorted(sh_map.keys())
        regime = 'sideways'
        if d in sdl:
            si = sdl.index(d)
            if si >= 20:
                sc = [float(sh_map[sdl[i]][2]) for i in range(si-19, si+1)]
                ma20 = sum(sc)/len(sc); ma5 = sum(sc[-5:])/5; cur = sc[-1]
                if cur > ma20 and ma5 > ma20: regime = 'bull'
                elif cur < ma20 and ma5 < ma20: regime = 'bear'
        cpct = 0.2 if (rr and regime == 'bear') else pos_pct
        new_pos = []; freed = 0
        for pos in positions:
            ks = klines_all.get(pos['code'], {}).get('klines', [])
            kdl = [k[0] for k in ks]
            try: di = kdl.index(d)
            except: new_pos.append(pos); continue
            h, lo, c = float(ks[di][3]), float(ks[di][4]), float(ks[di][2])
            sp = None; reason = None
            if h >= pos['bp']*(1+tp/100): sp = pos['bp']*(1+tp/100); reason='TP'
            elif lo <= pos['bp']*(1+sl/100): sp = pos['bp']*(1+sl/100); reason='SL'
            try: days = di - kdl.index(pos['buy_date'])
            except: days = 0
            if not sp and days >= hold: sp = c; reason='到期'
            if sp:
                ret = (sp-pos['bp'])/pos['bp']*100 - 0.63
                freed += pos['shares']*sp
                trades.append({'date':d,'ret':ret,'pnl':pos['shares']*pos['bp']*ret/100,'reason':reason})
            else: new_pos.append(pos)
        positions = new_pos; capital += freed
        osl = max_pos - len(positions)
        if osl > 0 and capital > 2000:
            sigs = [(c, feat_cache[(c,d)]) for c in all_codes if (c,d) in feat_cache and all(feat_cache[(c,d)].get(k,0) >= v for k,v in filters.items())]
            sigs.sort(key=lambda x: -x[1]['vol_ratio'])
            for code, f in sigs[:osl]:
                ks = klines_all.get(code, {}).get('klines', [])
                kdl = [k[0] for k in ks]
                try: idx = kdl.index(d)
                except: continue
                bp = float(ks[idx][2])
                if bp <= 0: continue
                alloc = min(capital*cpct, capital-1000)
                shares = int(alloc/bp/100)*100
                if shares >= 100:
                    capital -= shares*bp
                    positions.append({'code':code,'bp':bp,'shares':shares,'buy_date':d})
        pv = 0
        for pos in positions:
            ks = klines_all.get(pos['code'],{}).get('klines',[])
            kdl = [k[0] for k in ks]
            try: di = kdl.index(d); pv += pos['shares']*float(ks[di][4])
            except: pv += pos['shares']*pos['bp']
        tv.append(capital+pv)
    final = capital
    for pos in positions:
        ks = klines_all.get(pos['code'],{}).get('klines',[])
        if ks: final += pos['shares']*float(ks[-1][4])
    peak=0; mdd=0
    for v in tv: peak=max(peak,v); mdd=max(mdd,(peak-v)/peak*100)
    win = sum(1 for t in trades if t['ret']>0)
    return {'trades':len(trades),'win_rate':win/len(trades)*100 if trades else 0,
            'avg_ret':sum(t['ret'] for t in trades)/len(trades) if trades else 0,
            'total_ret':(final-15000)/15000*100,'final':final,'max_dd':mdd,'trades_detail':trades}

pf = purged_kfold(eval_dates, 8, 3)

configs = [
    ('chg3+vol1.5', {'chg': 3, 'vol_ratio': 1.5}, False),
    ('chg3+vol1.5+R', {'chg': 3, 'vol_ratio': 1.5}, True),
    ('chg4+vol2', {'chg': 4, 'vol_ratio': 2}, False),
    ('chg4+vol2+R', {'chg': 4, 'vol_ratio': 2}, True),
    ('chg4+vol2+pos70', {'chg': 4, 'vol_ratio': 2, 'close_pos': 70}, False),
    ('chg4+vol2+pos70+R', {'chg': 4, 'vol_ratio': 2, 'close_pos': 70}, True),
    ('chg3+vol2+pos65+body60', {'chg': 3, 'vol_ratio': 2, 'close_pos': 65, 'body': 60}, False),
    ('chg3+vol2+pos65+body60+R', {'chg': 3, 'vol_ratio': 2, 'close_pos': 65, 'body': 60}, True),
    ('chg5+vol2.5', {'chg': 5, 'vol_ratio': 2.5}, False),
    ('chg5+vol2.5+R', {'chg': 5, 'vol_ratio': 2.5}, True),
]

print(f"\n{'配置':<30} {'笔':>4} {'胜率':>6} {'均收益':>7} {'总收益':>8} {'回撤':>5} {'PurgedCV':>10}")
print("-" * 76)

best = None
for name, filters, rr in configs:
    folds_r = []
    for train, test in pf:
        te = bt(test, filters, rr=rr)
        folds_r.append(te['avg_ret'] > 0 if te['trades'] > 0 else False)
    prof = sum(folds_r)
    full = bt(eval_dates, filters, rr=rr)
    status = "✅" if prof >= 6 else "⚠️" if prof >= 5 else "❌"
    print(f"{status} {name:<28} {full['trades']:>4} {full['win_rate']:>5.1f}% {full['avg_ret']:>+6.2f}% {full['total_ret']:>+7.1f}% {full['max_dd']:>4.1f}% {prof}/8 ({prof*12}%)")
    if not best or prof > best[1] or (prof == best[1] and full["avg_ret"] > best[2]["avg_ret"]):
        best = (name, prof, full, filters, rr)

if best:
    print(f"\n{'='*76}")
    print(f"🏆 {best[0]}")
    p = best[2]
    print(f"  {p['trades']}笔 胜率{p['win_rate']:.1f}% 均{p['avg_ret']:+.2f}%")
    print(f"  ¥15,000 -> ¥{p['final']:,.0f} ({p['total_ret']:+.1f}%) 回撤{p['max_dd']:.1f}%")
    print(f"  Purged CV: {best[1]}/8 ({best[1]*12}%)")
    if p['trades_detail']:
        tp_c = sum(1 for t in p['trades_detail'] if t['reason']=='TP')
        sl_c = sum(1 for t in p['trades_detail'] if t['reason']=='SL')
        print(f"  TP{tp_c} SL{sl_c}")
        monthly = defaultdict(lambda: {'n':0,'wins':0,'pnl':0})
        for t in p['trades_detail']:
            m = t['date'][:7]; monthly[m]['n']+=1
            if t['ret']>0: monthly[m]['wins']+=1
            monthly[m]['pnl']+=t['pnl']
        print(f"  月度:")
        for m in sorted(monthly):
            d = monthly[m]
            print(f"    {m}: {d['n']}笔 胜率{d['wins']/d['n']*100:.0f}% 总{d['pnl']:+.0f}元")
