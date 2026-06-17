#!/usr/bin/env python3
"""
JH量化选股 v4.0 - 多分析师决策系统
9个分析师(并行) → 加权辩论 → 增强风险评估 → 个性化建议
FastAPI + SSE 实时流式输出
"""
import json
import asyncio
import os
import glob
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = "sk-bde0362f3a534d7cb60b922563e7adfe"
client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")
MODEL = "deepseek-v4-pro"

# Thread pool for sync OpenAI calls
executor = ThreadPoolExecutor(max_workers=9)

ANALYSTS = [
    {
        "id": "market",
        "name": "市场分析师",
        "icon": "📊",
        "color": "#4FC3F7",
        "desc": "K线、量价、技术指标",
        "system_prompt": "你是资深A股市场分析师，专注技术分析领域20年。你擅长K线形态识别、量价关系分析、技术指标应用。你的分析风格严谨，从不模棱两可。请用中文回答。",
        "analysis_prompt": """请从技术分析角度分析股票【{stock_code} {stock_name}】：

1. **趋势判断**：当前处于什么趋势？5日/10日/20日/60日均线排列如何？
2. **K线形态**：近5个交易日的K线组合是什么形态？有何含义？
3. **量价配合**：近期成交量变化如何？量价是否背离？
4. **技术指标**：RSI位置及信号、MACD金叉/死叉、布林带位置
5. **支撑阻力**：近期关键支撑位和阻力位
6. **短期预判**：未来3-5日最可能的走势

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"上涨/下跌/震荡","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","support_price":"支撑位","resist_price":"阻力位","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "sentiment",
        "name": "舆情分析师",
        "icon": "🗣️",
        "color": "#FF7043",
        "desc": "讨论热度、情绪变化",
        "system_prompt": "你是资深A股舆情分析师，专注市场情绪和舆论分析10年。你擅长从社交媒体、股吧讨论、搜索趋势中捕捉市场情绪变化。你深知'市场短期是投票机'。请用中文回答。",
        "analysis_prompt": """请从舆情角度分析股票【{stock_code} {stock_name}】：

1. **讨论热度**：该股近期在股吧/雪球/东方财富的讨论热度如何？
2. **情绪方向**：散户情绪偏多还是偏空？有无情绪极化？
3. **情绪变化**：近期情绪是否有明显转向？
4. **关键词**：讨论中最频繁出现的关键词
5. **异常信号**：有无异常舆论信号
6. **反身性风险**：情绪是否已充分反映在价格中？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"升温/降温/平稳","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","hot_keywords":["关键词1","关键词2","关键词3"],"confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "news",
        "name": "新闻分析师",
        "icon": "📰",
        "color": "#66BB6A",
        "desc": "公告、行业、宏观事件",
        "system_prompt": "你是资深A股新闻分析师，专注公司公告、行业动态和宏观经济分析15年。你擅长从新闻事件中提取对股价的影响因子。请用中文回答。",
        "analysis_prompt": """请从新闻事件角度分析股票【{stock_code} {stock_name}】：

1. **公司公告**：近期有无重大公告（业绩预告/重大合同/股权变动/定增/回购等）？
2. **行业动态**：所在行业近期有无重大事件？
3. **宏观事件**：近期有无影响该股的宏观经济事件？
4. **事件评级**：上述事件对股价的影响程度
5. **时间窗口**：哪些事件即将发生？
6. **信息差**：市场是否已充分消化这些信息？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"利好/利空/中性","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","key_events":["事件1","事件2"],"confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "fundamental",
        "name": "基本面分析师",
        "icon": "💰",
        "color": "#AB47BC",
        "desc": "财报、盈利、估值",
        "system_prompt": "你是资深A股基本面分析师，CFA持证人，专注财务分析和估值建模20年。你深信'价格终将回归价值'。请用中文回答。",
        "analysis_prompt": """请从基本面角度分析股票【{stock_code} {stock_name}】：

1. **盈利能力**：近3年营收和净利润增长趋势，ROE水平
2. **估值水平**：当前PE/PB在历史分位和行业分位中的位置
3. **财务健康**：资产负债率、经营现金流、速动比率
4. **成长性**：未来1-2年业绩增长预期
5. **分红情况**：股息率及分红稳定性
6. **盈利质量**：经营现金流与净利润匹配度

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"改善/恶化/平稳","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","pe_estimate":"估值判断","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "policy",
        "name": "政策分析师",
        "icon": "🏛️",
        "color": "#FFA726",
        "desc": "监管政策、产业方向",
        "system_prompt": "你是资深A股政策分析师，深谙A股政策周期和监管逻辑。你擅长预判政策走向，识别政策红利和监管风险。A股是政策市，你深知这一点。请用中文回答。",
        "analysis_prompt": """请从政策角度分析股票【{stock_code} {stock_name}】：

1. **行业政策**：该股所在行业当前受政策鼓励还是限制？
2. **监管环境**：近期有无针对该行业或公司的监管动作？
3. **产业方向**：是否符合国家产业政策方向（双碳/数字/自主可控/新质生产力）？
4. **政策红利**：有无即将到手的政策利好？
5. **政策风险**：有无潜在的政策利空？
6. **中央方向**：最新中央政策对该公司业务有何指引？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"政策利好/政策利空/中性","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","policy_direction":"支持/限制/中性","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "hotmoney",
        "name": "游资追踪师",
        "icon": "🐉",
        "color": "#EF5350",
        "desc": "龙虎榜、主力资金",
        "system_prompt": "你是资深A股游资追踪师，深谙A股游资运作手法。你能从龙虎榜数据中读出主力意图，从资金流向中捕捉庄家动向。A股是资金推动的市场。请用中文回答。",
        "analysis_prompt": """请从游资和主力资金角度分析股票【{stock_code} {stock_name}】：

1. **龙虎榜**：近期是否上榜？买方/卖方席位实力对比？
2. **主力资金**：近期主力资金净流入还是净流出？
3. **游资风格**：该股是游资主导还是机构主导？
4. **资金趋势**：主力资金流向是加速还是减速？
5. **筹码集中度**：筹码集中还是分散？有无主力吸筹/派发迹象？
6. **异动信号**：有无盘中异常大单、尾盘拉升/砸盘？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"主力流入/主力流出/资金平衡","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","fund_direction":"流入/流出/平衡","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "unlock",
        "name": "解禁监控师",
        "icon": "🔓",
        "color": "#26C6DA",
        "desc": "解禁、减持、股权风险",
        "system_prompt": "你是资深A股解禁与股权风险分析师，专注限售股解禁、股东增减持、股权质押等领域的风险预警。你深知A股最大的风险往往来自内部人。风格审慎，宁可错杀不可放过。请用中文回答。",
        "analysis_prompt": """请从解禁和股权风险角度分析股票【{stock_code} {stock_name}】：

1. **限售解禁**：近期（30日内）有无限售股解禁？规模占比？
2. **股东减持**：近期有无大股东或董监高减持公告？
3. **股权质押**：大股东股权质押比例如何？有无平仓风险？
4. **增发/配股**：近期有无增发或配股计划？
5. **股权变动**：有无控制权变更、要约收购等风险？
6. **内部人信号**：管理层近期增减持行为传递什么信号？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"风险上升/风险下降/平稳","signal":"看空/看多/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","unlock_risk":"高/中/低/无","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "quant",
        "name": "量化分析师",
        "icon": "🔢",
        "color": "#7C4DFF",
        "desc": "因子得分、技术形态、量化信号",
        "system_prompt": "你是资深A股量化分析师，精通多因子模型、统计套利和机器学习量化策略。你用数据和模型说话，不带主观情绪。你擅长从数百个因子中筛选出最有效的alpha信号。请用中文回答。",
        "analysis_prompt": """请从量化分析角度分析股票【{stock_code} {stock_name}】：

1. **动量因子**：近5/10/20日动量得分如何？动量是否持续？
2. **价值因子**：EP/BP/CP等价值因子在全市场分位？
3. **质量因子**：ROE稳定性、盈利持续性、财务杠杆评分
4. **技术形态量化**：布林带位置、RSI分位、ATR波动率
5. **多因子综合得分**：综合各因子的加权得分（0-100）
6. **量化信号**：有无金叉/死叉、突破/回踩等量化信号

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"上涨/下跌/震荡","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","factor_score":65,"factor_details":"因子分析详情","quant_signals":["信号1","信号2"],"confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    },
    {
        "id": "chip",
        "name": "筹码分析师",
        "icon": "🎰",
        "color": "#E040FB",
        "desc": "主力持仓、筹码分布、资金流向",
        "system_prompt": "你是资深A股筹码分析师，专注筹码分布和资金流向分析15年。你深谙'筹码决定价格'的道理，能从持仓变动中读出主力意图。你擅长分析筹码峰、套牢盘和获利盘的博弈。请用中文回答。",
        "analysis_prompt": """请从筹码分布和资金流向角度分析股票【{stock_code} {stock_name}】：

1. **筹码分布**：当前筹码是单峰还是多峰？筹码密集区在哪？
2. **套牢盘分析**：上方套牢盘集中区间？套牢盘压力大不大？
3. **获利盘分析**：当前获利盘比例？获利盘抛压如何？
4. **主力持仓**：北向资金、公募基金、社保持仓变化趋势
5. **资金流向**：近5日主力资金净流入/流出？大单占比？
6. **筹码博弈**：多空双方筹码博弈态势如何？

严格按以下JSON格式输出，不要加任何其他文字：
{{"trend":"吸筹/派发/换手","signal":"看多/看空/中性","strength":"强/中/弱","summary":"50字内核心观点","detail":"200字内详细分析","chip_peak":"筹码密集价位","profit_ratio":"获利盘比例","fund_flow":"净流入/净流出金额","confidence":3,"bull_points":["看多理由1","看多理由2"],"bear_points":["看空理由1","看空理由2"]}}"""
    }
]

# Total analyst count
TOTAL_ANALYSTS = len(ANALYSTS)


def extract_json(text: str) -> dict:
    """从AI回复中提取JSON，处理各种格式"""
    import re
    text = text.strip()
    
    # 1. 去掉markdown代码块
    if "```" in text:
        # 匹配 ```json ... ``` 或 ``` ... ```
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    
    # 2. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # 3. 找到第一个{和最后一个}之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        json_str = text[start:end+1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试修复常见问题
            # 移除注释
            json_str = re.sub(r'//.*?\n', '\n', json_str)
            # 移除尾部逗号
            json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
            # 修复单引号为双引号
            json_str = json_str.replace("'", '"')
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
    
    # 4. 尝试用正则提取关键字段
    try:
        result = {}
        # 提取常见的字段
        patterns = {
            'trend': r'"trend"\s*:\s*"([^"]*)"',
            'signal': r'"signal"\s*:\s*"([^"]*)"',
            'strength': r'"strength"\s*:\s*"([^"]*)"',
            'summary': r'"summary"\s*:\s*"([^"]*)"',
            'detail': r'"detail"\s*:\s*"([^"]*)"',
            'confidence': r'"confidence"\s*:\s*(\d+)',
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                result[key] = match.group(1)
        
        if result.get('trend') or result.get('signal'):
            result.setdefault('trend', '未知')
            result.setdefault('signal', '中性')
            result.setdefault('strength', '弱')
            result.setdefault('summary', '解析部分成功')
            result.setdefault('detail', text[:200])
            result.setdefault('confidence', 2)
            result.setdefault('bull_points', [])
            result.setdefault('bear_points', [])
            return result
    except:
        pass
    
    # 5. 完全失败，返回错误对象
    return {
        "trend": "未知", "signal": "中性", "strength": "弱",
        "summary": "分析数据解析异常", "detail": text[:300],
        "confidence": 1, "bull_points": [], "bear_points": []
    }


def call_deepseek_sync(system_prompt: str, user_prompt: str) -> str:
    """同步调用DeepSeek API（在线程池中运行）"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=1000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def call_deepseek(system_prompt: str, user_prompt: str) -> str:
    """异步调用DeepSeek API"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, call_deepseek_sync, system_prompt, user_prompt)


async def run_analyst(analyst: dict, stock_code: str, stock_name: str) -> dict:
    """运行单个分析师"""
    prompt = analyst["analysis_prompt"].format(stock_code=stock_code, stock_name=stock_name)
    raw = await call_deepseek(analyst["system_prompt"], prompt)
    result = extract_json(raw)
    result["id"] = analyst["id"]
    result["name"] = analyst["name"]
    result["icon"] = analyst["icon"]
    result["color"] = analyst["color"]
    result["desc"] = analyst["desc"]
    for key in ["trend", "signal", "strength", "summary", "detail", "confidence", "bull_points", "bear_points"]:
        if key not in result:
            result[key] = "未知" if key not in ["confidence"] else 1
            if key in ["bull_points", "bear_points"]:
                result[key] = []
    result["confidence"] = min(5, max(1, int(result.get("confidence", 1))))
    return result


async def run_analysts_parallel(analysts: list, stock_code: str, stock_name: str, progress_callback=None):
    """并行运行所有分析师，按完成顺序回调"""
    results = {}
    completed = []

    async def run_one(idx, analyst):
        result = await run_analyst(analyst, stock_code, stock_name)
        results[analyst["id"]] = (idx, result)
        completed.append(analyst["id"])
        if progress_callback:
            await progress_callback(idx, analyst, result, len(completed))
        return result

    tasks = [run_one(i, a) for i, a in enumerate(analysts)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Return in original order
    ordered = []
    for a in analysts:
        if a["id"] in results:
            ordered.append(results[a["id"]][1])
        else:
            ordered.append({
                "id": a["id"], "name": a["name"], "icon": a["icon"],
                "color": a["color"], "desc": a["desc"],
                "trend": "未知", "signal": "中性", "strength": "弱",
                "summary": "分析失败", "detail": "该分析师未能完成分析",
                "confidence": 1, "bull_points": [], "bear_points": []
            })
    return ordered


def compute_analyst_weight(report: dict) -> float:
    """根据分析师权重计算加权得分（信心度越高权重越大）"""
    confidence = report.get("confidence", 3)
    strength_map = {"强": 1.5, "中": 1.0, "弱": 0.6}
    strength_weight = strength_map.get(report.get("strength", "中"), 1.0)
    return confidence * strength_weight


def compute_weighted_signals(reports: list) -> dict:
    """加权信号计算"""
    bull_weight = 0.0
    bear_weight = 0.0
    neutral_weight = 0.0
    total_weight = 0.0

    for r in reports:
        w = compute_analyst_weight(r)
        total_weight += w
        signal = r.get("signal", "中性")
        if "多" in signal or "利好" in signal or "流入" in signal or "改善" in signal or "升温" in signal:
            bull_weight += w
        elif "空" in signal or "利空" in signal or "流出" in signal or "恶化" in signal or "降温" in signal or "风险上升" in signal:
            bear_weight += w
        else:
            neutral_weight += w

    if total_weight == 0:
        total_weight = 1

    return {
        "bull_weight": round(bull_weight, 2),
        "bear_weight": round(bear_weight, 2),
        "neutral_weight": round(neutral_weight, 2),
        "total_weight": round(total_weight, 2),
        "bull_ratio": round(bull_weight / total_weight * 100, 1),
        "bear_ratio": round(bear_weight / total_weight * 100, 1),
        "consensus_score": round(abs(bull_weight - bear_weight) / total_weight * 100, 1),
        "divergence_points": []
    }


async def run_debate(reports: list) -> dict:
    """加权多空辩论"""
    weighted_signals = compute_weighted_signals(reports)

    bull_args = []
    bear_args = []
    for r in reports:
        name = r.get("name", "")
        conf = r.get("confidence", 3)
        weight_tag = f"[权重{conf}/5]" if conf >= 4 else ""
        for bp in r.get("bull_points", []):
            bull_args.append(f"[{name}]{weight_tag} {bp}")
        for bp in r.get("bear_points", []):
            bear_args.append(f"[{name}]{weight_tag} {bp}")

    # Identify divergence points
    bull_reports = [r["name"] for r in reports if "多" in r.get("signal", "")]
    bear_reports = [r["name"] for r in reports if "空" in r.get("signal", "")]

    debate_prompt = f"""你是资深A股多空辩论主持人。请根据以下{TOTAL_ANALYSTS}位分析师的观点，组织一场多空辩论：

【多方阵营（加权得分: {weighted_signals["bull_ratio"]}%）】{', '.join(bull_reports) if bull_reports else '无'}
{chr(10).join('- ' + a for a in bull_args)}

【空方阵营（加权得分: {weighted_signals["bear_ratio"]}%）】{', '.join(bear_reports) if bear_reports else '无'}
{chr(10).join('- ' + a for a in bear_args)}

【一致性得分】{weighted_signals["consensus_score"]}%

请分析：
1. 多方最核心的3个论点是什么？
2. 空方最核心的3个论点是什么？
3. 多空双方的关键分歧在哪里？（重点标记高信心分析师之间的分歧）
4. 哪方的论据更有说服力？为什么？
5. 辩论结论：整体偏多还是偏空？

严格按以下JSON格式输出：
{{"bull_core":["多方核心论点1","多方核心论点2","多方核心论点3"],"bear_core":["空方核心论点1","空方核心论点2","空方核心论点3"],"conflicts":["分歧1","分歧2"],"winner":"多方/空方/势均力敌","winner_reason":"50字内原因","conclusion":"偏多/偏空/中性","debate_summary":"100字内辩论总结"}}"""

    raw = await call_deepseek(
        "你是资深A股多空辩论主持人，风格犀利，直击要害。请用中文回答。",
        debate_prompt
    )
    result = extract_json(raw)
    result["bull_all"] = bull_args
    result["bear_all"] = bear_args
    result["weighted_signals"] = weighted_signals
    return result


async def run_risk_assessment(reports: list, debate: dict) -> dict:
    """增强风险评估 - 引入历史波动率、相关性风险、动态止损"""
    signals = [r.get("signal", "中性") for r in reports]
    confidences = [r.get("confidence", 3) for r in reports]
    weighted_signals = debate.get("weighted_signals", {})

    # Calculate signal divergence as risk factor
    bull_count = sum(1 for s in signals if "多" in s)
    bear_count = sum(1 for s in signals if "空" in s)
    divergence = abs(bull_count - bear_count) / max(len(signals), 1)

    risk_prompt = f"""你是资深风险评估师。根据{TOTAL_ANALYSTS}位分析师的报告和多空辩论结果，评估该股票的风险水平：

【分析师信号分布】
{chr(10).join(f'- {r["name"]}: {r.get("signal","中性")} (信心{r.get("confidence",3)}/5, 强度{r.get("strength","弱")})' for r in reports)}

【加权信号】多方{weighted_signals.get("bull_ratio", 50)}% / 空方{weighted_signals.get("bear_ratio", 50)}%
【一致性得分】{weighted_signals.get("consensus_score", 0)}%
【辩论结论】{debate.get("conclusion", "中性")}，{debate.get("winner_reason", "")}

请评估：
1. 综合风险等级（1-10，1最低10最高）
2. 最大风险因素（3个），包含：历史波动率风险、行业相关性风险、流动性风险
3. 风险是否可控
4. 什么情况下风险会急剧上升
5. 动态止损建议（根据波动率给出百分比止损位）
6. 相关性风险分析（该股与大盘/行业的相关性）
7. 历史波动率评估（近期波动率是否异常）

严格按以下JSON格式输出：
{{"risk_score":5,"risk_level":"中低/中/中高/高","max_risks":[{{"factor":"风险因素","severity":"高/中/低","detail":"说明"}}],"controllable":true,"risk_trigger":"风险急剧上升的条件","stop_loss_advice":"动态止损建议（含百分比）","correlation_risk":"相关性风险分析","volatility_assessment":"历史波动率评估","risk_summary":"100字内风险总结"}}"""

    raw = await call_deepseek(
        "你是资深风险评估师，风格审慎客观，擅长量化风险分析。请用中文回答。",
        risk_prompt
    )
    return extract_json(raw)


async def run_final_recommendation(reports: list, debate: dict, risk: dict, user_profile: dict = None) -> dict:
    """个性化最终建议"""
    weighted_signals = debate.get("weighted_signals", {})

    # Build user profile context
    profile_context = ""
    if user_profile:
        holdings = user_profile.get("holdings", [])
        capital = user_profile.get("capital", "")
        style = user_profile.get("style", "balanced")
        style_map = {"aggressive": "激进型", "balanced": "稳健型", "conservative": "保守型"}
        profile_context = f"""
【用户画像】
- 投资风格：{style_map.get(style, '稳健型')}
- 账户资金规模：{capital}
- 当前持仓：{', '.join(holdings) if holdings else '无持仓信息'}
请根据用户风格调整仓位建议：激进型可适当放大仓位，保守型应降低仓位。"""

    rec_prompt = f"""你是首席投资决策官。综合以下信息，给出最终投资建议：

【{TOTAL_ANALYSTS}位分析师观点】
{chr(10).join(f'- {r["name"]}: {r.get("signal","中性")} ({r.get("strength","弱")}), {r.get("summary","")}' for r in reports)}

【加权信号】多方{weighted_signals.get("bull_ratio", 50)}% / 空方{weighted_signals.get("bear_ratio", 50)}%
【一致性得分】{weighted_signals.get("consensus_score", 0)}%
【多空辩论结论】{debate.get("conclusion", "中性")}
【辩论胜方】{debate.get("winner", "势均力敌")}
【风险评分】{risk.get("risk_score", 5)}/10 ({risk.get("risk_level", "中")})
{profile_context}

请给出最终建议：
1. 操作方向：BUY / HOLD / SELL
2. 仓位建议（占总资金比例）
3. 信心等级（1-5）
4. 3条核心理由
5. 操作策略（具体怎么操作）
6. 时间周期（短线/中线/长线）

严格按以下JSON格式输出：
{{"action":"BUY/HOLD/SELL","position":30,"confidence":3,"core_reasons":["理由1","理由2","理由3"],"strategy":"操作策略描述","time_horizon":"短线/中线/长线","entry_condition":"入场条件","exit_condition":"出场条件","final_summary":"100字内最终总结"}}"""

    raw = await call_deepseek(
        "你是首席投资决策官，风格果断但有理有据。请用中文回答。你必须严格从BUY/HOLD/SELL中三选一。",
        rec_prompt
    )
    result = extract_json(raw)
    action = result.get("action", "HOLD").upper().strip()
    if "BUY" in action or "买入" in action:
        result["action"] = "BUY"
    elif "SELL" in action or "卖出" in action:
        result["action"] = "SELL"
    else:
        result["action"] = "HOLD"
    try:
        result["position"] = min(100, max(0, int(result.get("position", 30))))
    except:
        result["position"] = 30
    try:
        result["confidence"] = min(5, max(1, int(result.get("confidence", 3))))
    except:
        result["confidence"] = 3
    return result


@app.get("/api/analyze/{stock_code}")
async def analyze_stock(
    stock_code: str,
    name: str = Query(default=""),
    capital: str = Query(default=""),
    style: str = Query(default="balanced"),
    holdings: str = Query(default="")
):
    """SSE流式分析接口 - 并行执行"""
    user_profile = {
        "capital": capital,
        "style": style,
        "holdings": [h.strip() for h in holdings.split(",") if h.strip()] if holdings else []
    }

    async def event_generator():
        # Phase 1: 并行运行所有分析师
        analyst_count = [0]

        async def on_analyst_complete(idx, analyst, result, total_done):
            analyst_count[0] = total_done
            yield_data = {
                "event": "progress",
                "data": json.dumps({
                    "phase": "analyst",
                    "current": total_done,
                    "total": TOTAL_ANALYSTS,
                    "analyst_id": analyst["id"],
                    "analyst_name": analyst["name"],
                    "status": "analyzing"
                }, ensure_ascii=False)
            }
            yield yield_data
            yield_data2 = {
                "event": "analyst",
                "data": json.dumps(result, ensure_ascii=False)
            }
            yield yield_data2

        # Since SSE generator can't easily yield from nested callbacks,
        # use a queue instead
        event_queue = asyncio.Queue()

        async def progress_callback(idx, analyst, result, total_done):
            await event_queue.put(("progress", {
                "phase": "analyst",
                "current": total_done,
                "total": TOTAL_ANALYSTS,
                "analyst_id": analyst["id"],
                "analyst_name": analyst["name"],
                "status": "analyzing"
            }))
            await event_queue.put(("analyst", result))

        # Start parallel analysis
        analysis_task = asyncio.create_task(
            run_analysts_parallel(ANALYSTS, stock_code, name or stock_code, progress_callback)
        )

        # Yield events as they come
        reports = None
        while True:
            try:
                event_type, data = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                yield {
                    "event": event_type,
                    "data": json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
                }
            except asyncio.TimeoutError:
                if analysis_task.done():
                    reports = analysis_task.result()
                    # Flush remaining queue
                    while not event_queue.empty():
                        try:
                            event_type, data = event_queue.get_nowait()
                            yield {
                                "event": event_type,
                                "data": json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
                            }
                        except asyncio.QueueEmpty:
                            break
                    break

        if reports is None:
            reports = await analysis_task

        # Phase 2: 加权多空辩论
        yield {
            "event": "progress",
            "data": json.dumps({
                "phase": "debate",
                "status": "analyzing"
            }, ensure_ascii=False)
        }

        debate = await run_debate(reports)
        yield {
            "event": "debate",
            "data": json.dumps(debate, ensure_ascii=False)
        }

        # Phase 3: 增强风险评估
        yield {
            "event": "progress",
            "data": json.dumps({
                "phase": "risk",
                "status": "analyzing"
            }, ensure_ascii=False)
        }

        risk = await run_risk_assessment(reports, debate)
        yield {
            "event": "risk",
            "data": json.dumps(risk, ensure_ascii=False)
        }

        # Phase 4: 个性化最终建议
        yield {
            "event": "progress",
            "data": json.dumps({
                "phase": "recommendation",
                "status": "analyzing"
            }, ensure_ascii=False)
        }

        recommendation = await run_final_recommendation(reports, debate, risk, user_profile)
        yield {
            "event": "recommendation",
            "data": json.dumps(recommendation, ensure_ascii=False)
        }

        # Save complete result
        full_result = {
            "stock_code": stock_code,
            "stock_name": name or stock_code,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reports": reports,
            "debate": debate,
            "risk": risk,
            "recommendation": recommendation,
            "user_profile": user_profile
        }

        try:
            data_dir = os.path.join(os.path.dirname(__file__), "data")
            os.makedirs(data_dir, exist_ok=True)
            filepath = os.path.join(data_dir, f"analyst_{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(full_result, f, ensure_ascii=False, indent=2)
        except:
            pass

        yield {
            "event": "done",
            "data": json.dumps({"timestamp": full_result["timestamp"]}, ensure_ascii=False)
        }

    return EventSourceResponse(event_generator())


@app.get("/api/history")
async def get_history(stock_code: str = Query(default="")):
    """获取历史分析记录"""
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    pattern = f"analyst_{stock_code}_*.json" if stock_code else "analyst_*.json"
    files = sorted(glob.glob(os.path.join(data_dir, pattern)), reverse=True)[:20]

    history = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                history.append({
                    "filename": os.path.basename(f),
                    "stock_code": data.get("stock_code", ""),
                    "stock_name": data.get("stock_name", ""),
                    "timestamp": data.get("timestamp", ""),
                    "action": data.get("recommendation", {}).get("action", "HOLD"),
                    "confidence": data.get("recommendation", {}).get("confidence", 0),
                    "risk_score": data.get("risk", {}).get("risk_score", 0)
                })
        except:
            continue
    return {"history": history}


@app.get("/api/history/{filename}")
async def get_history_detail(filename: str):
    """获取历史分析详情"""
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    filepath = os.path.join(data_dir, filename)
    if not os.path.exists(filepath):
        return JSONResponse({"error": "记录不存在"}, status_code=404)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stock-search/{keyword}")
async def stock_search(keyword: str):
    """股票搜索接口"""
    search_prompt = f"""用户搜索关键词"{keyword}"，请返回最匹配的5只A股股票信息。
严格按以下JSON格式输出：
[{{"code":"600519","name":"贵州茅台","industry":"白酒"}}]"""

    raw = await call_deepseek(
        "你是A股股票信息查询助手。请准确返回股票代码和名称。请用中文回答。",
        search_prompt
    )
    result = extract_json(raw)
    if isinstance(result, dict):
        result = [result]
    return {"results": result}


@app.get("/")
async def serve_page():
    """服务前端页面"""
    html_path = os.path.join(os.path.dirname(__file__), "analyst.html")
    return FileResponse(html_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)
