#!/usr/bin/env python3
"""
服务器健康检查工具 v1.0
一键检查服务器所有关键指标
"""
import subprocess
import json
import sys
from datetime import datetime

class ServerHealthChecker:
    def __init__(self):
        self.results = {}
    
    def run_cmd(self, cmd):
        """执行命令"""
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return r.stdout.strip(), r.returncode
        except:
            return "执行失败", 1
    
    def check_cpu(self):
        """检查CPU"""
        out, _ = self.run_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")
        try:
            return float(out.replace(",", "."))
        except:
            return -1
    
    def check_memory(self):
        """检查内存"""
        out, _ = self.run_cmd("free | grep -iE 'mem|内存' | awk '{printf \"%.1f\", $3/$2 * 100}'")
        try:
            return float(out)
        except:
            return -1
    
    def check_disk(self):
        """检查磁盘"""
        out, _ = self.run_cmd("df -h / | tail -1 | awk '{print $5}' | tr -d '%'")
        try:
            return int(out)
        except:
            return -1
    
    def check_load(self):
        """检查负载"""
        out, _ = self.run_cmd("cat /proc/loadavg | awk '{print $1}'")
        try:
            return float(out)
        except:
            return -1
    
    def check_docker(self):
        """检查Docker"""
        out, _ = self.run_cmd("docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null")
        containers = []
        for line in out.split("\n"):
            if "|" in line:
                name, status = line.split("|", 1)
                containers.append({"name": name.strip(), "status": status.strip()})
        return containers
    
    def check_services(self):
        """检查关键服务"""
        services = ["sshd", "docker", "cron"]
        results = []
        for svc in services:
            out, code = self.run_cmd(f"systemctl is-active {svc} 2>/dev/null")
            results.append({"name": svc, "status": out.strip()})
        return results
    
    def run_all_checks(self):
        """执行所有检查"""
        print("🔍 开始服务器健康检查...\n")
        
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "cpu": self.check_cpu(),
            "memory": self.check_memory(),
            "disk": self.check_disk(),
            "load": self.check_load(),
            "docker": self.check_docker(),
            "services": self.check_services()
        }
        
        return self.results
    
    def print_report(self):
        """打印报告"""
        r = self.results
        
        print("=" * 50)
        print("📊 服务器健康报告")
        print(f"时间: {r['timestamp']}")
        print("=" * 50)
        
        # 系统指标
        print("\n🖥️  系统指标:")
        print(f"  CPU:     {r['cpu']}% {'✅' if r['cpu'] < 85 else '⚠️'}")
        print(f"  内存:    {r['memory']}% {'✅' if r['memory'] < 85 else '⚠️'}")
        print(f"  磁盘:    {r['disk']}% {'✅' if r['disk'] < 90 else '⚠️'}")
        print(f"  负载:    {r['load']} {'✅' if r['load'] < 4 else '⚠️'}")
        
        # Docker容器
        if r['docker']:
            print(f"\n🐳 Docker容器 ({len(r['docker'])}个):")
            for c in r['docker']:
                status = "✅" if "Up" in c['status'] else "❌"
                print(f"  {status} {c['name']}: {c['status'][:40]}")
        
        # 系统服务
        print("\n⚙️  系统服务:")
        for s in r['services']:
            status = "✅" if s['status'] == 'active' else "❌"
            print(f"  {status} {s['name']}: {s['status']}")
        
        # 健康评分
        score = 100
        issues = []
        
        if r['cpu'] > 85:
            score -= 20
            issues.append("CPU过高")
        if r['memory'] > 85:
            score -= 20
            issues.append("内存过高")
        if r['disk'] > 90:
            score -= 30
            issues.append("磁盘不足")
        if r['load'] > 4:
            score -= 10
            issues.append("负载过高")
        
        print("\n" + "=" * 50)
        print(f"🏥 健康评分: {score}/100")
        
        if issues:
            print(f"⚠️  发现问题: {', '.join(issues)}")
        else:
            print("✅ 一切正常!")
        
        print("=" * 50)
        
        return score
    
    def export_json(self, filename="health_report.json"):
        """导出JSON报告"""
        with open(filename, 'w') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n📄 报告已保存: {filename}")


def main():
    checker = ServerHealthChecker()
    checker.run_all_checks()
    score = checker.print_report()
    
    if "--json" in sys.argv:
        checker.export_json()
    
    sys.exit(0 if score >= 80 else 1)


if __name__ == "__main__":
    main()
