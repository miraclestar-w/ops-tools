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

# 安装依赖
pip install requests flask psutil

# 运行服务器巡检
python3 server-health-checker.py

# 启动价差监控
python3 stablecoin-arbitrage-monitor.py
```

## 📦 部署建议

### 服务器巡检 (cron)
```bash
# 每小时检查一次
0 * * * * cd /root/ops-tools && python3 server-health-checker.py --json --quiet -o /var/log/server-health.json
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
