#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
稳定币价差监控工具 (Stablecoin Arbitrage Monitor)
=================================================

功能：
  1) 监控 Binance、OKX、Bybit 等交易所的 USDT/USDC 价格
  2) 计算跨交易所价差（百分比）
  3) 当价差超过阈值时通过多种方式告警（终端/日志/可选 Webhook）
  4) 记录历史价差数据到 CSV 文件
  5) 内置轻量 Web 看板（Flask），显示实时价差和历史图表

依赖安装：
  pip install requests flask

用法：
  python stablecoin-arbitrage-monitor.py                    # 使用默认配置运行
  python stablecoin-arbitrage-monitor.py --threshold 0.05   # 设置告警阈值为 0.05%
  python stablecoin-arbitrage-monitor.py --port 8080        # Web 看板端口
  python stablecoin-arbitrage-monitor.py --csv-history      # 同时写入 CSV 历史文件

作者：Hermes Agent
版本：1.0.0
日期：2026-05-28
"""

import os
import sys
import csv
import json
import time
import signal
import logging
import argparse
import hashlib
from datetime import datetime, timezone
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from threading import Thread, Lock

try:
    import requests
except ImportError:
    print("❌ 缺少 requests 库，请先安装：pip install requests")
    sys.exit(1)

# ============================================================
#  全局配置
# ============================================================

# 默认告警阈值（百分比），超过此值触发告警
DEFAULT_ALERT_THRESHOLD_PCT = 0.03

# 数据刷新间隔（秒）
DEFAULT_REFRESH_INTERVAL = 10

# Web 看板端口
DEFAULT_WEB_PORT = 8080

# 历史数据保留条数（内存中）
MAX_HISTORY_RECORDS = 5000

# HTTP 请求超时（秒）
REQUEST_TIMEOUT = 8

# 用户代理，部分交易所需要
USER_AGENT = "StablecoinArbitrageMonitor/1.0"

# ============================================================
#  日志配置
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("StablecoinMonitor")


# ============================================================
#  数据模型
# ============================================================

@dataclass
class PriceTick:
    """单次价格快照"""
    exchange: str          # 交易所名称
    symbol: str            # 交易对，如 "USDT/USDC"
    bid: float             # 买一价（USDC per USDT）
    ask: float             # 卖一价（USDC per USDT）
    mid: float             # 中间价
    timestamp: float       # 时间戳（Unix）
    source: str = ""       # 数据来源说明

    def __post_init__(self):
        if self.mid == 0:
            self.mid = (self.bid + self.ask) / 2 if self.bid and self.ask else 0


@dataclass
class SpreadRecord:
    """价差记录"""
    timestamp: str           # ISO 格式时间
    exchange_a: str          # 交易所 A
    exchange_b: str          # 交易所 B
    symbol: str              # 交易对
    bid_a: float             # A 的买价
    ask_a: float             # A 的卖价
    bid_b: float             # B 的买价
    ask_b: float             # B 的卖价
    mid_a: float             # A 的中间价
    mid_b: float             # B 的中间价
    spread_pct: float        # 价差百分比
    direction: str           # 价差方向


# ============================================================
#  交易所数据获取器（每个交易所一个类）
# ============================================================

class BinanceFetcher:
    """Binance 交易所价格获取器

    Binance 使用 REST API 获取 ticker 价格。
    交易对：USDCUSDT（表示 1 USDT = X USDC）

    API 文档：https://docs/binance.com/#symbol-ticker
    """

    NAME = "Binance"
    BASE_URL = "https://api.binance.com"

    @staticmethod
    def fetch_prices(symbol: str = "USDCUSDT") -> Optional[PriceTick]:
        """获取 Binance 的 bid/ask 价格

        参数：
            symbol: Binance 格式的交易对

        返回：
            PriceTick 对象，失败返回 None
        """
        try:
            # 获取 orderbook 顶层层级（depth=1），同时获得 bid 和 ask
            url = f"{BinanceFetcher.BASE_URL}/api/v3/ticker/bookTicker?symbol={symbol}"
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            bid = float(data["bidPrice"])
            ask = float(data["askPrice"])
            mid = (bid + ask) / 2

            return PriceTick(
                exchange="Binance",
                symbol="USDT/USDC",
                bid=bid,
                ask=ask,
                mid=mid,
                timestamp=time.time(),
                source=f"bookTicker({symbol})",
            )
        except Exception as e:
            logger.warning(f"[Binance] 获取价格失败: {e}")
            return None


class OKXFetcher:
    """OKX 交易所价格获取器

    OKX 使用 REST API 获取 ticker 价格。
    交易对：USDC-USDT（表示 1 USDT = X USDC）

    API 文档：https://www.okx.com/docs-v5/en/#order-book-trading-tickers-ticker
    """

    NAME = "OKX"
    BASE_URL = "https://www.okx.com"

    @staticmethod
    def fetch_prices(symbol: str = "USDC-USDT") -> Optional[PriceTick]:
        """获取 OKX 的 bid/ask 价格

        参数：
            symbol: OKX 格式的交易对

        返回：
            PriceTick 对象，失败返回 None
        """
        try:
            url = f"{OKXFetcher.BASE_URL}/api/v5/market/ticker?instId={symbol}"
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            result = resp.json()

            if result.get("code") != "0" or not result.get("data"):
                logger.warning(f"[OKX] API 返回异常: {result.get('msg', 'unknown')}")
                return None

            data = result["data"][0]
            bid = float(data["bidPx"])
            ask = float(data["askPx"])
            mid = (bid + ask) / 2

            return PriceTick(
                exchange="OKX",
                symbol="USDT/USDC",
                bid=bid,
                ask=ask,
                mid=mid,
                timestamp=time.time(),
                source=f"ticker({symbol})",
            )
        except Exception as e:
            logger.warning(f"[OKX] 获取价格失败: {e}")
            return None


class BybitFetcher:
    """Bybit 交易所价格获取器

    Bybit 使用 REST API 获取 ticker 价格。
    交易对：USDCUSDT（表示 1 USDT = X USDC）

    API 文档：https://bybit-exchange.github.io/docs/v5/market/ticker
    """

    NAME = "Bybit"
    BASE_URL = "https://api.bybit.com"

    @staticmethod
    def fetch_prices(symbol: str = "USDCUSDT") -> Optional[PriceTick]:
        """获取 Bybit 的 bid/ask 价格

        参数：
            symbol: Bybit 格式的交易对

        返回：
            PriceTick 对象，失败返回 None
        """
        try:
            url = f"{BybitFetcher.BASE_URL}/v5/market/tickers?category=spot&symbol={symbol}"
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            result = resp.json()

            if result.get("retCode") != 0 or not result.get("result", {}).get("list"):
                logger.warning(f"[Bybit] API 返回异常: {result.get('retMsg', 'unknown')}")
                return None

            data = result["result"]["list"][0]
            bid = float(data["bid1Price"])
            ask = float(data["ask1Price"])
            mid = (bid + ask) / 2

            return PriceTick(
                exchange="Bybit",
                symbol="USDT/USDC",
                bid=bid,
                ask=ask,
                mid=mid,
                timestamp=time.time(),
                source=f"tickers({symbol})",
            )
        except Exception as e:
            logger.warning(f"[Bybit] 获取价格失败: {e}")
            return None


class GateIOFetcher:
    """Gate.io 交易所价格获取器

    Gate.io 使用 REST API 获取 ticker 价格。
    交易对：USDC_USDT（表示 1 USDT = X USDC）

    API 文档：https://www.gate.io/docs/developers/spot-api/en/#ticker
    """

    NAME = "Gate.io"
    BASE_URL = "https://api.gateio.ws"

    @staticmethod
    def fetch_prices(symbol: str = "USDC_USDT") -> Optional[PriceTick]:
        """获取 Gate.io 的 bid/ask 价格

        参数：
            symbol: Gate.io 格式的交易对

        返回：
            PriceTick 对象，失败返回 None
        """
        try:
            url = f"{GateIOFetcher.BASE_URL}/api/v4/spot/tickers?currency_pair={symbol}"
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data_list = resp.json()

            if not data_list:
                logger.warning("[Gate.io] API 返回空数据")
                return None

            data = data_list[0]
            bid = float(data["highest_bid"])
            ask = float(data["lowest_ask"])
            mid = (bid + ask) / 2

            return PriceTick(
                exchange="Gate.io",
                symbol="USDT/USDC",
                bid=bid,
                ask=ask,
                mid=mid,
                timestamp=time.time(),
                source=f"tickers({symbol})",
            )
        except Exception as e:
            logger.warning(f"[Gate.io] 获取价格失败: {e}")
            return None


class HTXFetcher:
    """火币 (HTX) 交易所价格获取器

    火币使用 REST API 获取 ticker 价格。
    交易对：usdcusdt（表示 1 USDT = X USDC）

    API 文档：https://www.htx.com/en-us/opend/newApiPages/20000155/
    """

    NAME = "HTX"
    BASE_URL = "https://api.huobi.pro"

    @staticmethod
    def fetch_prices(symbol: str = "usdcusdt") -> Optional[PriceTick]:
        """获取火币的 bid/ask 价格

        参数：
            symbol: HTX 格式的交易对（全小写）

        返回：
            PriceTick 对象，失败返回 None
        """
        try:
            url = f"{HTXFetcher.BASE_URL}/market/detail/merged?symbol={symbol}"
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            result = resp.json()

            if result.get("status") != "ok" or not result.get("tick"):
                logger.warning(f"[HTX] API 返回异常: {result.get('err-msg', 'unknown')}")
                return None

            tick = result["tick"]
            # HTX 返回 bid[0]=price, bid[1]=amount
            bid = float(tick["bid"][0])
            ask = float(tick["ask"][0])
            mid = (bid + ask) / 2

            return PriceTick(
                exchange="HTX",
                symbol="USDT/USDC",
                bid=bid,
                ask=ask,
                mid=mid,
                timestamp=time.time(),
                source=f"detail({symbol})",
            )
        except Exception as e:
            logger.warning(f"[HTX] 获取价格失败: {e}")
            return None


# ============================================================
#  价差计算引擎
# ============================================================

class SpreadCalculator:
    """跨交易所价差计算器

    核心逻辑：
      1. 从各交易所获取 USDT/USDC 价格
      2. 对比所有交易所两两之间的中间价差
      3. 价差 = (高价 - 低价) / 低价 × 100%
      4. 理论上稳定币价差极小（<0.01%），出现显著价差即为套利机会
    """

    def __init__(self, fetchers: List = None):
        """初始化计算器

        参数：
            fetchers: 交易所获取器类的列表，默认包含所有支持的交易所
        """
        if fetchers is None:
            self.fetchers = [
                BinanceFetcher,
                OKXFetcher,
                BybitFetcher,
                GateIOFetcher,
                HTXFetcher,
            ]
        else:
            self.fetchers = fetchers

        # 最新价格缓存 {exchange_name: PriceTick}
        self.latest_prices: Dict[str, PriceTick] = {}
        self._lock = Lock()

    def fetch_all(self) -> Dict[str, PriceTick]:
        """并行获取所有交易所的最新价格

        返回：
            {交易所名称: PriceTick} 字典
        """
        results = {}
        for fetcher_cls in self.fetchers:
            try:
                tick = fetcher_cls.fetch_prices()
                if tick:
                    results[tick.exchange] = tick
            except Exception as e:
                logger.error(f"[{fetcher_cls.NAME}] 获取异常: {e}")

        with self._lock:
            self.latest_prices = results
        return results

    def calculate_all_spreads(self) -> List[SpreadRecord]:
        """计算所有交易所两两之间的价差

        返回：
            SpreadRecord 列表，按价差绝对值降序排列
        """
        with self._lock:
            prices = dict(self.latest_prices)

        if len(prices) < 2:
            return []

        exchanges = sorted(prices.keys())
        records = []

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for i in range(len(exchanges)):
            for j in range(i + 1, len(exchanges)):
                a_name = exchanges[i]
                b_name = exchanges[j]
                a = prices[a_name]
                b = prices[b_name]

                # 价差百分比计算
                # 以较低价为基准计算百分比价差
                if a.mid > 0 and b.mid > 0:
                    if a.mid >= b.mid:
                        spread_pct = (a.mid - b.mid) / b.mid * 100
                        direction = f"{b_name} → {a_name}"
                    else:
                        spread_pct = (b.mid - a.mid) / a.mid * 100
                        direction = f"{a_name} → {b_name}"

                    records.append(SpreadRecord(
                        timestamp=now_str,
                        exchange_a=a_name,
                        exchange_b=b_name,
                        symbol="USDT/USDC",
                        bid_a=round(a.bid, 8),
                        ask_a=round(a.ask, 8),
                        bid_b=round(b.bid, 8),
                        ask_b=round(b.ask, 8),
                        mid_a=round(a.mid, 8),
                        mid_b=round(b.mid, 8),
                        spread_pct=round(spread_pct, 6),
                        direction=direction,
                    ))

        # 按价差绝对值降序排列
        records.sort(key=lambda r: r.spread_pct, reverse=True)
        return records

    def get_prices_as_dicts(self) -> List[dict]:
        """获取最新价格的字典列表（供 Web 看板使用）"""
        with self._lock:
            return [asdict(tick) for tick in self.latest_prices.values()]

    def get_spreads_as_dicts(self) -> List[dict]:
        """获取价差记录的字典列表（供 Web 看板使用）"""
        records = self.calculate_all_spreads()
        return [asdict(r) for r in records]


# ============================================================
#  告警管理器
# ============================================================

class AlertManager:
    """告警管理器

    支持多种告警方式：
      1. 终端输出（默认）
      2. 写入日志文件
      3. 可选的 Webhook 回调（如企业微信、钉钉、Slack 等）

    告警策略：
      - 当任意价差超过阈值时触发告警
      - 同一对交易所的告警有冷却期（默认 300 秒），防止重复告警
    """

    def __init__(self, threshold_pct: float = DEFAULT_ALERT_THRESHOLD_PCT,
                 webhook_url: str = None, cooldown: int = 300):
        """
        参数：
            threshold_pct: 告警阈值（百分比）
            webhook_url: Webhook 回调 URL（可选）
            cooldown: 同一对交易所的告警冷却期（秒）
        """
        self.threshold_pct = threshold_pct
        self.webhook_url = webhook_url
        self.cooldown = cooldown

        # 告警历史 {pair_key: last_alert_timestamp}
        self._alert_history: Dict[str, float] = {}

        # 统计
        self.total_alerts = 0

        # 告警历史记录（内存中保留最近 200 条）
        self.recent_alerts: deque = deque(maxlen=200)

    def check_and_alert(self, spreads: List[SpreadRecord]) -> List[SpreadRecord]:
        """检查价差并触发告警

        参数：
            spreads: 价差记录列表

        返回：
            实际触发告警的价差记录列表
        """
        triggered = []

        for spread in spreads:
            if spread.spread_pct < self.threshold_pct:
                continue  # 价差未超过阈值

            # 冷却期检查
            pair_key = f"{spread.exchange_a}:{spread.exchange_b}"
            now = time.time()
            last_alert = self._alert_history.get(pair_key, 0)

            if now - last_alert < self.cooldown:
                continue  # 还在冷却期，跳过

            # 触发告警
            self._alert_history[pair_key] = now
            self.total_alerts += 1

            alert_msg = self._format_alert(spread)
            self.recent_alerts.append({
                "time": spread.timestamp,
                "message": alert_msg,
                "spread_pct": spread.spread_pct,
                "pair": pair_key,
            })

            # 方式 1：终端输出
            self._alert_terminal(spread, alert_msg)

            # 方式 2：Webhook 回调（如果配置了）
            if self.webhook_url:
                self._alert_webhook(spread, alert_msg)

            triggered.append(spread)

        return triggered

    def _format_alert(self, spread: SpreadRecord) -> str:
        """格式化告警消息"""
        return (
            f"🚨 价差告警 | {spread.symbol}\n"
            f"   价差: {spread.spread_pct:.4f}% (阈值: {self.threshold_pct:.2f}%)\n"
            f"   方向: {spread.direction}\n"
            f"   {spread.exchange_a}: bid={spread.bid_a} ask={spread.ask_a} mid={spread.mid_a}\n"
            f"   {spread.exchange_b}: bid={spread.bid_b} ask={spread.ask_b} mid={spread.mid_b}\n"
            f"   时间: {spread.timestamp}"
        )

    def _alert_terminal(self, spread: SpreadRecord, msg: str):
        """终端告警输出"""
        # 使用醒目的格式输出
        border = "=" * 60
        logger.warning(f"\n{border}\n{msg}\n{border}")

    def _alert_webhook(self, spread: SpreadRecord, msg: str):
        """通过 Webhook 发送告警

        支持以下格式（根据 URL 自动判断）：
          - 企业微信机器人：URL 包含 qyapi.weixin.qq.com
          - 钉钉机器人：URL 包含 oapi.dingtalk.com
          - Slack / Discord / 通用格式：其他 URL

        参数：
            spread: 价差记录
            msg: 告警消息文本
        """
        try:
            if "qyapi.weixin.qq.com" in self.webhook_url:
                # 企业微信机器人格式
                payload = {
                    "msgtype": "text",
                    "text": {"content": msg},
                }
            elif "oapi.dingtalk.com" in self.webhook_url:
                # 钉钉机器人格式
                payload = {
                    "msgtype": "text",
                    "text": {"content": msg},
                }
            else:
                # 通用 JSON 格式
                payload = {
                    "text": msg,
                    "alert": {
                        "exchange_a": spread.exchange_a,
                        "exchange_b": spread.exchange_b,
                        "spread_pct": spread.spread_pct,
                        "timestamp": spread.timestamp,
                    },
                }

            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info(f"[Webhook] 告警已发送")
            else:
                logger.warning(f"[Webhook] 发送失败: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[Webhook] 发送异常: {e}")

    def get_recent_alerts(self) -> List[dict]:
        """获取最近告警记录"""
        return list(self.recent_alerts)


# ============================================================
#  CSV 历史记录器
# ============================================================

class CSVHistoryWriter:
    """CSV 历史数据写入器

    将价差数据写入 CSV 文件，便于后续分析。
    每天创建一个新的文件，文件名格式：spread_history_YYYY-MM-DD.csv
    """

    def __init__(self, enabled: bool = False, output_dir: str = "./data"):
        """
        参数：
            enabled: 是否启用 CSV 写入
            output_dir: 输出目录
        """
        self.enabled = enabled
        self.output_dir = output_dir
        self._file_handles = {}  # {date_str: file_handle}
        self._writers = {}       # {date_str: csv_writer}
        self._lock = Lock()

        if enabled:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"[CSV] 历史数据将写入: {output_dir}/")

    def write(self, spreads: List[SpreadRecord]):
        """将价差记录写入 CSV 文件

        参数：
            spreads: 价差记录列表
        """
        if not self.enabled or not spreads:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with self._lock:
            # 如果今天的文件还未打开，创建它
            if today not in self._file_handles:
                self._open_file(today)

            writer = self._writers.get(today)
            if writer is None:
                return

            for s in spreads:
                writer.writerow([
                    s.timestamp,
                    s.exchange_a,
                    s.exchange_b,
                    s.symbol,
                    s.bid_a, s.ask_a, s.mid_a,
                    s.bid_b, s.ask_b, s.mid_b,
                    s.spread_pct,
                    s.direction,
                ])

    def _open_file(self, date_str: str):
        """打开（或创建）指定日期的 CSV 文件"""
        filename = os.path.join(self.output_dir, f"spread_history_{date_str}.csv")
        is_new = not os.path.exists(filename)

        fh = open(filename, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)

        # 如果是新文件，写入表头
        if is_new:
            writer.writerow([
                "timestamp", "exchange_a", "exchange_b", "symbol",
                "bid_a", "ask_a", "mid_a",
                "bid_b", "ask_b", "mid_b",
                "spread_pct", "direction",
            ])

        self._file_handles[date_str] = fh
        self._writers[date_str] = writer

    def close(self):
        """关闭所有打开的文件句柄"""
        with self._lock:
            for fh in self._file_handles.values():
                try:
                    fh.close()
                except Exception:
                    pass
            self._file_handles.clear()
            self._writers.clear()


# ============================================================
#  Web 看板（Flask 应用）
# ============================================================

def create_flask_app(calculator: SpreadCalculator, alert_manager: AlertManager):
    """创建 Flask Web 应用

    提供以下路由：
      GET /           → 看板主页
      GET /api/data   → 实时数据 JSON API
      GET /api/alerts → 最近告警记录
      GET /api/history → 历史价格数据（用于图表）

    参数：
        calculator: 价差计算器实例
        alert_manager: 告警管理器实例

    返回：
        Flask 应用对象
    """
    try:
        from flask import Flask, jsonify, Response
    except ImportError:
        logger.error("Flask 未安装，请执行: pip install flask")
        return None

    app = Flask(__name__)

    # 读取 dashboard HTML 文件路径
    dashboard_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "stablecoin-arbitrage-dashboard.html"
    )

    @app.route("/")
    def index():
        """返回看板 HTML 页面"""
        try:
            with open(dashboard_path, "r", encoding="utf-8") as f:
                html = f.read()
            return Response(html, mimetype="text/html; charset=utf-8")
        except FileNotFoundError:
            return Response(
                "<h1>Dashboard HTML not found</h1>",
                status=404,
                mimetype="text/html",
            )

    @app.route("/api/data")
    def api_data():
        """返回实时价格和价差数据

        返回 JSON：
        {
            "prices": [...],       // 各交易所最新价格
            "spreads": [...],      // 价差计算结果
            "timestamp": "...",    // 服务器时间
            "alert_count": 123,    // 累计告警次数
        }
        """
        prices = calculator.get_prices_as_dicts()
        spreads = calculator.get_spreads_as_dicts()
        return jsonify({
            "prices": prices,
            "spreads": spreads,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "alert_count": alert_manager.total_alerts,
            "threshold": alert_manager.threshold_pct,
        })

    @app.route("/api/alerts")
    def api_alerts():
        """返回最近告警记录"""
        return jsonify({
            "alerts": alert_manager.get_recent_alerts(),
        })

    return app


# ============================================================
#  主监控引擎
# ============================================================

class StablecoinMonitor:
    """稳定币价差监控主引擎

    负责协调所有组件：
      - 从各交易所获取价格
      - 计算价差
      - 检查告警条件
      - 更新 Web 看板数据
      - 写入历史记录
    """

    def __init__(self, config: dict):
        """
        参数：
            config: 配置字典，包含以下字段：
              - threshold: 告警阈值百分比
              - refresh_interval: 刷新间隔（秒）
              - web_port: Web 看板端口
              - csv_history: 是否启用 CSV 历史
              - csv_dir: CSV 输出目录
              - webhook_url: Webhook URL（可选）
              - web_only: 是否只启动 Web 服务不采集
        """
        self.config = config

        # 初始化组件
        self.calculator = SpreadCalculator()
        self.alert_manager = AlertManager(
            threshold_pct=config.get("threshold", DEFAULT_ALERT_THRESHOLD_PCT),
            webhook_url=config.get("webhook_url"),
        )
        self.csv_writer = CSVHistoryWriter(
            enabled=config.get("csv_history", False),
            output_dir=config.get("csv_dir", "./data"),
        )

        # 运行状态
        self._running = False
        self._tick_count = 0

        # 历史趋势数据（用于图表，保留最近 100 条）
        self.trend_history: deque = deque(maxlen=100)

        # 信号处理，优雅退出
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """优雅退出处理"""
        logger.info(f"\n收到信号 {signum}，正在退出...")
        self._running = False

    def _monitor_loop(self):
        """主监控循环"""
        interval = self.config.get("refresh_interval", DEFAULT_REFRESH_INTERVAL)

        logger.info(f"🔄 开始监控循环，刷新间隔: {interval}秒")
        logger.info(f"📊 告警阈值: {self.alert_manager.threshold_pct}%")
        logger.info(f"{"─" * 50}")

        while self._running:
            self._tick_count += 1
            try:
                self._one_tick()
            except Exception as e:
                logger.error(f"❌ 监控循环异常: {e}")

            # 等待下一次刷新
            for _ in range(int(interval * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def _one_tick(self):
        """执行一次数据采集和价差计算"""
        # 1. 获取所有交易所价格
        prices = self.calculator.fetch_all()
        if len(prices) < 2:
            logger.warning(f"⚠️ 仅获取到 {len(prices)} 个交易所数据，需要至少 2 个")
            return

        # 2. 计算价差
        spreads = self.calculator.calculate_all_spreads()

        # 3. 控制台输出当前价格和价差
        self._print_dashboard(prices, spreads)

        # 4. 检查告警
        self.alert_manager.check_and_alert(spreads)

        # 5. 写入 CSV
        self.csv_writer.write(spreads)

        # 6. 记录趋势数据
        if spreads:
            self.trend_history.append({
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "timestamp": time.time(),
                "max_spread": max(s.spread_pct for s in spreads),
                "spreads": [
                    {
                        "pair": f"{s.exchange_a}/{s.exchange_b}",
                        "pct": s.spread_pct,
                    }
                    for s in spreads
                ],
            })

    def _print_dashboard(self, prices: Dict[str, PriceTick], spreads: List[SpreadRecord]):
        """在终端打印简要仪表板"""
        # 只每隔 5 次完整打印一次，避免刷屏
        full_print = (self._tick_count % 5 == 1)

        if full_print:
            print(f"\n{'─' * 65}")
            print(f"  📡 各交易所 USDT/USDC 价格  |  #{self._tick_count}  |  "
                  f"{datetime.now().strftime('%H:%M:%S')}")
            print(f"{'─' * 65}")
            print(f"  {'交易所':<10} {'买价 (bid)':<14} {'卖价 (ask)':<14} {'中间价 (mid)':<14}")
            print(f"  {'─' * 58}")

        for name in sorted(prices.keys()):
            p = prices[name]
            if full_print:
                print(f"  {p.exchange:<10} {p.bid:<14.6f} {p.ask:<14.6f} {p.mid:<14.6f}")

        if spreads and full_print:
            print(f"\n  📊 跨交易所价差 (Top 5)")
            print(f"  {'交易对':<30} {'价差%':<12} {'方向':<20}")
            print(f"  {'─' * 60}")
            for s in spreads[:5]:
                marker = "🔴" if s.spread_pct >= self.alert_manager.threshold_pct else "🟢"
                print(f"  {marker} {s.exchange_a}/{s.exchange_b:<22} "
                      f"{s.spread_pct:<12.6f} {s.direction:<20}")

        if full_print:
            print(f"  累计告警: {self.alert_manager.total_alerts} 次 | "
                  f"数据采集: {self._tick_count} 次")
            print(f"{'─' * 65}")

    def run(self):
        """启动监控（阻塞式）

        同时启动 Web 看板线程和监控主循环
        """
        port = self.config.get("web_port", DEFAULT_WEB_PORT)

        # 启动 Web 看板
        app = create_flask_app(self.calculator, self.alert_manager)
        if app:
            web_thread = Thread(
                target=lambda: app.run(
                    host="0.0.0.0",
                    port=port,
                    debug=False,
                    use_reloader=False,
                ),
                daemon=True,
            )
            web_thread.start()
            logger.info(f"🌐 Web 看板已启动: http://localhost:{port}")
            logger.info(f"🌐 API 端点: http://localhost:{port}/api/data")
        else:
            logger.warning("⚠️ Flask 未安装，Web 看板不可用")

        logger.info(f"🚀 稳定币价差监控已启动")
        logger.info(f"{'=' * 50}")

        # 主监控循环
        self._running = True
        self._monitor_loop()

        # 清理
        self.csv_writer.close()
        logger.info("👋 监控已停止")


# ============================================================
#  命令行参数解析
# ============================================================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="稳定币价差监控工具 - 监控多交易所 USDT/USDC 价差并告警",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 默认运行（0.03% 阈值，10秒刷新）
  python stablecoin-arbitrage-monitor.py

  # 自定义阈值和刷新间隔
  python stablecoin-arbitrage-monitor.py --threshold 0.05 --interval 5

  # 启用 CSV 历史记录
  python stablecoin-arbitrage-monitor.py --csv-history --csv-dir ./data

  # 配置企业微信 Webhook 告警
  python stablecoin-arbitrage-monitor.py --webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

  # 只启动 Web 看板（不采集数据，用于测试）
  python stablecoin-arbitrage-monitor.py --web-only
        """,
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=DEFAULT_ALERT_THRESHOLD_PCT,
        help=f"告警阈值百分比（默认: {DEFAULT_ALERT_THRESHOLD_PCT}%%）",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=DEFAULT_REFRESH_INTERVAL,
        help=f"数据刷新间隔秒数（默认: {DEFAULT_REFRESH_INTERVAL}）",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_WEB_PORT,
        help=f"Web 看板端口（默认: {DEFAULT_WEB_PORT}）",
    )
    parser.add_argument(
        "--csv-history",
        action="store_true",
        default=False,
        help="启用 CSV 历史数据写入",
    )
    parser.add_argument(
        "--csv-dir",
        type=str,
        default="./data",
        help="CSV 文件输出目录（默认: ./data）",
    )
    parser.add_argument(
        "--webhook",
        type=str,
        default=None,
        help="Webhook 告警 URL（支持企业微信/钉钉/Slack）",
    )
    parser.add_argument(
        "--web-only",
        action="store_true",
        default=False,
        help="只启动 Web 看板服务",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="启用调试日志",
    )
    return parser.parse_args()


# ============================================================
#  程序入口
# ============================================================

def main():
    """主函数"""
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # 打印启动信息
    print("""
╔══════════════════════════════════════════════════════════════╗
║          💰 稳定币价差监控工具 v1.0.0                       ║
║     Stablecoin Arbitrage Monitor                            ║
║                                                              ║
║     监控: Binance / OKX / Bybit / Gate.io / HTX             ║
╚══════════════════════════════════════════════════════════════╝
    """)

    config = {
        "threshold": args.threshold,
        "refresh_interval": args.interval,
        "web_port": args.port,
        "csv_history": args.csv_history,
        "csv_dir": args.csv_dir,
        "webhook_url": args.webhook,
        "web_only": args.web_only,
    }

    logger.info(f"📋 配置:")
    logger.info(f"   告警阈值: {args.threshold}%")
    logger.info(f"   刷新间隔: {args.interval}秒")
    logger.info(f"   Web 端口: {args.port}")
    logger.info(f"   CSV 记录: {'✅ 启用' if args.csv_history else '❌ 未启用'}")
    logger.info(f"   Webhook: {'✅ 已配置' if args.webhook else '❌ 未配置'}")
    logger.info(f"   Web 仅模式: {'✅ 是' if args.web_only else '❌ 否'}")

    monitor = StablecoinMonitor(config)

    if args.web_only:
        # 只启动 Web 看板
        app = create_flask_app(monitor.calculator, monitor.alert_manager)
        if app:
            logger.info(f"🌐 Web 看板模式，端口: {args.port}")
            app.run(host="0.0.0.0", port=args.port, debug=True)
        else:
            logger.error("Flask 未安装，无法启动 Web 看板")
            sys.exit(1)
    else:
        # 完整监控模式
        monitor.run()


if __name__ == "__main__":
    main()
