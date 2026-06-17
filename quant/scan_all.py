#!/usr/bin/env python3
"""
Scan ALL A-shares via Tencent API.
Filter: exclude 科创板(688), 创业板(300), 北交所(8xx/920).
Fetch real-time quotes for all valid stocks.
Then fetch 600-day klines for top N by volume.
Output: data/all_quotes.json + data/kline_cache.json
"""
import json, urllib.request, re, time, os, sys

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def gen_all_codes():
    """Generate all possible A-share codes."""
    codes = []
    # 深圳主板: 000xxx, 001xxx
    for i in range(0, 1000): codes.append(f'sz000{i:03d}')
    for i in range(0, 1000): codes.append(f'sz001{i:03d}')
    # 深圳中小板: 002xxx, 003xxx
    for i in range(0, 1000): codes.append(f'sz002{i:03d}')
    for i in range(0, 1000): codes.append(f'sz003{i:03d}')
    # 上海主板: 600xxx, 601xxx, 603xxx, 605xxx
    for i in range(0, 1000): codes.append(f'sh600{i:03d}')
    for i in range(0, 1000): codes.append(f'sh601{i:03d}')
    for i in range(0, 1000): codes.append(f'sh603{i:03d}')
    for i in range(0, 1000): codes.append(f'sh605{i:03d}')
    return codes

def is_excluded(code):
    """Exclude 科创板688, 创业板300/301, 北交所8xx/920."""
    c = code  # pure digits
    if c.startswith('688'): return True   # 科创板
    if c.startswith('300') or c.startswith('301'): return True  # 创业板
    if c.startswith('8') or c.startswith('920'): return True    # 北交所
    return False

def fetch_batch(batch_codes):
    """Fetch real-time quotes for a batch of codes. Returns list of dicts."""
    url = 'https://qt.gtimg.cn/q=' + ','.join(batch_codes)
    results = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        text = resp.read().decode('gbk', errors='ignore')
        for line in text.split(';'):
            parts = line.split('~')
            if len(parts) < 50:
                continue
            try:
                code = parts[2].strip()
                name = parts[1].strip()
                price = float(parts[3]) if parts[3] else 0
                prev_close = float(parts[4]) if parts[4] else 0
                volume = int(parts[6]) if parts[6] else 0  # 手
                change_pct = float(parts[32]) if parts[32] else 0
                turnover = float(parts[38]) if parts[38] else 0  # 换手率%
                mcap = float(parts[45]) if parts[45] else 0  # 总市值(亿)
                float_mcap = float(parts[44]) if parts[44] else 0  # 流通市值(亿)
            except (ValueError, IndexError):
                continue
            
            if price <= 0 or volume <= 0:
                continue
            if 'ST' in name or '*ST' in name or '退' in name:
                continue
            
            results.append({
                'code': code,
                'name': name,
                'price': price,
                'prevClose': prev_close,
                'volume': volume,
                'change': change_pct,
                'turnover': turnover,
                'mcap': mcap,
                'floatMcap': float_mcap
            })
    except Exception as e:
        print(f"  [WARN] batch fetch error: {e}", file=sys.stderr)
    return results

def fetch_kline(code, days=600):
    """Fetch historical kline from Tencent. Returns list of [date,open,close,high,low,vol]."""
    prefix = 'sh' if code[0] == '6' else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline&param={prefix}{code},day,,,{days},qfq'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        text = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        m = re.search(r'kline=(.+)', text)
        if not m: return []
        d = json.loads(m.group(1))
        kl = d.get('data', {}).get(f'{prefix}{code}', {})
        arr = kl.get('day') or kl.get('qfqday') or []
        if len(arr) < 60: return []
        result = []
        for k in arr:
            try:
                # Some entries have dividend info as 7th element (dict), skip those checks
                result.append([k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(float(k[5]))])
            except (ValueError, IndexError, TypeError):
                continue
        return result if len(result) >= 60 else []
    except Exception as e:
        return []

# ======== MAIN ========
print("=" * 60)
print("A股全量扫描 (剔除科创/创业/北交)")
print("=" * 60)

# Step 1: Generate all possible codes
all_codes = gen_all_codes()
print(f"\n[1/3] 扫描行情... 代码池: {len(all_codes)} 个")

# Step 2: Fetch quotes in batches
all_quotes = []
batch_size = 80
for i in range(0, len(all_codes), batch_size):
    batch = all_codes[i:i + batch_size]
    quotes = fetch_batch(batch)
    all_quotes.extend(quotes)
    done = min(i + batch_size, len(all_codes))
    if (i // batch_size) % 25 == 0:
        print(f"  进度: {done}/{len(all_codes)}, 已找到: {len(all_quotes)} 只有效股票")
    time.sleep(0.05)

# Step 3: Filter
print(f"\n[2/3] 过滤...")
before_filter = len(all_quotes)
valid_quotes = [q for q in all_quotes if not is_excluded(q['code'])]
excluded = before_filter - len(valid_quotes)
print(f"  扫描到: {before_filter} 只, 剔除科创/创业/北交: {excluded} 只, 保留: {len(valid_quotes)} 只")

# Sort by volume (liquidity)
valid_quotes.sort(key=lambda x: x['volume'], reverse=True)

# Price filter for 1.5万 capital: <=75元 (buy 100 shares = 7500 max)
affordable = [q for q in valid_quotes if q['price'] <= 75]
print(f"  价格<=75元(可买1手): {len(affordable)} 只")

# Save all valid quotes
with open(os.path.join(DATA_DIR, 'all_quotes.json'), 'w', encoding='utf-8') as f:
    json.dump(valid_quotes, f, ensure_ascii=False)
print(f"  已保存: data/all_quotes.json ({len(valid_quotes)} 只)")

# Step 4: Fetch klines for top stocks by volume
TOP_N = 500  # Top 500 by liquidity
top_stocks = valid_quotes[:TOP_N]
print(f"\n[3/3] 下载K线数据 (Top {TOP_N} by成交量)...")

kline_cache = {}
failed = 0
for idx, s in enumerate(top_stocks):
    kl = fetch_kline(s['code'], 600)
    if kl:
        kline_cache[s['code']] = kl
    else:
        failed += 1
    if (idx + 1) % 50 == 0:
        print(f"  K线进度: {idx + 1}/{TOP_N}, 成功: {len(kline_cache)}, 失败: {failed}")
    time.sleep(0.08)

print(f"\nK线完成: {len(kline_cache)} 只成功, {failed} 只失败")
with open(os.path.join(DATA_DIR, 'kline_cache.json'), 'w') as f:
    json.dump(kline_cache, f)
print(f"已保存: data/kline_cache.json")

# Summary
print("\n" + "=" * 60)
print("扫描完成!")
print(f"  全A股(不含科创/创业/北交): {len(valid_quotes)} 只")
print(f"  可买(<=75元): {len(affordable)} 只")
print(f"  K线数据: {len(kline_cache)} 只")
print(f"  Top5 成交量:")
for q in valid_quotes[:5]:
    print(f"    {q['code']} {q['name']} ¥{q['price']} vol={q['volume']}手 chg={q['change']}%")
print("=" * 60)
