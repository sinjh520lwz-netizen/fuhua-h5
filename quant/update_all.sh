#!/bin/bash
# JH量化盘中自动更新 v2.5
# 工作日 9:25-15:05 每5分钟运行
# v2.5优化：盘中仅跑量化评分（零token），AI只在3个关键时点运行

HOUR=$(date +%H)
MIN=$(date +%M)
DOW=$(date +%u)
TIME_NUM=$((HOUR * 100 + MIN))

# 非工作日跳过
[ "$DOW" -gt 5 ] && exit 0

# 非交易时间跳过
[ $TIME_NUM -lt 925 ] || [ $TIME_NUM -gt 1458 ] && exit 0

cd /var/www/html/h5/quant

# 预下载同花顺热门列表
curl -s -m 10 -o data/ths_hot_list.json \
  -H "User-Agent: Mozilla/5.0" \
  -H "Referer: https://www.10jqka.com.cn/" \
  "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal&page_size=200&page=1" 2>/dev/null

# 预下载东方财富涨幅榜
curl -s -m 10 -o data/east_momentum.json \
  -H "User-Agent: Mozilla/5.0" \
  -H "Referer: https://quote.eastmoney.com/" \
  "http://push2.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=50&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f12,f14" 2>/dev/null

# 判断是否在AI分析时段（9:35-9:40 / 13:05-13:10 / 14:45-14:50）
IN_AI_WINDOW=0
if ([ $TIME_NUM -ge 935 ] && [ $TIME_NUM -le 940 ]) || \
   ([ $TIME_NUM -ge 1305 ] && [ $TIME_NUM -le 1310 ]) || \
   ([ $TIME_NUM -ge 1445 ] && [ $TIME_NUM -le 1450 ]); then
  IN_AI_WINDOW=1
fi

echo "[$(date '+%H:%M:%S')] 更新选股推荐..."

if [ $IN_AI_WINDOW -eq 1 ]; then
  # 关键时点：量化评分 + AI分析
  echo "  🤖 AI分析窗口，运行完整分析..."
  python3 screener.py > /tmp/screener.log 2>&1
else
  # 平时：仅量化评分
  python3 screener.py --skip-ai > /tmp/screener.log 2>&1
fi

# 运行反T分析引擎
echo "[$(date '+%H:%M:%S')] 更新反T分析..."
python3 t_engine.py > /tmp/t_engine.log 2>&1

# 运行接回推荐引擎
echo "[$(date '+%H:%M:%S')] 更新接回推荐..."
python3 buyback_engine.py > /tmp/buyback.log 2>&1

echo "[$(date '+%H:%M:%S')] 更新完成"

# 收盘后生成15天回测 + 权重优化
if [ "$HOUR" -ge 15 ]; then
  echo "[$(date '+%H:%M:%S')] 生成15天回测绩效数据..."
  python3 -c "
import json
from datetime import datetime, timedelta

with open('data/backtest_result.json') as f:
    bt = json.load(f)

daily_stats = bt.get('daily_stats', [])
daily_perf = []
for d in daily_stats:
    picks = d.get('picks', [])
    stocks = []
    for p in picks:
        stocks.append({
            'code': p.get('code', ''),
            'name': p.get('name', ''),
            'score': p.get('score', 0),
            'rec_price': p.get('entry_price', 0),
            'day1_return': round(p.get('t1_return', 0), 2),
            'max_return': round(p.get('max_return', 0), 2),
            'result': 'win' if p.get('t1_return', 0) > 0 else 'loss' if p.get('t1_return', 0) < 0 else 'flat'
        })
    
    daily_perf.append({
        'date': d['date'],
        'weekday': ['周一','周二','周三','周四','周五','周六','周日'][datetime.strptime(d['date'], '%Y-%m-%d').weekday()],
        'total_recommended': d.get('picks_count', 0),
        'total_evaluated': d.get('picks_count', 0),
        'wins': round(d.get('picks_count', 0) * d.get('t1_win_rate', 0) / 100),
        'losses': d.get('picks_count', 0) - round(d.get('picks_count', 0) * d.get('t1_win_rate', 0) / 100),
        'win_rate': d.get('t1_win_rate', 0),
        'avg_return': d.get('t1_avg_return', 0),
        'stocks': stocks
    })

all_picks = []
for d in daily_stats:
    all_picks.extend(d.get('picks', []))

total_wins = sum(1 for p in all_picks if p.get('t1_return', 0) > 0)
total_eval = len(all_picks)
overall_win_rate = round(total_wins / total_eval * 100, 1) if total_eval else 0
overall_avg_return = round(sum(p.get('t1_return', 0) for p in all_picks) / total_eval, 2) if total_eval else 0

summary = {
    'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'period': '近15个交易日回测',
    'total_evaluated': total_eval,
    'total_wins': total_wins,
    'overall_win_rate': overall_win_rate,
    'overall_avg_return': overall_avg_return,
    'daily': daily_perf
}
with open('data/performance_summary.json', 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print(f'15天回测: {total_eval}只次 | 胜率{overall_win_rate}% | 均涨{overall_avg_return:+.2f}%')
"

  echo "[$(date '+%H:%M:%S')] 权重自迭代优化..."
  python3 weight_optimizer.py > /tmp/weight_optimizer.log 2>&1
fi
