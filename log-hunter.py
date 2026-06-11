#!/usr/bin/env python3
"""
日志猎手 v1.0 — 运维日志分析利器
解析系统日志、应用日志、Docker日志，快速定位问题

支持: syslog, journald, nginx, docker, 任意文本日志
过滤: 时间范围、严重级别、关键词、正则
输出: 终端报告、JSON、HTML

用法:
    python3 log-hunter.py /var/log/syslog                      # 扫描系统日志
    python3 log-hunter.py /var/log/nginx/error.log --level ERROR # 只看错误
    python3 log-hunter.py /var/log/syslog --since "1 hour ago"  # 最近1小时
    python3 log-hunter.py --docker my-container                 # Docker容器日志
    python3 log-hunter.py /var/log/syslog --keyword "OOM|kill"  # 正则过滤
    python3 log-hunter.py /var/log/syslog --json                # JSON输出
    python3 log-hunter.py /var/log/syslog --html report.html    # HTML报告

By miraclestar-w | MIT License
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ── 日志格式模式 ──────────────────────────────────────────────

# 常见时间戳模式，按优先级排列
TIMESTAMP_PATTERNS = [
    # syslog: "Jun 11 18:30:22"
    (r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})', '%b %d %H:%M:%S'),
    # ISO/datetime: "2026-06-11T18:30:22" or "2026-06-11 18:30:22"
    (r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', '%Y-%m-%dT%H:%M:%S'),
    # nginx: "2026/06/11 18:30:22"
    (r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})', '%Y/%m/%d %H:%M:%S'),
    # 仅时间: "18:30:22"
    (r'(\d{2}:\d{2}:\d{2})', None),  # 需要特殊处理
]

# 严重级别关键词
LEVEL_KEYWORDS = {
    'CRITICAL': ['critical', 'crit', 'emerg', 'emergency', 'fatal', 'panic'],
    'ERROR':    ['error', 'err', 'failed', 'failure', 'exception', 'traceback', 'oom'],
    'WARNING':  ['warning', 'warn', 'deprecated', 'timeout', 'refused', 'denied'],
    'INFO':     ['info', 'notice', 'started', 'stopped', 'completed'],
    'DEBUG':    ['debug', 'trace', 'verbose'],
}


def parse_timestamp(line):
    """尝试从日志行中提取时间戳"""
    for pattern, fmt in TIMESTAMP_PATTERNS:
        match = re.search(pattern, line)
        if match:
            ts_str = match.group(1)
            if fmt is None:
                # 仅时间，补充今天的日期
                today = datetime.now().strftime('%Y-%m-%d')
                ts_str = f"{today} {ts_str}"
                fmt = '%Y-%m-%d %H:%M:%S'
            try:
                # 尝试带年份的格式
                if '%Y' in fmt:
                    return datetime.strptime(ts_str, fmt)
                else:
                    # syslog格式不带年份，用当前年
                    dt = datetime.strptime(ts_str, fmt.replace('%Y', str(datetime.now().year)))
                    return dt.replace(year=datetime.now().year)
            except ValueError:
                continue
    return None


def detect_level(line):
    """从日志行中检测严重级别"""
    lower = line.lower()
    for level, keywords in LEVEL_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return level
    return 'UNKNOWN'


class LogHunter:
    """日志分析引擎"""

    def __init__(self, since=None, until=None, level=None, keyword=None,
                 top_n=20, quiet=False):
        self.since = since          # 起始时间
        self.until = until          # 结束时间
        self.level = level          # 过滤级别
        self.keyword = keyword      # 关键词/正则
        self.top_n = top_n          # Top N 统计
        self.quiet = quiet          # 静默模式

        # 分析结果
        self.total_lines = 0
        self.matched_lines = 0
        self.parsed_lines = []
        self.level_counts = Counter()
        self.hourly_counts = Counter()
        self.source_counts = Counter()
        self.error_samples = []     # 错误样本
        self.keyword_matches = Counter()

    def _should_include(self, line):
        """判断是否包含该行"""
        # 级别过滤
        if self.level:
            line_level = detect_level(line)
            if line_level != self.level:
                return False

        # 关键词过滤
        if self.keyword:
            try:
                if not re.search(self.keyword, line, re.IGNORECASE):
                    return False
            except re.error:
                # 正则无效，退回到简单匹配
                if self.keyword.lower() not in line.lower():
                    return False

        return True

    def scan_file(self, filepath):
        """扫描日志文件"""
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"❌ 文件不存在: {filepath}", file=sys.stderr)
            return False

        if self.level == 'DOCKER':
            return self._scan_docker(filepath.name)

        try:
            with open(filepath, 'r', errors='replace') as f:
                for line in f:
                    self.total_lines += 1
                    line = line.rstrip('\n')

                    if not self._should_include(line):
                        continue

                    self.matched_lines += 1
                    self._analyze_line(line, source=str(filepath.name))
        except Exception as e:
            print(f"❌ 读取失败: {e}", file=sys.stderr)
            return False

        return True

    def _scan_docker(self, container_name):
        """扫描Docker容器日志"""
        try:
            r = subprocess.run(
                ['docker', 'logs', '--tail', '5000', container_name],
                capture_output=True, text=True, timeout=30
            )
            output = r.stdout + r.stderr
            for line in output.splitlines():
                self.total_lines += 1
                if not self._should_include(line):
                    continue
                self.matched_lines += 1
                self._analyze_line(line, source=f"docker:{container_name}")
            return True
        except FileNotFoundError:
            print("❌ Docker未安装", file=sys.stderr)
            return False
        except subprocess.TimeoutExpired:
            print("❌ Docker日志获取超时", file=sys.stderr)
            return False

    def _scan_journal(self, unit=None, lines=5000):
        """扫描journald日志"""
        cmd = ['journalctl', '--no-pager', '-n', str(lines)]
        if unit:
            cmd.extend(['-u', unit])

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                self.total_lines += 1
                if not self._should_include(line):
                    continue
                self.matched_lines += 1
                self._analyze_line(line, source=f"journald:{unit or 'all'}")
            return True
        except FileNotFoundError:
            print("❌ journalctl未安装", file=sys.stderr)
            return False

    def _analyze_line(self, line, source="unknown"):
        """分析单行日志"""
        ts = parse_timestamp(line)
        level = detect_level(line)

        # 时间过滤
        if self.since and ts and ts < self.since:
            return
        if self.until and ts and ts > self.until:
            return

        self.level_counts[level] += 1
        if ts:
            self.hourly_counts[ts.strftime('%Y-%m-%d %H:00')] += 1
        self.source_counts[source] += 1

        # 保留错误样本
        if level in ('CRITICAL', 'ERROR') and len(self.error_samples) < self.top_n:
            self.error_samples.append({
                'time': ts.strftime('%H:%M:%S') if ts else '??:??:??',
                'level': level,
                'source': source,
                'line': line[:300],  # 截断过长的行
            })

        # 关键词匹配统计
        if self.keyword:
            matches = re.findall(self.keyword, line, re.IGNORECASE)
            for m in matches:
                self.keyword_matches[m] += 1

        self.parsed_lines.append({
            'time': ts,
            'level': level,
            'source': source,
            'line': line,
        })

    def generate_report(self):
        """生成终端报告"""
        lines = []
        lines.append("")
        lines.append("=" * 60)
        lines.append("  🔍 日志猎手 — 分析报告")
        lines.append("=" * 60)
        lines.append("")

        # 概览
        lines.append(f"  📊 扫描行数: {self.total_lines}")
        lines.append(f"  📌 匹配行数: {self.matched_lines}")
        if self.total_lines > 0:
            pct = self.matched_lines / self.total_lines * 100
            lines.append(f"  📈 匹配比例: {pct:.1f}%")
        lines.append("")

        # 严重级别分布
        if self.level_counts:
            lines.append("  🚦 严重级别分布:")
            lines.append("  " + "-" * 40)
            max_count = max(self.level_counts.values()) if self.level_counts else 1
            for level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'UNKNOWN']:
                count = self.level_counts.get(level, 0)
                if count > 0:
                    bar_len = int(count / max_count * 20)
                    bar = '█' * bar_len
                    icon = {'CRITICAL': '🔴', 'ERROR': '🔴', 'WARNING': '🟡',
                            'INFO': '🟢', 'DEBUG': '🔵', 'UNKNOWN': '⚪'}.get(level, '⚪')
                    lines.append(f"  {icon} {level:10s} {count:6d}  {bar}")
            lines.append("")

        # 时间分布（最近几小时）
        if self.hourly_counts:
            lines.append("  ⏰ 时间分布 (按小时):")
            lines.append("  " + "-" * 40)
            sorted_hours = sorted(self.hourly_counts.items())[-12:]  # 最近12小时
            max_h = max(v for _, v in sorted_hours) if sorted_hours else 1
            for hour, count in sorted_hours:
                bar_len = int(count / max_h * 25)
                bar = '▓' * bar_len
                lines.append(f"  {hour}  {count:5d}  {bar}")
            lines.append("")

        # 来源统计
        if self.source_counts and len(self.source_counts) > 1:
            lines.append("  📁 来源分布:")
            lines.append("  " + "-" * 40)
            for src, count in self.source_counts.most_common(10):
                lines.append(f"  {src:30s} {count:6d}")
            lines.append("")

        # 错误样本
        if self.error_samples:
            lines.append(f"  🐛 错误样本 (最多{self.top_n}条):")
            lines.append("  " + "-" * 40)
            for i, sample in enumerate(self.error_samples[:self.top_n], 1):
                lines.append(f"  [{sample['time']}] [{sample['level']}] {sample['source']}")
                # 截断显示
                text = sample['line']
                if len(text) > 120:
                    text = text[:117] + "..."
                lines.append(f"    {text}")
                lines.append("")
            lines.append("")

        # 关键词匹配统计
        if self.keyword_matches:
            lines.append(f"  🔑 关键词匹配统计:")
            lines.append("  " + "-" * 40)
            for kw, count in self.keyword_matches.most_common(self.top_n):
                lines.append(f"  {kw:30s} {count:6d}")
            lines.append("")

        lines.append("=" * 60)
        return '\n'.join(lines)

    def to_json(self):
        """导出JSON"""
        return {
            'summary': {
                'total_lines': self.total_lines,
                'matched_lines': self.matched_lines,
                'scan_time': datetime.now().isoformat(),
            },
            'level_distribution': dict(self.level_counts),
            'hourly_distribution': dict(self.hourly_counts),
            'source_distribution': dict(self.source_counts),
            'error_samples': self.error_samples[:self.top_n],
            'keyword_matches': dict(self.keyword_matches.most_common(self.top_n)),
        }

    def to_html(self, output_path):
        """生成HTML报告"""
        data = self.to_json()
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日志猎手报告 — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, 'SF Pro', 'PingFang SC', sans-serif;
       background: #0f172a; color: #e2e8f0; padding: 20px; }}
.container {{ max-width: 1000px; margin: 0 auto; }}
h1 {{ font-size: 28px; margin-bottom: 20px; text-align: center; color: #38bdf8; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 16px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 20px;
              text-align: center; border: 1px solid #334155; }}
.stat-card .number {{ font-size: 48px; font-weight: 700; color: #38bdf8; }}
.stat-card .label {{ font-size: 14px; color: #94a3b8; margin-top: 4px; }}
.section {{ background: #1e293b; border-radius: 12px; padding: 20px;
            margin-bottom: 16px; border: 1px solid #334155; }}
.section h2 {{ font-size: 18px; margin-bottom: 12px; color: #f1f5f9; }}
.bar-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
.bar-label {{ width: 100px; font-size: 13px; color: #94a3b8; }}
.bar-container {{ flex: 1; height: 24px; background: #334155; border-radius: 4px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; display: flex; align-items: center;
             padding-left: 8px; font-size: 12px; font-weight: 600; }}
.bar-fill.critical {{ background: #ef4444; }}
.bar-fill.error {{ background: #f97316; }}
.bar-fill.warning {{ background: #eab308; color: #000; }}
.bar-fill.info {{ background: #22c55e; }}
.bar-fill.debug {{ background: #3b82f6; }}
.bar-fill.unknown {{ background: #64748b; }}
.error-sample {{ background: #1a1a2e; border-left: 3px solid #ef4444;
                 padding: 12px; margin-bottom: 8px; border-radius: 4px;
                 font-family: 'SF Mono', monospace; font-size: 13px;
                 word-break: break-all; }}
.error-sample .meta {{ color: #94a3b8; font-size: 12px; margin-bottom: 4px; }}
.footer {{ text-align: center; color: #64748b; font-size: 13px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
<h1>🔍 日志猎手报告</h1>
<div class="stats">
  <div class="stat-card">
    <div class="number">{data['summary']['total_lines']:,}</div>
    <div class="label">扫描行数</div>
  </div>
  <div class="stat-card">
    <div class="number">{data['summary']['matched_lines']:,}</div>
    <div class="label">匹配行数</div>
  </div>
  <div class="stat-card">
    <div class="number">{len(data['level_distribution'])}</div>
    <div class="label">日志级别</div>
  </div>
</div>
"""
        # 级别分布
        if data['level_distribution']:
            max_count = max(data['level_distribution'].values()) or 1
            html += '<div class="section"><h2>🚦 严重级别分布</h2>'
            level_colors = {
                'CRITICAL': 'critical', 'ERROR': 'error', 'WARNING': 'warning',
                'INFO': 'info', 'DEBUG': 'debug', 'UNKNOWN': 'unknown'
            }
            for level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'UNKNOWN']:
                count = data['level_distribution'].get(level, 0)
                if count > 0:
                    pct = count / max_count * 100
                    color = level_colors.get(level, 'unknown')
                    html += f'<div class="bar-row"><span class="bar-label">{level}</span>'
                    html += f'<div class="bar-container"><div class="bar-fill {color}" '
                    html += f'style="width:{pct}%">{count:,}</div></div></div>'
            html += '</div>'

        # 错误样本
        if data['error_samples']:
            html += f'<div class="section"><h2>🐛 错误样本 (Top {len(data["error_samples"])})</h2>'
            for s in data['error_samples']:
                html += f'<div class="error-sample">'
                html += f'<div class="meta">[{s["time"]}] [{s["level"]}] {s["source"]}</div>'
                html += f'{_escape_html(s["line"][:500])}</div>'
            html += '</div>'

        html += f"""
<div class="footer">Generated by 日志猎手 v1.0 · {data['summary']['scan_time']}</div>
</div>
</body></html>"""

        with open(output_path, 'w') as f:
            f.write(html)
        return output_path


def _escape_html(text):
    """HTML转义"""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def parse_time_arg(time_str):
    """解析时间参数，支持 '1 hour ago', '2026-06-11 18:00', 'yesterday' 等"""
    time_str = time_str.strip().lower()
    now = datetime.now()

    # 相对时间: "1 hour ago", "30 min ago"
    m = re.match(r'(\d+)\s*(second|minute|hour|day|week)s?\s*(ago)?', time_str)
    if m:
        n, unit, _ = int(m.group(1)), m.group(2), m.group(3)
        deltas = {
            'second': timedelta(seconds=n),
            'minute': timedelta(minutes=n),
            'hour': timedelta(hours=n),
            'day': timedelta(days=n),
            'week': timedelta(weeks=n),
        }
        return now - deltas[unit]

    # yesterday
    if time_str == 'yesterday':
        return now - timedelta(days=1)

    # ISO格式
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                '%Y/%m/%d %H:%M:%S', '%H:%M:%S'):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue

    # 仅日期补时间
    try:
        return datetime.strptime(f"{time_str} 00:00:00", '%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass

    print(f"⚠️  无法解析时间: '{time_str}'，忽略", file=sys.stderr)
    return None


def main():
    parser = argparse.ArgumentParser(
        description='🔍 日志猎手 — 运维日志分析利器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s /var/log/syslog                         # 扫描系统日志
  %(prog)s /var/log/syslog --level ERROR            # 只看错误
  %(prog)s /var/log/syslog --since "1 hour ago"     # 最近1小时
  %(prog)s /var/log/nginx/error.log --keyword "502" # 搜502
  %(prog)s --docker my-container                    # Docker容器日志
  %(prog)s --journal -u nginx                       # journald + nginx单元
  %(prog)s /var/log/syslog --json                   # JSON输出
  %(prog)s /var/log/syslog --html report.html       # HTML报告
"""
    )
    parser.add_argument('files', nargs='*', help='日志文件路径')
    parser.add_argument('--since', help='起始时间 (如 "1 hour ago", "2026-06-11 18:00")')
    parser.add_argument('--until', help='结束时间')
    parser.add_argument('--level', choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'DOCKER'],
                        help='过滤严重级别')
    parser.add_argument('--keyword', '-k', help='关键词或正则表达式')
    parser.add_argument('--top', type=int, default=20, help='Top N (默认20)')
    parser.add_argument('--json', action='store_true', help='JSON输出')
    parser.add_argument('--html', metavar='FILE', help='生成HTML报告')
    parser.add_argument('--docker', metavar='CONTAINER', help='扫描Docker容器日志')
    parser.add_argument('--journal', action='store_true', help='扫描journald日志')
    parser.add_argument('-u', '--unit', help='journald单元 (配合 --journal)')
    parser.add_argument('-q', '--quiet', action='store_true', help='静默模式')
    parser.add_argument('-n', '--lines', type=int, default=5000, help='journald/Docker行数限制')

    args = parser.parse_args()

    if not args.files and not args.docker and not args.journal:
        parser.print_help()
        sys.exit(1)

    hunter = LogHunter(
        since=parse_time_arg(args.since) if args.since else None,
        until=parse_time_arg(args.until) if args.until else None,
        level=args.level,
        keyword=args.keyword,
        top_n=args.top,
        quiet=args.quiet,
    )

    # 扫描文件
    for f in args.files:
        hunter.scan_file(f)

    # Docker日志
    if args.docker:
        hunter.level = 'DOCKER'
        hunter._scan_docker(args.docker)

    # journald
    if args.journal:
        hunter._scan_journal(unit=args.unit, lines=args.lines)

    # 输出结果
    if args.json:
        print(json.dumps(hunter.to_json(), indent=2, ensure_ascii=False))
    elif args.html:
        path = hunter.to_html(args.html)
        if not args.quiet:
            print(f"✅ HTML报告已生成: {path}")
    else:
        if not args.quiet or hunter.matched_lines > 0:
            print(hunter.generate_report())

    # 退出码: 有ERROR/CRITICAL返回1
    if hunter.level_counts.get('CRITICAL', 0) > 0 or hunter.level_counts.get('ERROR', 0) > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
