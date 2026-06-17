#!/usr/bin/env python3
"""将 screener.py 的 recommendations.json 同步到 jh_summary.json（picks.html 读取）"""
import json, os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
REC_FILE = os.path.join(DATA_DIR, 'recommendations.json')
SUMMARY_FILE = os.path.join(DATA_DIR, 'jh_summary.json')

with open(REC_FILE, 'r', encoding='utf-8') as f:
    rec = json.load(f)

# 读取旧summary保留backtest/cumulative/stats_detail
old = {}
if os.path.exists(SUMMARY_FILE):
    with open(SUMMARY_FILE, 'r', encoding='utf-8') as f:
        old = json.load(f)

# 转换推荐格式
picks = rec.get('top_picks', [])
recommendations = []
for p in picks[:5]:
    recommendations.append({
        'name': p.get('name', ''),
        'code': p.get('code', ''),
        'score': round(p.get('score', 0), 1),
        'change': round(p.get('change', 0), 2),
        'price': round(p.get('score_price', p.get('price', 0)), 2),  # 评分价（非收盘价）
        'score_price': round(p.get('score_price', p.get('price', 0)), 2)
    })

summary = {
    'version': rec.get('version', 'v4.1'),
    'full_name': f"JH Screener {rec.get('version', 'v4.1')}",
    'strategy': rec.get('strategy', ''),
    'backtest': old.get('backtest', {}),
    'recommendations': recommendations,
    'updated_at': f"{rec.get('date', '')} {rec.get('time', '')}",
    'cumulative': old.get('cumulative', []),
    'stats_detail': old.get('stats_detail', {})
}

with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f"✅ jh_summary.json 已更新: {len(recommendations)} 只推荐, 时间 {summary['updated_at']}")
