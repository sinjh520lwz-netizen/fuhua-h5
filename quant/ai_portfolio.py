#!/usr/bin/env python3
"""AI持仓分析 - 接收JSON输入，调用DeepSeek分析，输出JSON结果"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_analyzer import DEEPSEEK_KEY, DEEPSEEK_BASE, MODEL
from openai import OpenAI

client = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE)

SYSTEM_PROMPT = """你是一位严谨的A股投资顾问。分析用户持仓并提供建议。

输出JSON格式（必须严格，不要加markdown标记）：
{
  "verdict": "建议持仓/可做T/建议止损/可补仓",
  "confidence": 1-5,
  "reason": "核心判断理由（1-2句话）",
  "market_view": "大盘环境判断（一句话）",
  "t_suggestion": "做T策略建议（一句话）",
  "risk_warning": "风险提示（一句话，无风险则空字符串）"
}

规则：
- 浮亏<3%且趋势向上 → "可做T"
- 浮亏3-8%且大盘弱势 → "建议持仓观望"
- 浮亏>8%且继续下跌 → "建议止损"
- 浮亏>8%但已企稳 → "可做T降本"
- 盈利且趋势好 → "继续持有"
- 仓位>70%且个股集中 → 提示风险
"""

def analyze(data):
    stock = data['stock']
    quote = data['quote']
    kline = data['kline']
    market = data.get('market', {})
    
    closes = [k['close'] for k in kline[-30:]] if kline else []
    ma5 = sum(closes[-5:])/5 if len(closes)>=5 else stock['cost_price']
    ma20 = sum(closes)/len(closes) if closes else stock['cost_price']
    trend = '向上' if ma5 > ma20 else '向下'
    
    user_prompt = f"""持仓分析：
股票：{stock['name']}({stock['code']})
成本价：{stock['cost_price']}
现价：{quote['price']}
浮亏：{quote['pl_pct']:.1f}%
持仓：{stock['shares']}股
市值：{(quote['price']*stock['shares']):.0f}
5日均线：{ma5:.2f}
20日均线：{ma20:.2f}
趋势：{trend}
大盘：{'跌'+str(abs(market.get('market_change',0)))+'%' if market.get('market_change',0)<0 else '涨'+str(market.get('market_change',0))+'%'}
大盘情绪：{market.get('emotion_phase','未知')}
仓位：{data.get('portfolio',{}).get('position_ratio',0)}%
总资产：{data.get('portfolio',{}).get('total_assets',0)}
可用资金：{data.get('portfolio',{}).get('available',0)}

请给出持仓建议。"""
    
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        max_tokens=300,
        temperature=0.3,
        timeout=30
    )
    
    raw = resp.choices[0].message.content.strip()
    
    # 暴力JSON提取：找第一个{和最后一个}
    import re
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        json_str = raw[start:end+1]
        try:
            result = json.loads(json_str)
            if isinstance(result, dict) and 'verdict' in result:
                return result
        except:
            pass
    
    # 如果上面失败，可能是深层嵌套，移除所有转义
    cleaned = raw.replace('\\"', '"').replace("\\'", "'").replace('\\n', '\n').replace('\\t', '\t')
    start2 = cleaned.find('{')
    end2 = cleaned.rfind('}')
    if start2 >= 0 and end2 > start2:
        try:
            result = json.loads(cleaned[start2:end2+1])
            if isinstance(result, dict) and 'verdict' in result:
                return result
        except:
            pass
    
    return {"verdict": "AI分析暂不可用", "raw_snippet": raw[:300]}

if __name__ == '__main__':
    try:
        data = json.loads(sys.stdin.read())
        result = analyze(data)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
