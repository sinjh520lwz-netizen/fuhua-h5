#!/usr/bin/env python3
"""
5分钟K线条件单回测 — v10.0评分系统最近18天选股
TP+5%/SL-4%/T+3 条件单模拟
"""
import json, os, sys, time, math
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from screener import quick_analyze
from cross_sectional_score import score_early_entry
from backtest_full import load_all_klines, get_trading_dates, collect_market_snapshot, build_hist_klines

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# ──────────────────────────────────────────────
# 1. 获取5分钟K线数据 (mootdx)
# ──────────────────────────────────────────────
def get_5min_bars(code, start_date, count=400):
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std', timeout=10)
        bars = client.bars(symbol=code, frequency=0, start=0, count=count)
        if bars is None or len(bars) == 0:
            return None
        # mootdx返回的DataFrame index是datetime
        bars_filtered = bars[bars.index >= start_date].copy()
        if len(bars_filtered) == 0:
            return None
        return bars_filtered
    except:
        time.sleep(0.5)
        try:
            from mootdx.quotes import Quotes
            client = Quotes.factory(market='std', timeout=15)
            bars = client.bars(symbol=code, frequency=0, start=0, count=count)
            if bars is None or len(bars) == 0: return None
            bars_filtered = bars[bars.index >= start_date].copy()
            return bars_filtered if len(bars_filtered) > 0 else None
        except:
            return None

def get_5min_bars_retry(code, start_date, max_retries=3):
    """带重试的5分钟K线获取"""
    for attempt in range(max_retries):
        bars = get_5min_bars(code, start_date, count=800)
        if bars is not None and len(bars) > 0:
            return bars
        time.sleep(0.5)
    return None

def get_trading_day_5min(bars, date_str):
    """从5分钟数据中提取指定交易日的所有bar"""
    day_bars = bars[bars['datetime'].str.startswith(date_str)]
    return day_bars

# ──────────────────────────────────────────────
# 2. 条件单模拟
# ──────────────────────────────────────────────
def simulate_condition_order(bars_5min, entry_date_str, entry_price, tp_pct=5.0, sl_pct=4.0, max_days=3):
    """
    在5分钟K线上模拟条件单
    
    参数:
        bars_5min: DataFrame with datetime, open, high, low, close
        entry_date_str: 买入日期 '2026-06-09'
        entry_price: 买入价
        tp_pct: 止盈百分比
        sl_pct: 止损百分比
        max_days: 最大持有天数
    
    返回:
        dict: {hit_type, entry_price, exit_price, return_pct, exit_day, exit_time}
    """
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    
    # 找到entry_date之后的所有bar（包括entry_date当天的开盘价可以触发）
    # 条件单通常在次日开盘生效，但也有可能当天盘中触发
    entry_dt = datetime.strptime(entry_date_str, '%Y-%m-%d')
    end_dt = entry_dt + timedelta(days=max_days + 2)  # 放宽到T+5天
    
    # 确保datetime是列不是index
    df = bars_5min.copy()
    # 使用index (DatetimeIndex) 进行过滤，它已经是datetime类型
    # 同时保留datetime列作为字符串备用
    
    bars_filtered = df[
        (df.index >= entry_date_str) & 
        (df.index < end_dt.strftime('%Y-%m-%d'))
    ]
    
    if len(bars_filtered) == 0:
        return None
    
    # 按时间排序（index已经是排序的）
    bars_sorted = bars_filtered.sort_index()
    
    # 记录每个交易日的日期（从index提取）
    trading_days = set()
    for idx in bars_sorted.index:
        day = str(idx)[:10]
        trading_days.add(day)
    trading_days = sorted(trading_days)
    
    # T+3: 最多持有3个完整交易日（不含买入日？通常是买入日+3个交易日）
    # 条件单：一买入就开始监控。但买入可能是在收盘时按收盘价买入
    # 假设买入价 = entry_price，买入动作发生在entry_date的收盘
    # 那么条件单从entry_date的下一个交易日开盘开始生效
    
    # 更准确：买入日是entry_date_str，当天收盘买入
    # 条件单监控从下一根K线开始（如果是集合竞价买入，从当天开盘就开始）
    
    # 标准做法：entry_date收盘买入，T+0不监控，从T+1开盘开始监控到T+3收盘
    entry_day = entry_date_str
    entry_day_index = -1
    for i, day in enumerate(trading_days):
        if day == entry_day:
            entry_day_index = i
            break
    
    if entry_day_index < 0:
        # entry_date可能不在5分钟数据中（比如今天没有数据）
        # 尝试用第一个交易日作为entry
        if len(trading_days) > 0:
            entry_day_index = 0
            entry_day = trading_days[0]
        else:
            return None
    
    # 从entry_date的下一个交易日开始监控
    # 但实际上条件单是盘中触发的，所以从entry_date的下一根5分钟K线就开始监控
    # 简单处理：从第一个大于entry_date的bar开始
    
    # 找entry_bar的位置（基于index）
    entry_bar_idx = -1
    for i, (idx, bar) in enumerate(bars_sorted.iterrows()):
        bar_day = str(idx)[:10]
        if bar_day >= entry_day:
            entry_bar_idx = i
            break
    
    if entry_bar_idx < 0:
        return None
    
    # 从entry_bar开始监控（假设开盘买入，盘中条件单立即生效）
    monitor_start = entry_bar_idx
    
    if monitor_start >= len(bars_sorted):
        return None
    
    # 找到T+3的最后一日
    max_trade_day = min(entry_day_index + max_days, len(trading_days) - 1)
    last_trade_date = trading_days[max_trade_day]
    
    # 开始监控
    for i in range(monitor_start, len(bars_sorted)):
        bar = bars_sorted.iloc[i]
        bar_idx = bars_sorted.index[i]
        bar_day = str(bar_idx)[:10]
        
        # 如果已经超过T+3，退出
        day_idx = trading_days.index(bar_day) if bar_day in trading_days else -1
        if day_idx > max_trade_day or day_idx < 0:
            break
        
        bar_open = bar['open']
        bar_high = bar['high']
        bar_low = bar['low']
        bar_close = bar['close']
        bar_time = str(bar_idx)
        
        # 检查是否触发条件单
        # 按时间顺序：开盘价 → 最低价/最高价 → 收盘价
        # 但更准确的是：开盘价触发 → 盘中最低价触发止损 → 盘中最高价触发止盈
        
        # 盘中最高价是否触发止盈
        if bar_high >= tp_price:
            return {
                'hit_type': 'TP',
                'entry_price': entry_price,
                'exit_price': tp_price,
                'return_pct': tp_pct,
                'exit_day': day_idx - entry_day_index,
                'exit_time': bar_time,
                'bar_index': i,
            }
        
        # 盘中最低价是否触发止损
        if bar_low <= sl_price:
            return {
                'hit_type': 'SL',
                'entry_price': entry_price,
                'exit_price': sl_price,
                'return_pct': -sl_pct,
                'exit_day': day_idx - entry_day_index,
                'exit_time': bar_time,
                'bar_index': i,
            }
    
    # 没触发：T+3收盘平仓
    last_date_str = str(last_trade_date)
    last_bars = bars_sorted[bars_sorted.index >= last_date_str]
    last_bars = last_bars[last_bars.index < (pd.Timestamp(last_date_str) + pd.Timedelta(days=1))]
    if len(last_bars) > 0:
        last_bar = last_bars.iloc[-1]
        exit_price = last_bar['close']
        ret = (exit_price / entry_price - 1) * 100
        return {
            'hit_type': 'T+3平仓',
            'entry_price': entry_price,
            'exit_price': exit_price,
            'return_pct': round(ret, 2),
            'exit_day': max_days,
            'exit_time': str(last_bars.index[-1]),
            'bar_index': len(bars_sorted) - 1,
        }
    
    # 数据不足，无法平仓
    return None

# ──────────────────────────────────────────────
# 3. 主回测流程
# ──────────────────────────────────────────────
def run_backtest():
    print("=" * 65)
    print("  v10.0 评分系统 | 真实5分钟K线条件单回测")
    print("  TP+5% / SL-4% / T+3")
    print("=" * 65)
    
    print("\n加载全A股K线数据...")
    all_klines = load_all_klines()
    print(f"  已加载 {len(all_klines)}只股票")
    
    trading_dates = get_trading_dates(all_klines, 200)
    # 最近18天
    backtest_dates = trading_dates[-18:]
    print(f"  交易日: {backtest_dates[0]} ~ {backtest_dates[-1]} ({len(backtest_dates)}天)")
    
    all_trades = []
    recent_codes = set()
    
    for di, date in enumerate(backtest_dates):
        print(f"\n📅 [{di+1}/{len(backtest_dates)}] {date} ...")
        
        # 第1步：获取当日行情快照
        snapshot = collect_market_snapshot(all_klines, date)
        if not snapshot:
            print(f"  无行情数据，跳过")
            continue
        
        # 第2步：取成交额TOP200
        top_stocks = snapshot[:200]
        
        # 第3步：评分筛选
        day_picks = []
        for s in top_stocks:
            code = s['code']
            if code in recent_codes:
                continue
            
            hist = build_hist_klines(all_klines, code, date)
            if len(hist) < 30:
                continue
            
            ind = quick_analyze(hist)
            if not ind:
                continue
            
            score, factors = score_early_entry(ind, s['change'], 0)
            
            if score < 6:
                continue
            
            day_picks.append({
                'date': date,
                'code': code,
                'name': s['name'],
                'score': round(score, 1),
                'entry_price': s['price'],
                'change': round(s['change'], 2),
                'amount': round(s['amount'], 0),
            })
        
        if not day_picks:
            print(f"  无符合评分股票")
            continue
        
        # 第4步：排名模式，取前8
        day_picks.sort(key=lambda x: x['score'], reverse=True)
        top_n = min(len(day_picks), max(3, int(len(day_picks) * 0.1)))
        top_n = max(top_n, min(5, len(day_picks)))
        day_picks = day_picks[:top_n]
        recent_codes.update(p['code'] for p in day_picks[:3])
        
        # 第5步：对每只股票获取5分钟数据做条件单模拟
        trade_results = []
        for pick in day_picks:
            code = pick['code']
            name = pick['name']
            entry_price = pick['entry_price']
            score = pick['score']
            
            # 获取5分钟数据
            bars = get_5min_bars_retry(code, date, max_retries=2)
            if bars is None or len(bars) < 20:
                print(f"  ⏭ {name}({code}) 5分钟数据不足，跳过")
                continue
            
            # 模拟条件单
            result = simulate_condition_order(
                bars, date, entry_price,
                tp_pct=5.0, sl_pct=4.0, max_days=3
            )
            
            if result is None:
                print(f"  ⏭ {name}({code}) 条件单模拟失败，跳过")
                continue
            
            result.update({
                'code': code,
                'name': name,
                'date': date,
                'score': score,
                'entry_price': entry_price,
            })
            trade_results.append(result)
            
        # 修正打印格式
        for r in trade_results:
            emoji = '🟢' if r['hit_type'] == 'TP' else ('🔴' if r['hit_type'] == 'SL' else '⚪')
            print(f"  {emoji} {r['name']:6s}({r['code']}) 分:{r['score']:.0f} 买:{r['entry_price']:.2f} → {r['hit_type']} {r['return_pct']:+.2f}%")
        
        all_trades.extend(trade_results)
    
    # ========== 汇总 ==========
    print(f"\n{'=' * 65}")
    print(f"  📊 真实5分钟线条件单回测 (TP+5%/SL-4%)")
    print(f"{'=' * 65}")
    
    if not all_trades:
        print("  无交易记录")
        return
    
    total = len(all_trades)
    tp_hits = sum(1 for t in all_trades if t['hit_type'] == 'TP')
    sl_hits = sum(1 for t in all_trades if t['hit_type'] == 'SL')
    t3_flat = sum(1 for t in all_trades if t['hit_type'] == 'T+3平仓')
    
    total_return = sum(t['return_pct'] for t in all_trades)
    avg_return = total_return / total if total > 0 else 0
    
    print(f"\n  总交易: {total}笔")
    print(f"  🟢 止盈触发(TP): {tp_hits}笔 = {tp_hits/total*100:.1f}%")
    print(f"  🔴 止损触发(SL): {sl_hits}笔 = {sl_hits/total*100:.1f}%")
    print(f"  ⚪ T+3平: {t3_flat}笔 = {t3_flat/total*100:.1f}%")
    print(f"")
    print(f"  💰 总收益: {total_return:+.1f}%")
    print(f"     均每笔: {avg_return:+.2f}%")
    
    # 胜率（TP+盈利的T+3平仓算赢）
    wins = sum(1 for t in all_trades if t['return_pct'] > 0)
    print(f"  🏆 胜率: {wins}/{total} = {wins/total*100:.1f}%")
    
    if total_return > 0:
        print(f"\n  ✅ 策略判定: 盈利")
    else:
        print(f"\n  ❌ 策略判定: 亏损")
    
    # 保存详细结果
    result_file = os.path.join(DATA_DIR, 'backtest_5min_condition.json')
    with open(result_file, 'w') as f:
        json.dump({
            'total_trades': total,
            'tp_hits': tp_hits,
            'sl_hits': sl_hits,
            't3_flat': t3_flat,
            'total_return_pct': round(total_return, 2),
            'avg_return_pct': round(avg_return, 2),
            'win_rate': round(wins/total*100, 1),
            'trades': all_trades,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  详细结果已保存至: {result_file}")
    
    return all_trades

if __name__ == '__main__':
    run_backtest()
