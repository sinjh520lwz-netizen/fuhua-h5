#!/usr/bin/env python3
"""
每日调度脚本 - daily_scheduler.py
功能：
1. 检查是否为交易日
2. 调用复盘脚本
3. 调用迭代优化脚本
4. 运行选股生成次日推荐
5. 生成综合日报
6. 设置 crontab 定时任务

运行时间：每个交易日 15:30（收盘后30分钟）
"""
import json, os, sys, time, logging, subprocess
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
REPORT_DIR = os.path.join(DATA_DIR, 'review_reports')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, 'scheduler.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ]
)
log = logging.getLogger('daily_scheduler')

# 2025-2026 A股节假日（非交易日）
HOLIDAYS_2025_2026 = {
    # 2025年
    '2025-01-01',  # 元旦
    '2025-01-28', '2025-01-29', '2025-01-30', '2025-01-31',
    '2025-02-03', '2025-02-04',  # 春节
    '2025-04-04',  # 清明
    '2025-05-01', '2025-05-02', '2025-05-05',  # 劳动节
    '2025-05-31', '2025-06-02',  # 端午
    '2025-10-01', '2025-10-02', '2025-10-03',
    '2025-10-06', '2025-10-07',  # 国庆
    # 2026年
    '2026-01-01', '2026-01-02',  # 元旦
    '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
    '2026-02-23', '2026-02-24',  # 春节
    '2026-04-06',  # 清明
    '2026-05-01',  # 劳动节
    '2026-06-19',  # 端午
    '2026-10-01', '2026-10-02', '2026-10-05',
    '2026-10-06', '2026-10-07',  # 国庆
}


def is_trading_day(date=None):
    """
    判断是否为交易日
    - 周一到周五
    - 非法定节假日
    """
    if date is None:
        date = datetime.now()
    date_str = date.strftime('%Y-%m-%d')

    # 周末
    if date.weekday() >= 5:
        return False

    # 法定节假日
    if date_str in HOLIDAYS_2025_2026:
        return False

    return True


def run_script(script_name, args=None):
    """
    运行子脚本
    返回: (success: bool, output: str)
    """
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.exists(script_path):
        log.error(f"脚本不存在: {script_path}")
        return False, f"脚本不存在: {script_path}"

    cmd = [sys.executable, script_path]
    if args:
        cmd.extend(args)

    log.info(f"运行: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=300, cwd=SCRIPT_DIR
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            log.info(f"✅ {script_name} 执行成功")
            return True, output
        else:
            log.warning(f"⚠️ {script_name} 返回码: {result.returncode}")
            return False, output
    except subprocess.TimeoutExpired:
        log.error(f"❌ {script_name} 执行超时")
        return False, "执行超时"
    except Exception as e:
        log.error(f"❌ {script_name} 执行异常: {e}")
        return False, str(e)


def generate_daily_summary(review_result, optimize_result):
    """生成综合日报"""
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    today_compact = now.strftime('%Y%m%d')

    lines = []
    lines.append(f"# 📊 JH量化系统日报 {today}")
    lines.append(f"")
    lines.append(f"> 生成时间: {now.strftime('%H:%M:%S')}")
    lines.append(f"> 系统: JH Screener v2.2 + Alpha191 混合引擎")
    lines.append(f"")

    # 复盘摘要
    if review_result:
        stats = review_result.get('stats', {})
        lines.append(f"## 📈 今日复盘")
        lines.append(f"")
        wr = stats.get('win_rate', 0)
        emoji = "🟢" if wr >= 50 else "🔴"
        lines.append(f"- 推荐数: **{stats.get('total', 0)}** 只")
        lines.append(f"- {emoji} 胜率: **{wr:.1f}%** ({stats.get('wins', 0)}/{stats.get('total', 0)})")
        lines.append(f"- 平均收益: **{stats.get('avg_return', 0):+.2f}%**")
        lines.append(f"- 止损: {stats.get('stop_loss_count', 0)}只 | 止盈: {stats.get('take_profit_count', 0)}只")
        lines.append(f"")

        # 表现最好/最差
        stocks = review_result.get('stocks', [])
        valid = [s for s in stocks if s.get('return_pct') is not None]
        if valid:
            best = max(valid, key=lambda x: x['return_pct'])
            worst = min(valid, key=lambda x: x['return_pct'])
            lines.append(f"- 🏆 最佳: {best['name']}({best['code']}) {best['return_pct']:+.2f}%")
            lines.append(f"- 💀 最差: {worst['name']}({worst['code']}) {worst['return_pct']:+.2f}%")
            lines.append(f"")
    else:
        lines.append(f"## 📈 今日复盘")
        lines.append(f"")
        lines.append(f"- ⚠️ 无复盘数据")
        lines.append(f"")

    # 优化摘要
    if optimize_result:
        tr = optimize_result.get('threshold_result', {})
        wa = optimize_result.get('weight_adjustments', {})
        lines.append(f"## 🔧 策略优化")
        lines.append(f"")

        if wa:
            lines.append(f"### 权重调整 ({len(wa)}项)")
            lines.append(f"")
            for fname, adj in sorted(wa.items(), key=lambda x: -abs(x[1]['delta'])):
                direction = "↑" if adj['delta'] > 0 else "↓"
                lines.append(f"- {direction} {fname}: {adj['old']} → {adj['new']} ({adj['delta']:+.1f})")
            lines.append(f"")
        else:
            lines.append(f"- 权重无显著变化")
            lines.append(f"")

        lines.append(f"### 阈值配置")
        lines.append(f"")
        lines.append(f"- 评分阈值: {tr.get('score_threshold', 'N/A')}")
        lines.append(f"- 止损线: {tr.get('stop_loss', 'N/A')}%")
        lines.append(f"- 整体胜率: {tr.get('overall_win_rate', 'N/A')}%")
        lines.append(f"- 样本数: {tr.get('sample_count', 0)}")
        lines.append(f"")
    else:
        lines.append(f"## 🔧 策略优化")
        lines.append(f"")
        lines.append(f"- ⚠️ 无优化数据")
        lines.append(f"")

    # 胜率趋势
    lines.append(f"## 📉 胜率趋势（最近10天）")
    lines.append(f"")
    lines.append(f"| 日期 | 胜率 | 平均收益 | 推荐数 |")
    lines.append(f"|------|------|----------|--------|")
    try:
        history_file = os.path.join(DATA_DIR, 'history.json')
        if os.path.exists(history_file):
            with open(history_file) as f:
                history = json.load(f)
            perf = history.get('performance', [])
            for p in perf[-10:]:
                s = p.get('stats', {})
                lines.append(f"| {p.get('date', 'N/A')} | {s.get('win_rate', 0):.1f}% | {s.get('avg_return', 0):+.2f}% | {s.get('total', 0)} |")
    except:
        lines.append(f"| N/A | N/A | N/A | N/A |")
    lines.append(f"")

    lines.append(f"---")
    lines.append(f"*自动生成 by JH量化调度系统*")

    report = '\n'.join(lines)
    report_file = os.path.join(REPORT_DIR, f'summary_{today_compact}.md')
    with open(report_file, 'w') as f:
        f.write(report)
    log.info(f"综合日报已保存: {report_file}")

    return report_file


def run_daily_pipeline():
    """
    主流程：复盘 → 优化 → 选股
    """
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')

    log.info(f"{'='*60}")
    log.info(f"JH量化每日调度启动 {today}")
    log.info(f"{'='*60}")

    # 1. 交易日检查
    if not is_trading_day(now):
        log.info(f"今日 {today} 非交易日（周末/节假日），跳过")
        return {'status': 'skip', 'reason': 'non_trading_day'}

    log.info(f"✅ 确认交易日: {today}")

    results = {
        'date': today,
        'status': 'running',
        'steps': {},
    }

    # 2. Step 1: 每日复盘
    log.info(f"\n{'='*40}")
    log.info(f"Step 1/3: 每日复盘")
    log.info(f"{'='*40}")
    review_success, review_output = run_script('daily_review.py')
    review_result = None
    if review_success:
        review_compact = today.replace('-', '')
        review_file = os.path.join(DATA_DIR, f'daily_review_{review_compact}.json')
        if os.path.exists(review_file):
            with open(review_file) as f:
                review_result = json.load(f)
    results['steps']['review'] = {
        'success': review_success,
        'output': review_output[:500] if review_output else '',
    }

    # 3. Step 2: 迭代优化
    log.info(f"\n{'='*40}")
    log.info(f"Step 2/3: 策略迭代优化")
    log.info(f"{'='*40}")
    optimize_success, optimize_output = run_script('auto_optimize.py', ['--days', '5'])
    optimize_result = None
    if optimize_success:
        try:
            with open(os.path.join(DATA_DIR, 'optimize_log.json')) as f:
                opt_log = json.load(f)
            if opt_log:
                optimize_result = opt_log[-1]
        except:
            pass
    results['steps']['optimize'] = {
        'success': optimize_success,
        'output': optimize_output[:500] if optimize_output else '',
    }

    # 4. Step 3: 运行选股（生成次日推荐）
    log.info(f"\n{'='*40}")
    log.info(f"Step 3/3: 选股（生成次日推荐）")
    log.info(f"{'='*40}")
    screener_success, screener_output = run_script('screener.py')
    results['steps']['screener'] = {
        'success': screener_success,
        'output': screener_output[:500] if screener_output else '',
    }

    # 4.5 同步 jh_summary.json（picks.html 读取）
    if screener_success:
        sync_success, sync_output = run_script('sync_summary.py')
        log.info(f"sync_summary: {'✅' if sync_success else '❌'} {sync_output[:200]}")

    # 5. 生成综合日报
    log.info(f"\n生成综合日报...")
    summary_file = generate_daily_summary(review_result, optimize_result)

    # 6. 汇总
    all_success = all(step['success'] for step in results['steps'].values())
    results['status'] = 'success' if all_success else 'partial'
    results['summary_file'] = summary_file

    log.info(f"\n{'='*60}")
    log.info(f"调度完成: {'✅ 全部成功' if all_success else '⚠️ 部分失败'}")
    for step_name, step in results['steps'].items():
        status = "✅" if step['success'] else "❌"
        log.info(f"  {status} {step_name}")
    log.info(f"{'='*60}")

    # 保存调度结果
    result_file = os.path.join(DATA_DIR, f'scheduler_result_{today.replace("-","")}.json')
    with open(result_file, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    return results


def setup_crontab():
    """
    设置 crontab 定时任务
    每个交易日 15:30 运行
    """
    cron_line = f"30 15 * * 1-5 cd {SCRIPT_DIR} && {sys.executable} daily_scheduler.py >> {LOG_FILE} 2>&1"
    cron_marker = "# JH量化每日调度"

    log.info(f"设置 crontab...")
    log.info(f"  定时任务: {cron_line}")

    # 读取现有 crontab
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ''
    except:
        existing = ''

    # 移除旧的同类任务
    lines = [l for l in existing.split('\n')
             if 'daily_scheduler.py' not in l and cron_marker not in l]

    # 添加新任务
    lines.append(cron_marker)
    lines.append(cron_line)
    lines.append('')

    new_cron = '\n'.join(lines)
    try:
        proc = subprocess.run(
            ['crontab', '-'], input=new_cron, text=True,
            capture_output=True
        )
        if proc.returncode == 0:
            log.info("✅ crontab 设置成功")
            return True
        else:
            log.error(f"❌ crontab 设置失败: {proc.stderr}")
            return False
    except Exception as e:
        log.error(f"❌ crontab 设置异常: {e}")
        return False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='JH量化每日调度')
    parser.add_argument('--setup-cron', action='store_true', help='设置crontab定时任务')
    parser.add_argument('--force', action='store_true', help='强制运行（忽略交易日检查）')
    parser.add_argument('--review-only', action='store_true', help='仅运行复盘')
    parser.add_argument('--optimize-only', action='store_true', help='仅运行优化')
    args = parser.parse_args()

    if args.setup_cron:
        setup_crontab()
        sys.exit(0)

    if args.review_only:
        log.info("仅运行复盘...")
        from daily_review import run_daily_review
        run_daily_review()
        sys.exit(0)

    if args.optimize_only:
        log.info("仅运行优化...")
        from auto_optimize import run_optimization
        run_optimization()
        sys.exit(0)

    # 检查是否强制运行
    if not args.force and not is_trading_day():
        log.info("非交易日，跳过。使用 --force 强制运行")
        sys.exit(0)

    run_daily_pipeline()
