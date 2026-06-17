#!/usr/bin/env python3
"""
JH MiMo AI 分析模块 v1.0 — 对标 DeepSeek V4 Pro 的 MiMo 版本
用于 A/B 对比测试
"""
import json, os, sys, time, re
from openai import OpenAI

MIMO_KEY = "tp-ctagobt4qm6vp64jz4zskiuhqv83t4qbn4on2rxy2n9k8pij"
MIMO_BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MODEL = "mimo-v2.5-pro"

client = OpenAI(api_key=MIMO_KEY, base_url=MIMO_BASE)

SYSTEM_PROMPT = """你是一位严谨的A股量化分析师，擅长从技术面和概念题材角度评估短线机会。
你的分析必须简洁、有观点、有风险意识。

输出格式（严格遵守JSON）：
{
  "ai_view": "一句话核心观点，15字以内",
  "logic": "上涨逻辑判断：基本面驱动/题材炒作/技术反弹/资金推动，选1-2个",
  "risk_tip": "最主要的一个风险提示，20字以内",
  "confidence": 1-5的整数，5最看好，1最不看好
}

注意：
- 不要吹捧，宁可保守
- 涨幅过大的票必须降confidence
- RSI>70的票confidence不超过3
- 纯概念炒作无业绩支撑的confidence不超过2
- 只输出JSON，不要任何其他文字"""


def _extract_json(text):
    """从文本中提取JSON对象，处理markdown代码块等"""
    if not text:
        return None
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def analyze_stock(stock_data, max_retries=3):
    """用 MiMo 分析单只股票（带重试）"""
    prompt = f"""分析以下A股：
名称: {stock_data.get('name', '')}
代码: {stock_data.get('code', '')}
当前价: {stock_data.get('price', 0)}
今日涨幅: {stock_data.get('change', 0):.2f}%
量化评分: {stock_data.get('score', 0)}/100
技术信号: {', '.join(stock_data.get('signals', []))}
风险标签: {', '.join(stock_data.get('risk_tags', [])) or '无'}

请给出你的分析。"""

    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,  # MiMo推理模型需大量token
                temperature=0.3,
            )
            msg = resp.choices[0].message
            text = (msg.content or '').strip()
            # MiMo推理模型内容在reasoning_content字段
            if not text:
                reasoning = getattr(msg, 'reasoning_content', None)
                if reasoning:
                    text = reasoning.strip()
            
            if not text:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                return {"ai_view": "MiMo空响应", "logic": "未知", "risk_tip": "", "confidence": 3}
            
            result = _extract_json(text)
            if result:
                return {
                    "ai_view": result.get("ai_view", "分析异常")[:40],
                    "logic": result.get("logic", "未知")[:20],
                    "risk_tip": result.get("risk_tip", "")[:30],
                    "confidence": max(1, min(5, int(result.get("confidence", 3)))),
                }
            
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            
            return {"ai_view": "MiMo解析失败", "logic": "未知", "risk_tip": "", "confidence": 3}
            
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.5)
                continue
            return {"ai_view": "MiMo调用失败", "logic": "未知", "risk_tip": str(e)[:20], "confidence": 3}
    
    return {"ai_view": "MiMo重试耗尽", "logic": "未知", "risk_tip": "", "confidence": 3}


def batch_analyze(stocks, max_count=25, delay=0.8):
    """批量分析，控制速率"""
    results = {}
    ok_count = 0
    for i, s in enumerate(stocks[:max_count]):
        code = s.get('code', '')
        name = s.get('name', '')
        ai_result = analyze_stock(s)
        results[code] = ai_result
        
        status = "✅" if "异常" not in ai_result['ai_view'] and "失败" not in ai_result['ai_view'] and "空响应" not in ai_result['ai_view'] and "重试" not in ai_result['ai_view'] and "解析失败" not in ai_result['ai_view'] else "❌"
        if status == "✅":
            ok_count += 1
        
        print(f"  MiMo {status} {i+1}/{min(len(stocks), max_count)}: {name}({code}) → {ai_result['ai_view'][:20]} | 信心:{ai_result['confidence']}/5", file=sys.stderr)
        
        if i < len(stocks) - 1:
            time.sleep(delay)
    
    print(f"  MiMo成功率: {ok_count}/{min(len(stocks), max_count)}", file=sys.stderr)
    return results


def adjust_score_with_ai(original_score, ai_result):
    """根据AI分析微调评分"""
    conf = ai_result.get('confidence', 3)
    if conf >= 5: bonus = 5
    elif conf == 4: bonus = 3
    elif conf == 3: bonus = 0
    elif conf == 2: bonus = -5
    else: bonus = -8
    
    if '炒作' in ai_result.get('logic', '') and conf <= 2:
        bonus -= 3
    return round(min(max(original_score + bonus, 0), 100), 1)


if __name__ == '__main__':
    test = {
        'name': '中国稀土', 'code': '000831', 'price': 10.50, 'change': 1.3, 'score': 82.5,
        'signals': ['MACD低位多头', 'RSI回升', '均线粘合'],
        'risk_tags': [], 'concepts': [],
        'indicators': {'rsi14': 55, 'dif': 0.01, 'dea': -0.02, 'vol_ratio': 1.1, 'ma_convergence': 0.5},
        'alpha': {'mom_5d': 1.2, 'mom_20d': -3.5, 'boll_pos': 45, 'breakout': 70},
    }
    result = analyze_stock(test)
    print(json.dumps(result, ensure_ascii=False, indent=2))
