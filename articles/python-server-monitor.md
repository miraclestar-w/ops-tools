# 运维人必学：用Python打造你的7×24监控系统

> 不花一分钱，用Python+Telegram搭建专业级服务器监控

## 前言

作为运维，最怕的就是服务器半夜出问题，第二天早上才发现。今天教大家用Python搭建一个7×24小时自动监控系统，出问题自动推送到手机。

## 效果展示

```
🚨 服务器告警
时间：2026-05-28 03:25:12
服务器：192.168.1.27
问题：CPU使用率 92%
当前状态：已自动重启服务
```

## 技术栈

- Python 3.8+
- paramiko（SSH连接）
- requests（API调用）
- schedule（定时任务）

## 完整代码

### 1. 配置文件 config.yaml

```yaml
server:
  host: "192.168.1.27"
  port: 22
  username: "root"
  password: "your_password"  # 建议用密钥

monitor:
  cpu_threshold: 85
  memory_threshold: 85
  disk_threshold: 90
  check_interval: 300  # 5分钟

alert:
  telegram_bot_token: "your_bot_token"
  telegram_chat_id: "your_chat_id"
```

### 2. 监控脚本 monitor.py

```python
#!/usr/bin/env python3
"""7×24服务器监控系统"""
import yaml
import paramiko
import requests
import schedule
import time
from datetime import datetime

class ServerMonitor:
    def __init__(self, config_path):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
    
    def ssh_connect(self):
        """建立SSH连接"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.config['server']['host'],
            port=self.config['server']['port'],
            username=self.config['server']['username'],
            password=self.config['server']['password']
        )
        return client
    
    def check_server(self):
        """检查服务器状态"""
        try:
            client = self.ssh_connect()
            
            # 检查CPU
            stdin, stdout, stderr = client.exec_command(
                "top -bn1 | grep 'Cpu(s)' | awk '{print $2}'"
            )
            cpu = float(stdout.read().decode().strip())
            
            # 检查内存
            stdin, stdout, stderr = client.exec_command(
                "free | grep Mem | awk '{printf \"%.1f\", $3/$2 * 100}'"
            )
            memory = float(stdout.read().decode().strip())
            
            # 检查磁盘
            stdin, stdout, stderr = client.exec_command(
                "df -h / | tail -1 | awk '{print $5}' | tr -d '%'"
            )
            disk = int(stdout.read().decode().strip())
            
            client.close()
            
            return {
                'cpu': cpu,
                'memory': memory,
                'disk': disk,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def send_alert(self, message):
        """发送Telegram告警"""
        bot_token = self.config['alert']['telegram_bot_token']
        chat_id = self.config['alert']['telegram_chat_id']
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }
        
        requests.post(url, data=data)
    
    def run_check(self):
        """执行检查"""
        status = self.check_server()
        
        if 'error' in status:
            self.send_alert(f"🚨 连接失败\n{status['error']}")
            return
        
        alerts = []
        thresholds = self.config['monitor']
        
        if status['cpu'] > thresholds['cpu_threshold']:
            alerts.append(f"CPU: {status['cpu']}%")
        
        if status['memory'] > thresholds['memory_threshold']:
            alerts.append(f"内存: {status['memory']}%")
        
        if status['disk'] > thresholds['disk_threshold']:
            alerts.append(f"磁盘: {status['disk']}%")
        
        if alerts:
            message = f"""🚨 <b>服务器告警</b>
时间: {status['timestamp']}
服务器: {self.config['server']['host']}

异常指标:
{chr(10).join('- ' + a for a in alerts)}"""
            
            self.send_alert(message)
            print(f"[{datetime.now()}] 告警已发送")
        else:
            print(f"[{datetime.now()}] 一切正常")

if __name__ == "__main__":
    monitor = ServerMonitor("config.yaml")
    
    # 每5分钟检查一次
    schedule.every(5).minutes.do(monitor.run_check)
    
    print("监控系统已启动...")
    monitor.run_check()  # 立即执行一次
    
    while True:
        schedule.run_pending()
        time.sleep(1)
```

### 3. 使用方法

```bash
# 安装依赖
pip install paramiko pyyaml schedule requests

# 配置config.yaml

# 启动监控
python monitor.py

# 后台运行
nohup python monitor.py > monitor.log 2>&1 &
```

## 扩展功能

### 1. 监控Docker容器

```python
def check_docker(self):
    """检查Docker容器"""
    stdin, stdout, stderr = client.exec_command("docker ps --format '{{.Names}}|{{.Status}}'")
    containers = []
    for line in stdout.read().decode().strip().split('\n'):
        if '|' in line:
            name, status = line.split('|')
            containers.append({'name': name, 'status': status})
    return containers
```

### 2. 历史数据存储

```python
import sqlite3

def save_status(self, status):
    """保存到SQLite"""
    conn = sqlite3.connect('monitor.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS status
                 (timestamp TEXT, cpu REAL, memory REAL, disk REAL)''')
    c.execute("INSERT INTO status VALUES (?, ?, ?, ?)",
              (status['timestamp'], status['cpu'], status['memory'], status['disk']))
    conn.commit()
    conn.close()
```

### 3. Web看板

```python
from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/api/status')
def get_status():
    return jsonify(monitor.check_server())

if __name__ == '__main__':
    app.run(port=5000)
```

## 总结

这个监控系统的优势：
- ✅ 完全免费
- ✅ 7×24自动运行
- ✅ 手机实时推送
- ✅ 可扩展性强
- ✅ 代码简单易懂

适合：
- 个人站长
- 小型团队
- 学习运维自动化

## 获取完整代码

关注公众号「铁蛋运维」，回复「监控」获取完整代码包。

---

*作者：铁蛋运维小队*
*转载请注明出处*
