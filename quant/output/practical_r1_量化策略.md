Query: 深度研究量化策略，难度等级：1级。
    要求：
    - 聚焦实战应用，不要纯理论
    - 用真实A股数据和代码示例
    - 每个方法都要说明：什么场景用、怎么用、注意事项
    - 输出不少于1万字
    - 必须包含可直接运行的Python代码
    - 重点：能帮我提高选股准确率和回测效率
正在初始化爱马仕机器人...
────────────────────────────────────────

  ┊ 📚 preparing skill_view…
  ┊ 📚 preparing skill_view…
  ┊ 📚 preparing skill_view…
  ┊ 📚 技能      a-stock-data  0.1s
  ┊ 📚 技能      quant-trading-toolkit  0.1s
  ┊ 📚 技能      quant-stock-screener  0.1s
  ┊ 📚 preparing skill_view…
  ┊ 📚 技能      cross-sectional-ranking-factor-system  0.0s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    Now let me load the key reference files for detailed code and methodology:
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 📚 preparing skill_view…
  ┊ 📚 preparing skill_view…
  ┊ 📚 技能      cross-sectional-ranking-factor-system  0.1s
  ┊ 📚 技能      cross-sectional-ranking-factor-system  0.1s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    Now I have all the context. Let me write the comprehensive research document with runnable code.
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ ✍️ preparing write_file…
  ┊ ✍️  写入      /root/a_stock_quant_deep_research.py  0.2s
  ┊ review diff
a//root/a_stock_quant_deep_research.py → b//root/a_stock_quant_deep_research.py
@@ -0,0 +1,1884 @@
+#!/usr/bin/env python3
+# -*- coding: utf-8 -*-
+"""
+A股量化策略深度研究（实战版）
+======================================
+作者：JH量化研究团队
+日期：2026-06-11
+用途：可直接运行的Python代码 + 完整方法论文档
+
+使用方法：
+    python3 a_stock_quant_deep_research.py          # 运行全部代码示例
+    python3 a_stock_quant_deep_research.py --demo   # 只运行轻量演示
+    python3 a_stock_quant_deep_research.py --doc    # 只输出文档（不执行）
+
+依赖安装：
+    pip install baostock pandas numpy
+    # 可选：pip install MyTT（需要系统Python 3.10）
+"""
+
+import sys
+import json
+import time
+import math
+import urllib.request
+from datetime import datetime, timedelta
+from collections import defaultdict
+
+# ============================================================
+# 第一章：数据获取 — A股量化基础设施
+# ============================================================
+
+"""
+=== 第一章：数据获取 — A股量化基础设施 ===
+
+A股量化最大的坑不是策略，而是数据。
+很多初学者花大量时间研究因子，结果发现数据源被封、数据不一致、
+实时数据拿不到——整个回测结果都是假的。
+
+本章介绍经过实战验证的4条数据获取路径，按优先级排序：
+
+1. 腾讯财经 API（最稳定，不封IP）
+2. mootdx 通达信协议（最快，TCP直连）
+3. BaoStock（免费无限制，适合回测）
+4. 东方财富（独有数据但会封IP）
+
+关键教训：
+- 东方财富push2 API从服务器访问会被封IP（HTTP 000）
+- 腾讯API返回GBK编码，必须用decode('gbk')
+- BaoStock不支持并发查询，必须顺序调用
+- Pandas 3.0+兼容：BaoStock的rs.get_data()已不能用
+"""
+
+
+def demo_tencent_realtime():
+    """
+    演示1：腾讯实时行情 — 批量获取A股实时数据
+    
+    场景：盘中盯盘、批量行情筛选、实时监控
+    速度：200只/批，约0.04秒
+    限制：无，不封IP
+    """
+    print("\n" + "="*60)
+    print("【演示1】腾讯实时行情 — 批量获取")
+    print("="*60)
+    
+    # 测试股票：茅台、平安银行、中兴通讯、工业富联
+    codes = ['600519', '000001', '000063', '601138']
+    prefixed = []
+    for c in codes:
+        if c.startswith(('6', '9')):
+            prefixed.append(f'sh{c}')
+        else:
+            prefixed.append(f'sz{c}')
+    
+    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
+    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
+    
+    try:
… omitted 1806 diff line(s) across 1 additional file(s)/section(s)

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    文件已写好，现在运行验证：
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | head -200  1.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | tail -80  1.0s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    K线API的key解析需要修正，让我检查一下：
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 💻 preparing terminal…
  ┊ 💻 $         python3 -c "
import urllib.request, json
url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param=sh600519,day,,,5,qfq'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
json_str = text[text.index('{'):text.rindex('}') + 1]
data = json.loads(json_str)
print('Keys:', list(data.get('data',{}).keys()))
for k,v in data.get('data',{}).items():
    if isinstance(v, dict):
        print(f'  {k}: {list(v.keys())}')
        for kk,vv in v.items():
            if isinstance(vv, list) and len(vv)>0:
                print(f'    {kk}: {len(vv)} items, first={vv[0]}')
" 2>&1  0.2s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    K线的key是qfqday不是day，修正代码：
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 🔧 preparing patch…
  ┊ 🔧 修改      /root/a_stock_quant_deep_research.py  0.1s
  ┊ review diff
a//root/a_stock_quant_deep_research.py → b//root/a_stock_quant_deep_research.py
@@ -129,12 +129,9 @@
         data = json.loads(json_str)
         
         # 解析K线数据
-        kline_key = f"qfq{prefix}{code}"
-        klines = data.get('data', {}).get(kline_key, {}).get('day', [])
-        
-        if not klines:
-            # 有时key不含qfq前缀
-            klines = data.get('data', {}).get(f"{prefix}{code}", {}).get('day', [])
+        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
+        # 腾讯K线API：前复权key是qfqday，不复权是day
+        klines = stock_data.get('qfqday', stock_data.get('day', []))
         
         if klines:
             print(f"  茅台({code}) 最近{len(klines)}根日K线:")
  ┊ 🔎 preparing search_files…
  ┊ 🔎 查内容       kline_key.*qfq  0.1s
  ┊ 🐍 preparing execute_code…
  ┊ 🐍 执行      from hermes_tools import read_file, patch  0.0s [BLOCKED: execute_code runs arbitrary local Py...]
  ┊ 📖 preparing read_file…
  ┊ 📖 读取      /root/a_stock_quant_deep_research.py  0.0s
  ┊ 🔧 preparing patch…
  ┊ 🔧 修改      /root/a_stock_quant_deep_research.py  0.4s [Found 4 matches for old_string. Provide more ...]
  ┊ 🔧 preparing patch…
  ┊ 🔧 修改      /root/a_stock_quant_deep_research.py  0.1s
  ┊ review diff
a//root/a_stock_quant_deep_research.py → b//root/a_stock_quant_deep_research.py
@@ -437,10 +437,10 @@
         json_str = text[text.index('{'):text.rindex('}') + 1]
         data = json.loads(json_str)
         
-        kline_key = f"qfq{prefix}{code}"
-        raw = data.get('data', {}).get(kline_key, {}).get('day', [])
+        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
+        raw = stock_data.get('qfqday', stock_data.get('day', []))
         if not raw:
-            raw = data.get('data', {}).get(f"{prefix}{code}", {}).get('day', [])
+            raw = stock_data.get('day', stock_data.get('qfqday', []))
         
         if not raw:
             print("  [无K线数据]")
@@ -894,10 +894,10 @@
         json_str = text[text.index('{'):text.rindex('}') + 1]
         data = json.loads(json_str)
         
-        kline_key = f"qfq{prefix}{code}"
-        raw = data.get('data', {}).get(kline_key, {}).get('day', [])
+        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
+        raw = stock_data.get('qfqday', stock_data.get('day', []))
         if not raw:
-            raw = data.get('data', {}).get(f"{prefix}{code}", {}).get('day', [])
+            raw = stock_data.get('day', stock_data.get('qfqday', []))
         
         if not raw:
             print("  [无K线数据]")
@@ -1073,10 +1073,10 @@
         json_str = text[text.index('{'):text.rindex('}') + 1]
         data = json.loads(json_str)
         
-        kline_key = f"qfq{prefix}{code}"
-        raw = data.get('data', {}).get(kline_key, {}).get('day', [])
+        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
+        raw = stock_data.get('qfqday', stock_data.get('day', []))
         if not raw:
-            raw = data.get('data', {}).get(f"{prefix}{code}", {}).get('day', [])
+            raw = stock_data.get('day', stock_data.get('qfqday', []))
         
         if len(raw) < 30:
             print("  [数据不足]")

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    现在重新运行验证全部演示：
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -E '【演示|茅台|收盘价|均线|MACD|RSI|量比|评分|推荐|无数据|无K线|错误' | head -40  1.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -E '总交易|胜率|平均收益|止盈|止损|排名|代码|演示12|推荐买入|全部完成' | head -20  1.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --doc 2>&1 | wc -c && echo "---" && cat a_stock_quant_deep_research.py | wc -c && echo "---Python code chars" && python3 -c "
# Count Chinese characters + English words in the docstring/comments
import re
with open('a_stock_quant_deep_research.py','r') as f:
    content = f.read()
# Extract all triple-quoted strings and comments
docstrings = re.findall(r'\"\"\"(.*?)\"\"\"', content, re.DOTALL)
comments = re.findall(r'\"([^\"]+)\"', content)
total_text = ' '.join(docstrings + comments)
# Count Chinese chars
cn = len(re.findall(r'[\u4e00-\u9fff]', total_text))
# Count English words
en = len(re.findall(r'[a-zA-Z]+', total_text))
print(f'Chinese chars: {cn}')
print(f'English words: {en}')
print(f'Estimated total: ~{cn + en} words equivalent')
"  0.2s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -A 30 '演示12'  1.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         python3 -c "
import urllib.request, json
code='000001'; prefix='sz'
url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,60,qfq'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
json_str = text[text.index('{'):text.rindex('}') + 1]
data = json.loads(json_str)
stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
raw = stock_data.get('qfqday', stock_data.get('day', []))
print(f'Keys: {list(stock_data.keys())}')
print(f'Rows: {len(raw)}')
if raw: print(f'First: {raw[0]}')
" 2>&1  0.2s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 -c "
import urllib.request, json, time
test_stocks = [
    {'code': '600519', 'name': '贵州茅台'},
    {'code': '000001', 'name': '平安银行'},
    {'code': '000333', 'name': '美的集团'},
]
for stock in test_stocks:
    code = stock['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    try:
        url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,60,qfq'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        raw = stock_data.get('qfqday', stock_data.get('day', []))
        print(f'{stock[\"name\"]}: {len(raw)} rows')
    except Exception as e:
        print(f'{stock[\"name\"]}: ERROR {e}')
    time.sleep(0.1)
" 2>&1  0.6s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 -c "
import urllib.request, json, time, sys
sys.path.insert(0, '.')
from a_stock_quant_deep_research import quick_analyze, score_early_entry

test_stocks = [
    {'code': '600519', 'name': '贵州茅台'},
    {'code': '000001', 'name': '平安银行'},
    {'code': '000333', 'name': '美的集团'},
]
for stock in test_stocks:
    code = stock['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    try:
        url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,60,qfq'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        raw = stock_data.get('qfqday', stock_data.get('day', []))
        print(f'{stock[\"name\"]}: raw={len(raw)}')
        
        klines = [{'date':k[0],'open':float(k[1]),'close':float(k[2]),
                   'high':float(k[3]),'low':float(k[4]),
                   'volume':float(k[5]) if len(k)>5 else 0} for k in raw]
        ind = quick_analyze(klines)
        if ind:
            score, det = score_early_entry(ind, ind['today_change'])
            print(f'  score={score}, change={ind[\"today_change\"]:.2f}%')
        else:
            print(f'  ind=None, klines len={len(klines)}')
    except Exception as e:
        print(f'{stock[\"name\"]}: ERROR {e}')
    time.sleep(0.1)
" 2>&1  0.6s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 -c "
import sys
sys.path.insert(0, '.')
from a_stock_quant_deep_research import run_mini_screener
run_mini_screener()
" 2>&1  0.6s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 -c "
import sys
sys.path.insert(0, '.')

# Patch to add debug output
import a_stock_quant_deep_research as m

test_stocks = [
    {'code': '600519', 'name': '贵州茅台'},
    {'code': '000001', 'name': '平安银行'},
]
import urllib.request, json, time

for stock in test_stocks:
    code = stock['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    try:
        url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,60,qfq'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        text = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}') + 1]
        data = json.loads(json_str)
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        raw = stock_data.get('qfqday', stock_data.get('day', []))
        print(f'  {stock[\"name\"]}: raw len={len(raw)}')
        
        if len(raw) < 30:
            print(f'  SKIP (<30)')
            continue
        
        klines = [{'date':k[0],'open':float(k[1]),'close':float(k[2]),
                   'high':float(k[3]),'low':float(k[4]),
                   'volume':float(k[5]) if len(k)>5 else 0} for k in raw]
        
        ind = m.quick_analyze(klines)
        print(f'  ind={ind is not None}')
        if ind is None:
            continue
        
        today_change = ind['today_change']
        score, details = m.score_early_entry(ind, today_change)
        print(f'  score={score}')
        
        results = []
        results.append({
            'code': code, 'name': stock['name'],
            'price': klines[-1]['close'], 'change': today_change,
            'score': score, 'details': details,
            'ma5': ind['ma5'], 'rsi': ind['rsi'],
            'macd': ind['dif'] - ind['dea'],
        })
        print(f'  results now has {len(results)} items')
    except Exception as e:
        print(f'  ERROR: {e}')
        import traceback; traceback.print_exc()
    time.sleep(0.1)

print(f'Final results: {len(results)}')
" 2>&1  0.4s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 -c "
import sys
sys.path.insert(0, '.')
import a_stock_quant_deep_research as m
import traceback

try:
    m.run_mini_screener()
except Exception as e:
    print(f'EXCEPTION: {e}')
    traceback.print_exc()
" 2>&1  0.6s
  ┊ 📖 preparing read_file…
  ┊ 📖 读取      /root/a_stock_quant_deep_research.py  0.0s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    I see - the screener function still has the old kline key pattern. Let me fix it:
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 🔧 preparing patch…
  ┊ 🔧 修改      /root/a_stock_quant_deep_research.py  0.1s
  ┊ review diff
a//root/a_stock_quant_deep_research.py → b//root/a_stock_quant_deep_research.py
@@ -1668,10 +1668,8 @@
             json_str = text[text.index('{'):text.rindex('}') + 1]
             data = json.loads(json_str)
             
-            kline_key = f"qfq{prefix}{code}"
-            raw = data.get('data', {}).get(kline_key, {}).get('day', [])
-            if not raw:
-                raw = data.get('data', {}).get(f"{prefix}{code}", {}).get('day', [])
+            stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
+            raw = stock_data.get('qfqday', stock_data.get('day', []))
             
             if len(raw) < 30:
                 continue
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -E '演示12|推荐买入|价格|评分|全部完成|止盈|止损' | head -20  2.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -A 20 '演示12'  2.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep -A 20 '演示6'  2.0s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    全部12个演示运行完成。现在验证完整输出：
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 💻 preparing terminal…
  ┊ 💻 $         cd /root && python3 a_stock_quant_deep_research.py --demo 2>&1 | wc -l && echo "---总行数" && python3 a_stock_quant_deep_research.py --demo 2>&1 | grep '【演示' | wc -l && echo "---演示数"  3.8s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    完成。文件路径：/root/a_stock_quant_deep_research.py
    
    === 交付物总结 ===
    
    文件大小：67KB Python代码，约13,600字（中文9,790字 + 英文3,846词）
    运行方式：
      python3 /root/a_stock_quant_deep_research.py          # 全量运行
      python3 /root/a_stock_quant_deep_research.py --demo   # 轻量演示
      python3 /root/a_stock_quant_deep_research.py --doc    # 只看文档
    
    === 10章内容清单 ===
    
    第1章  数据获取 — 腾讯API/BaoStock/mootdx实战（含实时行情+K线获取代码）
    第2章  技术指标 — 从零实现MA/MACD/RSI/布林带/量比/KDJ（6个指标）
    第3章  多因子评分 — JH vXS2 11因子评分系统完整代码
    第4章  回测系统 — 日线模拟+5分钟逐根条件单模拟方法论
    第5章  止损优化 — TP+5%/SL-4%数学推导（盈亏平衡点44.4%）
    第6章  全A股扫描 — 三层筛选架构（5525只→初筛→TOP200→评分→TOP5）
    第7章  因子验证 — 实测因子有效性排名+分数段质量分析
    第8章  实战手册 — 从选股到执行的完整操作流程
    第9章  十大陷阱 — 横截面排名vs绝对评分、5分钟回测、止损线优化等
    第10章 精简选股引擎 — 10只股票实时评分演示
    
    === 核心结论（9个月/286交易日/3024只全A股验证） ===
    
    1. 横截面排名 >> 绝对评分（T+1胜率62.2% vs 43%）
    2. 止损-4%让策略从亏损变盈利（盈亏平衡点仅44.4%）
    3. 简单3因子+排名 > 复杂11因子+评分
    4. 越加严过滤越差（高波动才有高弹跳）
    5. 每轮只改1-2个参数，先测分布再定阈值
╰──────────────────────────────────────────────────────────────────────────────╯
