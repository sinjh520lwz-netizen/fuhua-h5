#!/usr/bin/env python3
"""重新拉取全A股K线数据（252天），覆盖原文件"""
import json, urllib.request, time, sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# 从原文件读取已有股票列表
old_file = os.path.join(DATA_DIR, 'all_klines_60d.json')
with open(old_file) as f:
    old_data = json.load(f)

codes = [(code, info.get('name','')) for code, info in old_data.items()]
print(f"需更新: {len(codes)}只股票 → 252天")

def fetch_kline_252(code, name):
    """腾讯API获取252天日K线"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,252,qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        text = urllib.request.urlopen(req, timeout=12).read().decode('utf-8')
        json_str = text[text.index('{'):text.rindex('}')+1]
        data = json.loads(json_str)
        raw = data.get('data', {}).get(f'{prefix}{code}', {})
        kdata = raw.get('qfqday', []) or raw.get('day', [])
        return code, {'code': code, 'name': name, 'klines': kdata}
    except Exception as e:
        return code, None

new_data = {}
success = 0
batch_size = 20

with ThreadPoolExecutor(max_workers=8) as executor:
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        futures = {executor.submit(fetch_kline_252, c, n): c for c, n in batch}
        batch_results = {}
        for f in as_completed(futures):
            code, result = f.result()
            if result:
                batch_results[code] = result
                success += 1
        new_data.update(batch_results)
        
        if (i // batch_size + 1) % 5 == 0 or i + batch_size >= len(codes):
            pct = min(i + batch_size, len(codes))
            print(f"  进度: {pct}/{len(codes)} → 成功{success}只")
        time.sleep(0.1)  # 每批间隔

print(f"\n拉取完成: {success}/{len(codes)}")

# 写入
out_file = os.path.join(DATA_DIR, 'all_klines_60d.json')
with open(out_file, 'w') as f:
    json.dump(new_data, f, ensure_ascii=False)
print(f"已保存: {out_file}")
print(f"校验: {len(new_data)}只股票")

# 统计日期范围
dates = set()
for code, info in new_data.items():
    for k in info.get('klines', []):
        if isinstance(k, list) and len(k) >= 6:
            dates.add(k[0])
sorted_dates = sorted(dates)
print(f"日期范围: {sorted_dates[0]} ~ {sorted_dates[-1]} ({len(sorted_dates)}个交易日)")
