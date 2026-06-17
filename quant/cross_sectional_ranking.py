#!/usr/bin/env python3
"""
JH 横截面排名因子系统 vXS
完全不同于之前的方案：
- 不计算绝对数值，每天对所有股票横截面排名
- 排名因子天然去量纲、抗噪声、捕捉相对强弱
- 每天只买排名最高的股票
"""
import json, os, sys, time
import numpy as np
from collections import defaultdict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

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

def per_day_factor_values(all_klines, dates):
    """
    构建每日横截面数据矩阵
    返回: {date -> {factor_name -> {code: value}}}
    以及 {date -> {code: t1_return}}
    """
    print("构建每日横截面数据...")
    t0 = time.time()
    
    # 先按代码整理K线
    stock_hist = {}
    for code, info in all_klines.items():
        name = info.get('name', '')
        if 'ST' in name or '*ST' in name or '退' in name:
            continue
        klines = info.get('klines', [])
        hist = []
        for k in klines:
            if isinstance(k, list) and len(k) >= 6:
                hist.append({
                    'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                    'high': float(k[3]), 'low': float(k[4]), 'volume': float(k[5]),
                })
        if hist:
            stock_hist[code] = hist
    
    # 对每个交易日，收集所有股票的因子值
    raw_factors = defaultdict(lambda: defaultdict(dict))  # date -> factor -> {code: val}
    t1_returns = defaultdict(dict)  # date -> {code: t1_return}
    
    for di, date in enumerate(dates):
        if di == 0: continue
        prev_date = dates[di-1]
        
        for code, hist in stock_hist.items():
            # 找当天和昨天的数据
            today_idx = -1
            for i, h in enumerate(hist):
                if h['date'] == date:
                    today_idx = i
                    break
            if today_idx < 1: continue
            
            today = hist[today_idx]
            prev = hist[today_idx - 1]
            
            close = today['close']
            if close <= 0 or close > 500: continue
            
            # T+1收益
            if today_idx + 1 < len(hist):
                t1_close = hist[today_idx + 1]['close']
                t1_returns[date][code] = (t1_close - close) / close
            
            # 基础数据
            open_p = today['open']
            high = today['high']
            low = today['low']
            volume = today['volume']
            prev_close = prev['close']
            
            # ========== 计算因子 ==========
            # 获取历史数据用于窗口计算
            window_start = max(0, today_idx - 25)
            window = hist[window_start:today_idx + 1]
            closes = np.array([h['close'] for h in window])
            volumes = np.array([h['volume'] for h in window])
            highs = np.array([h['high'] for h in window])
            lows = np.array([h['low'] for h in window])
            n = len(closes)
            
            if n < 20: continue
            
            # F1: 价格位置 (横截面)
            f1 = (close - low) / (high - low) if high > low else 0.5
            raw_factors[date]['price_position'][code] = f1
            
            # F2: 今日涨跌幅
            f2 = (close - prev_close) / prev_close * 100 if prev_close > 0 else 0
            raw_factors[date]['daily_change'][code] = f2
            
            # F3: 5日动量
            if n >= 6:
                f3 = (closes[-1] - closes[-6]) / closes[-6] * 100 if closes[-6] > 0 else 0
                raw_factors[date]['mom_5d'][code] = f3
            
            # F4: 20日动量
            if n >= 21:
                f4 = (closes[-1] - closes[-21]) / closes[-21] * 100 if closes[-21] > 0 else 0
                raw_factors[date]['mom_20d'][code] = f4
            
            # F5: 量比 (今日/5日均量)
            if n >= 6:
                vol_5 = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else volumes[-1]
                f5 = volume / vol_5 if vol_5 > 0 else 1
                raw_factors[date]['vol_ratio'][code] = f5
            
            # F6: 相对强弱 (今日涨跌幅 - 市场平均)
            raw_factors[date]['relative_strength'][code] = f2  # 将在后续减去市场均值
            
            # F7: 振幅 (high-low)/close
            f7 = (high - low) / close * 100 if close > 0 else 0
            raw_factors[date]['amplitude'][code] = f7
            
            # F8: RSI
            if n >= 15:
                gains = [closes[i] - closes[i-1] for i in range(-14, 0) if closes[i] > closes[i-1]]
                losses = [closes[i-1] - closes[i] for i in range(-14, 0) if closes[i] < closes[i-1]]
                ag = np.mean(gains) if gains else 0
                al = np.mean(losses) if losses else 0
                f8 = 100 - 100/(1+ag/al) if al > 0 else 100
                raw_factors[date]['rsi'][code] = f8
            
            # F9: MA偏离度 (close/MA5 - 1)
            if n >= 6:
                ma5 = np.mean(closes[-5:])
                if ma5 > 0:
                    f9 = (closes[-1] - ma5) / ma5 * 100
                    raw_factors[date]['ma5_deviation'][code] = f9
            
            # F10: 量价相关性(简化为方向同步性)
            if n >= 6:
                price_up = closes[-1] > closes[-2]
                vol_up = volumes[-1] > np.mean(volumes[-6:-1]) if len(volumes) >= 6 else False
                f10 = 1 if price_up == vol_up else -1
                raw_factors[date]['vp_sync'][code] = f10
            
            # F11: 日内位置 (close在open和prev_close之间的位置)
            daily_range = max(high, max(open_p, prev_close)) - min(low, min(open_p, prev_close))
            f11 = (close - min(low, min(open_p, prev_close))) / daily_range if daily_range > 0 else 0.5
            raw_factors[date]['day_position'][code] = f11
            
            # F12: 缺口 (gap)
            if prev_close > 0:
                f12 = (open_p - prev_close) / prev_close * 100
                raw_factors[date]['gap'][code] = f12
    
    elapsed = time.time() - t0
    print(f"  完成! {len(dates)}天, {len(raw_factors)}天有数据, {elapsed:.0f}s")
    return raw_factors, t1_returns, stock_hist

def cross_sectional_rank(factor_dict):
    """
    横截面排名：对每天的所有股票因子值排名
    返回相同结构，但值是百分位排名 [0, 1]
    """
    ranked = {}
    for date, codes_dict in factor_dict.items():
        codes = list(codes_dict.keys())
        vals = np.array([codes_dict[c] for c in codes])
        # 处理非有限值
        mask = np.isfinite(vals)
        if mask.sum() < 10:
            ranked[date] = {c: 0.5 for c in codes}
            continue
        # 排名（大->小），归一化到[0,1]
        ranks = np.argsort(np.argsort(vals))  # 0=最小, n-1=最大
        rank_pct = ranks / (len(ranks) - 1) if len(ranks) > 1 else np.ones_like(ranks)
        ranked[date] = {c: float(rank_pct[i]) for i, c in enumerate(codes)}
    return ranked

def main():
    print("="*70)
    print("  JH 横截面排名因子系统 vXS")
    print(f"  启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  核心理念: 每天对所有股票横截面排名，只用相对位置")
    print("="*70)
    
    all_klines = load_all()
    dates = get_dates(all_klines)
    print(f"\n  {len(all_klines)}只股票, {len(dates)}个交易日")
    
    # 第1步：构建每日横截面因子数据
    raw_factors, t1_returns, stock_hist = per_day_factor_values(all_klines, dates)
    
    # 第2步：对每个因子横截面排名
    print("\n计算横截面排名...")
    ranked_factors = {}
    factor_names = list(list(raw_factors.values())[0].keys())
    for fn in factor_names:
        date_dict = {d: raw_factors[d][fn] for d in raw_factors if fn in raw_factors[d]}
        ranked_factors[fn] = cross_sectional_rank(date_dict)
        print(f"  {fn:<20s} 已排名")
    
    # 第3步：测试每个排名因子的T+1预测能力
    print(f"\n{'='*70}")
    print(f"  测试单个排名因子的T+1胜率")
    print(f"{'='*70}")
    print(f"  {'因子名':<22s} {'样本':>7} {'高排名胜率':>10} {'低排名胜率':>10} {'胜差':>7} {'IC':>8}")
    print(f"  {'-'*64}")
    
    factor_performance = []
    for fn in factor_names:
        preds = ranked_factors[fn]
        
        vals = []
        wins = []
        for date in preds:
            if date not in t1_returns: continue
            for code, rank_val in preds[date].items():
                if code in t1_returns[date]:
                    vals.append(rank_val)
                    wins.append(1 if t1_returns[date][code] > 0 else 0)
        
        if len(vals) < 500: continue
        
        # 分高分低两组
        vals = np.array(vals)
        wins = np.array(wins)
        high = wins[vals > 0.8]
        low = wins[vals < 0.2]
        
        # Rank IC
        rank_v = np.argsort(np.argsort(vals))
        rank_w = np.argsort(np.argsort(wins))
        ric = np.corrcoef(rank_v, rank_w)[0, 1]
        
        h_win = np.mean(high)*100 if len(high) > 0 else 0
        l_win = np.mean(low)*100 if len(low) > 0 else 0
        spread = h_win - l_win
        
        factor_performance.append({
            'factor': fn, 'samples': len(vals),
            'high_win': round(h_win, 1), 'low_win': round(l_win, 1),
            'spread': round(spread, 1), 'ic': round(ric, 4)
        })
        
        mk = '⭐' if spread > 3 else ' '
        print(f"  {mk}{fn:<22s} {len(vals):>7d} {h_win:>9.1f}% {l_win:>9.1f}% {spread:>6.1f}% {ric:>+8.4f}")
    
    factor_performance.sort(key=lambda x: -x['spread'])
    
    # 第4步：组合最佳排名因子
    print(f"\n{'='*70}")
    print(f"  🏆 排名因子有效性排行（按胜率差）")
    print(f"{'='*70}")
    for i, fp in enumerate(factor_performance[:10]):
        print(f"  {i+1:>2}. {fp['factor']:<22s} 胜差{fp['spread']:>5.1f}% IC{fp['ic']:>+8.4f} 高排名胜率{fp['high_win']:>6.1f}%")
    
    # 第5步：构建复合排名评分 + 回测
    best_factors = [fp['factor'] for fp in factor_performance[:5] if fp['spread'] > 2]
    
    print(f"\n{'='*70}")
    print(f"  复合排名回测（最佳{len(best_factors)}因子等权）")
    print(f"{'='*70}")
    
    for select_pct in [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
        test_dates = sorted(raw_factors.keys())
        all_picks = []
        
        for di, date in enumerate(test_dates):
            if di < 1: continue
            if date not in t1_returns: continue
            if not all(fn in ranked_factors and date in ranked_factors[fn] for fn in best_factors):
                continue
            
            # 找同时有所有因子的股票
            code_list = set(ranked_factors[best_factors[0]][date].keys())
            for fn in best_factors[1:]:
                code_list &= set(ranked_factors[fn][date].keys())
            
            if not code_list: continue
            
            # 计算复合排名得分
            scores = {}
            for code in code_list:
                total = 0
                for fn in best_factors:
                    total += ranked_factors[fn][date].get(code, 0.5)
                scores[code] = total / len(best_factors)
            
            # 选排名前select_pct的股票
            sorted_codes = sorted(scores.keys(), key=lambda c: -scores[c])
            n_select = max(1, int(len(sorted_codes) * select_pct))
            selected = sorted_codes[:min(n_select, 10)]  # 最多10只
            
            for code in selected:
                if code in t1_returns[date]:
                    all_picks.append({
                        'code': code, 't1': t1_returns[date][code],
                        'score': scores[code]
                    })
        
        if not all_picks: continue
        t1s = [p['t1'] for p in all_picks]
        wins = sum(1 for x in t1s if x > 0)
        stops = sum(1 for x in t1s if x <= -0.06)
        n = len(t1s)
        avg = np.mean(t1s)
        mk = '🏆' if wins/n*100 >= 55 else ' '
        
        # 去重：只取第一次出现的股票
        seen = set()
        unique = []
        for p in all_picks:
            if p['code'] not in seen:
                seen.add(p['code'])
                unique.append(p)
        
        print(f"  选前{select_pct*100:>4.0f}%: {n:>4d}只(去重{len(unique)}只) {mk}T+1={wins/n*100:>4.1f}% 均{avg*100:+>+6.2f}% 止损{stops/n*100:>4.1f}%")

if __name__ == '__main__':
    main()
