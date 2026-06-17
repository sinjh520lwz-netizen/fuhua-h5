#!/usr/bin/env python3
"""
反T信号监控 v2.0 — 检测到买卖时机输出告警，无信号则静默

v2.0 优化：
- 只有high urgency才推送给用户
- 接回信号基于多条件评分（RSI+支撑位+放量企稳）
- 大盘情绪过滤：大盘跌>1%时接回信号更保守
- 去重逻辑优化
"""
import json, os, sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'data', 'signal_state.json')
sys.path.insert(0, SCRIPT_DIR)

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {'last_sell_alert': '', 'last_buy_alert': '', 'last_info': '', 'last_buyback_alert': ''}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False)

def run():
    from t_engine import run_t_analysis
    from buyback_engine import analyze_buyback
    import io, contextlib
    
    # 抑制t_engine的全部print输出，只在有强信号时才输出
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_t_analysis()
    
    if result.get('error'):
        return  # 静默
    
    signals = result.get('signals', [])
    state = load_state()
    now = datetime.now().strftime('%H:%M')
    stock = result.get('stock', '601138')
    code = result.get('code', '601138')
    current = result.get('current', 0)
    change = result.get('change', 0)
    deviation = result.get('deviation', 0)
    rsi = result.get('rsi')
    market = result.get('market_sentiment', {})
    
    # ---- 1. 反T信号：只推送high urgency ----
    actionable = [s for s in signals if s['type'] in ('sell', 'buy_back') and s.get('urgency') == 'high']
    
    if actionable:
        top = actionable[0]
        signal_key = f"{top['type']}_{top.get('reason','')}_{datetime.now().strftime('%Y%m%d_%H')}"
        
        if top['type'] == 'sell':
            if state.get('last_sell_alert') == signal_key:
                return  # 已推送过
            state['last_sell_alert'] = signal_key
        elif top['type'] == 'buy_back':
            if state.get('last_buy_alert') == signal_key:
                return
            state['last_buy_alert'] = signal_key
        
        save_state(state)
        
        emoji = '🔴' if top['type'] == 'sell' else '🟢'
        direction = '卖出信号' if top['type'] == 'sell' else '买回信号'
        
        tp = f"目标价: {top['target_price']}" if top.get('target_price') else ''
        ep = f"预期收益: {top['expected_profit']}" if top.get('expected_profit') else ''
        rsi_str = f"RSI: {rsi}" if rsi else ''
        mkt_str = f"大盘: {market.get('change', 0)}%" if market.get('change') is not None else ''
        
        msg = (
            f"{emoji} ⚡紧急 {stock}({code}) {direction}\n"
            f"━━━━━━━━━━━━━━\n"
            f"当前价: {current} ({'+' if change >= 0 else ''}{change}%)\n"
            f"操作: {top['action']}\n"
            f"理由: {top['desc']}\n"
            f"{tp}  {ep}\n"
            f"均线偏离: {deviation}%  {rsi_str}  {mkt_str}\n"
            f"时间: {now}\n"
            f"━━━━━━━━━━━━━━\n"
            f"⚠️ 反T提醒，注意仓位控制"
        )
        print(msg)
        return
    
    # ---- 2. 接回信号：基于评分系统 ----
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            bb = analyze_buyback()
        
        if bb and not bb.get('error'):
            bb_signal = bb.get('signal', 'wait')
            bb_urgency = bb.get('urgency', 'low')
            bb_score = bb.get('total_score', 0)
            
            # 只有strong_buy(score>=8)或buy(score>=5)才推送
            should_push = False
            if bb_signal == 'strong_buy':
                should_push = True
            elif bb_signal == 'buy' and bb_urgency == 'medium':
                should_push = True
            
            if should_push:
                bb_key = f"buyback_{bb_signal}_{bb_score}_{datetime.now().strftime('%Y%m%d_%H')}"
                if state.get('last_buyback_alert') == bb_key:
                    return  # 已推送过
                state['last_buyback_alert'] = bb_key
                save_state(state)
                
                sup = bb.get('support', [{}])[0] if bb.get('support') else {}
                triggers_met = [t for t in bb.get('triggers', []) if t['met']]
                trigger_str = '、'.join(t['name'] for t in triggers_met)
                rec = bb.get('recommendations', [{}])[0] if bb.get('recommendations') else {}
                
                urgency_tag = '⚡紧急' if bb_signal == 'strong_buy' else '📍提示'
                emoji = '🟢🟢' if bb_signal == 'strong_buy' else '🟢'
                
                msg = (
                    f"{emoji} {urgency_tag} 接回提醒 {bb['stock']}({bb['code']})\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"当前价: {bb['current']} (低于卖出价{abs(bb['diff_pct'])}%)\n"
                    f"卖出价: {bb['sell_price']}\n"
                    f"综合评分: {bb_score}分 ({bb['signal_text']})\n"
                    f"触发条件: {trigger_str}\n"
                )
                if sup:
                    msg += f"支撑位: {sup.get('price', '--')} ({sup.get('type', '')})\n"
                if rec:
                    msg += f"建议: {rec.get('action', '')} @ {rec.get('price', '--')}\n"
                
                market = bb.get('market', {})
                if market.get('change') is not None:
                    msg += f"大盘: {market['change']}% ({market.get('sentiment', '')})\n"
                
                msg += (
                    f"━━━━━━━━━━━━━━\n"
                    f"📊 {bb['advice']}"
                )
                print(msg)
                return
    
    except Exception as e:
        pass
    
    # 无信号，静默
    return

if __name__ == '__main__':
    run()
