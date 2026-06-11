# 🥚 铁蛋运维工具集

> 运维人必备的自动化工具合集 | By [miraclestar-w](https://github.com/miraclestar-w)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)

## 🛠️ 工具列表

### 🔍 [server-health-checker.py](server-health-checker.py)
一键检查服务器所有关键指标

**检查项：** CPU、内存、磁盘、负载、Docker容器、系统服务、僵尸进程

```bash
python3 server-health-checker.py                    # 文本报告
python3 server-health-checker.py --json             # JSON输出
python3 server-health-checker.py --json --quiet      # 静默+JSON（适合cron）
```

### 🕵️ [log-hunter.py](log-hunter.py)
运维日志分析利器 — 快速定位系统问题

**功能：** 多格式日志解析(syslog/journald/nginx/docker)、级别过滤、关键词正则、时间范围、HTML报告

```bash
python3 log-hunter.py /var/log/syslog                      # 扫描系统日志
python3 log-hunter.py /var/log/syslog --level ERROR        # 只看错误
python3 log-hunter.py /var/log/syslog --since "1 hour ago" # 最近1小时
python3 log-hunter.py /var/log/syslog -k "OOM|kill"        # 正则关键词
python3 log-hunter.py --docker my-container                # Docker容器日志
python3 log-hunter.py --journal -u nginx                   # journald日志
python3 log-hunter.py /var/log/syslog --json               # JSON输出
python3 log-hunter.py /var/log/syslog --html report.html   # HTML报告
```

### 💰 [stablecoin-arbitrage-monitor.py](stablecoin-arbitrage-monitor.py)
稳定币价差监控 + Web看板

**功能：** Binance/OKX/Bybit价格监控、价差计算、告警推送、Web仪表盘

```bash
pip install requests flask
python3 stablecoin-arbitrage-monitor.py                        # 默认配置
python3 stablecoin-arbitrage-monitor.py --threshold 0.05      # 0.05%阈值
python3 stablecoin-arbitrage-monitor.py --port 8080            # 自定义端口
python3 stablecoin-arbitrage-monitor.py --csv-history          # CSV历史记录
```

### 📊 [stablecoin-arbitrage-dashboard.html](stablecoin-arbitrage-dashboard.html)
独立Web看板 — 直接在浏览器打开，可视化价差数据

## 🚀 快速开始

```bash
# 克隆仓库
git clone https://github.com/miraclestar-w/ops-tools.git
cd ops-tools

# 安装依赖（日志猎手零依赖，其他工具需要）
pip install requests flask psutil

# 运行服务器巡检
python3 server-health-checker.py

# 分析系统日志
python3 log-hunter.py /var/log/syslog --level ERROR

# 启动价差监控
python3 stablecoin-arbitrage-monitor.py
```

## 📦 部署建议

### 服务器巡检 (cron)
```bash
# 每小时检查一次
0 * * * * cd /root/ops-tools && python3 server-health-checker.py --json --quiet -o /var/log/server-health.json
```

### 日志分析 (定时审计)
```bash
# 每天9点生成前一天的错误报告
0 9 * * * cd /root/ops-tools && python3 log-hunter.py /var/log/syslog --level ERROR --since "24 hours ago" --html /var/log/daily-report-$(date +\%F).html
```

### 价差监控 (systemd)
```ini
[Unit]
Description=Stablecoin Arbitrage Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/ops-tools
ExecStart=/usr/bin/python3 stablecoin-arbitrage-monitor.py --threshold 0.1
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 📄 License

MIT License - 自由使用、修改、分发

---

*Built with 🥚 by Hermes Agent*
