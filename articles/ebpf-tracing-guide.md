# 用eBPF一行命令追踪线上诡异Bug（Linux内核调试实战）

> 本文面向有一定Linux基础的运维和开发同学。如果你曾经在凌晨三点被线上告警叫醒，对着满屏日志抓耳挠腮，那这篇文章就是为你写的。

---

## 前言：那个让我怀疑人生的下午

去年某天下午两点，我正在摸鱼写周报，运维群里炸了。

> "服务又抖了，P99 从5ms飙到2s，但CPU、内存、磁盘IO全正常。"

又？对，"又"。这已经是这周第三次了。每次抖个十来分钟自己又好了，像是有个幽灵在机房里捣乱。

我打开监控看了一圈：

- **CPU**：平稳，没毛刺
- **内存**：空闲还有20G
- **磁盘IO**：读写延迟正常
- **网络带宽**：没打满

"什么都查了，就是找不到原因。"

老运维张哥在群里丢了一句话："要不试试eBPF？"

eBPF这东西我听过，但一直没真正用过。印象中是个很高大上的内核技术，得写一堆C代码。但后来发生的事告诉我——**用BCC/CoRe工具，一行命令就能定位线上诡异问题**。

下面我把排查这几个问题的过程整理出来，希望能帮你少踩点坑。

---

## 一、eBPF到底是什么？（5分钟搞懂）

### 一句话版本

eBPF = 在内核里安全地跑你自己的小程序，不用改内核代码，不用重启，不用重新编译。

### 正经版本

eBPF（extended Berkeley Packet Filter）最早是给网络包过滤用的（BPF就是tcpdump的底层），后来Linux内核开发者发现这玩意太好用了，就把它扩展成了一个**内核可编程框架**。

核心能力：**你可以写一小段程序，挂到内核的几乎任何函数上（syscall、TCP协议栈、调度器、文件系统、内存分配器……），在它执行前后偷看参数和返回值，而内核完全不知道。**

### 架构图解

```
┌─────────────────────────────────────────────────────────┐
│                    用户空间 (User Space)                  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  bpftool     │  │   bcc/bpfcc  │  │   自定义工具  │  │
│  │  (命令行)     │  │  (Python库)   │  │  (libbpf等)   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│         └────────┬────────┴────────┬─────────┘          │
│                  │  BPF syscall    │                    │
│                  ▼                 ▼                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│               eBPF 虚拟机 (BPF Runtime)                  │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │  每次挂载都会被验证器(viator)检查，                    ││
│  │  确保不会死循环、不会越界访问、不会崩溃内核              ││
│  └─────────────────────────────────────────────────────┘│
│                         │                               │
├─────────────────────────┼───────────────────────────────┤
│                    内核空间 (Kernel Space)                │
│                         ▼                               │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐ │
│  │  网络栈  │  │  进程调度  │  │  内存管理 │  │  文件系统 │ │
│  │ tcp/ip  │  │  scheduler│  │  mm/vm   │  │  ext4/xfs│ │
│  │         │  │          │  │         │  │          │ │
│  └────▲────┘  └────▲─────┘  └────▲────┘  └────▲─────┘ │
│       │            │             │             │        │
│       └────────────┴──────┬──────┴─────────────┘        │
│                           │                             │
│                    eBPF 程序挂载点                       │
│                  (kprobe/uprobe/tracepoint等)           │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 挂载点类型速查

| 类型 | 说明 | 能挂在哪 |
|------|------|----------|
| **kprobe/kretprobe** | 内核函数入口/返回 | 任何内核函数（需root权限） |
| **uprobe/uretprobe** | 用户态函数入口/返回 | 任何可执行文件的函数 |
| **tracepoint** | 内核预埋的静态挂点 | scheduler、syscall、net等 |
| **XDP** | 网络包最早到达的处理点 | 网卡驱动层 |
| **cgroup** | 容器/cgroup级别的网络和资源 | 网络、内存等 |

### 关键心智模型

```
传统方式:                    eBPF方式:
                             
strace -p <pid>             bpftool trace
  │                            │
  │  要attach进程              │  挂到内核函数上
  │  性能开销大                │  性能开销极低（<1%）
  │  只能看到syscall            │  能看到内核内部状态
  │  会让程序变慢              │  对业务几乎无感
  ▼                            ▼
抓包器:                      XDP/eBPF:
                             
tcpdump                      直接在网卡驱动层处理
  │                            │
  │  需要抓完整包              │  100Gbps线速处理
  │  10G网卡就扛不住          │  丢包？不存在的
  │  事后分析                  │  实时过滤+聚合
```

---

## 二、环境准备（5分钟搞定）

### 内核版本要求

eBPF需要Linux 4.15+，推荐5.x+（特性更完整）。

```bash
# 查看内核版本
uname -r
# 5.15.0-generic  # OK，完美支持
# 4.14.0-generic  # 勉强能用，建议升级
```

### 安装BCC工具集

Ubuntu/Debian：

```bash
sudo apt update
sudo apt install -y bpfcc-tools linux-headers-$(uname -r)
# 安装完后，所有工具都在 /usr/sbin/ 下，命令格式是 bpftool-xxx
```

CentOS/RHEL 8+：

```bash
sudo yum install -y bcc-tools kernel-devel-$(uname -r)
# 工具在 /usr/share/bcc/tools/ 下
```

验证安装：

```bash
# 应该能看到几十个工具
ls /usr/sbin/bpftool-* 2>/dev/null | wc -l
# 或
ls /usr/share/bcc/tools/ 2>/dev/null | wc -l
```

### 权限说明

eBPF需要 `CAP_BPF` 或 root 权限。线上环境建议用 sudo，别折腾 capability 那套了，效率优先。

---

## 三、实战案例一：OOM排查（那个被误杀的进程）

### 现象

凌晨2点，一个Java服务被OOM Killer干掉了。但诡异的是：

- Java堆内存配置是4G，实际使用只有2.8G
- 系统总内存64G，空闲还有30G+
- cgroup没有设内存限制

**"内存明明够用，为什么要杀我的进程？"**

### 传统排查思路

```bash
# 看系统日志
dmesg -T | grep -i oom
# 或
journalctl -k | grep -i oom
```

日志显示：

```
[ 4827.631592] java invoked oom-killer: gfp_mask=0x200da, order=0
...
[ 4827.631598] Out of memory: Killed process 28347 (java)
```

但这些信息没啥用——它只告诉你谁被杀了，不告诉你**为什么**。

### eBPF一行命令搞定

```bash
# 安装oomkill工具（BCC内置）
# Debian/Ubuntu
sudo apt install -y bpfcc-tools

# 运行oomkill - 显示OOM Killer触发时的完整上下文
sudo bpftool-oomkill
```

如果系统没有 `bpftool-oomkill`，用这个Python脚本：

```python
#!/usr/bin/env python3
# oom_watch.py - 监控OOM相关事件
from bcc import BPF

bpf_text = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

struct event_t {
    u32 pid;
    u32 oom_score_adj;
    long pages;
    char comm[16];
};

BPF_PERF_OUTPUT(events);

// 挂载到 out_of_memory 这个内核函数
int trace_oom(struct pt_regs *ctx) {
    struct event_t event = {};
    event.pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}
"""

b = BPF(text=bpf_text)
b.attach_kprobe(event="out_of_memory", fn_name="trace_oom")

def handle_event(cpu, data, size):
    event = b["events"].event(data)
    print(f"⚠️  OOM事件! 进程: {event.comm.decode()} PID: {event.pid} "
          f"OOM Score: {event.oom_score_adj}")

b["events"].open_perf_buffer(handle_event)
print("监听中... 任意进程触发OOM时会捕获到")
while True:
    b.perf_buffer_poll()
```

但更直接的做法——**直接查OOM时的内存全景**：

```bash
# 方法1：用slabtop看内核自身的内存消耗
sudo slabtop -o -s c | head -30

# 方法2：用BCC的memleak看谁在泄漏（最猛）
sudo bpftool-memleak -p $(pgrep -f java) 10
# 每10秒打印一次还在增长的内存分配
```

我们当时用的方法更暴力：

```bash
# 用bcc的biolatency看磁盘IO（怀疑有大量swap）
sudo bpftool-biolatency -D

# 真相：某个内核模块在疯狂分配slab内存
# slabtop显示 inode_cache 占了40G+
```

### 真相

**inode_cache爆了**。服务器上有个定时任务在扫一个包含500万个小文件的目录，每扫一次就在内核里创建大量inode缓存。虽然系统空闲内存还很多，但这些内存是slab分配的，不能被直接回收。OOM Killer看的是 `committed_AS`，不是空闲内存。

**修复**：调整了 `vm.vfs_cache_pressure` 参数，并且那个定时任务改成了分批扫描。

```bash
# 当前值是100（默认），调到500让内核更积极回收inode缓存
echo 500 > /proc/sys/vm/vfs_cache_pressure
```

---

## 四、实战案例二：网络抖动（那个抓不到的包）

### 现象

服务间调用偶发超时，监控显示：

- TCP重传率偶尔飙到5%
- 延迟毛刺每次持续1-3分钟
- 恢复后一切正常

运维怀疑是网络设备问题，但让网络团队查了交换机日志，说"没问题"。

### 传统排查思路

```bash
# 看TCP重传统计
netstat -s | grep -i retransmit
# 看当前连接状态
ss -ti | grep -E 'retrans|rto'
# 看丢包
ethtool -S eth0 | grep -i drop
```

能看到重传，但看不到**哪些连接在重传、为什么重传**。

### eBPF精准定位

```bash
# 工具1：tcpconnect - 看所有TCP连接发起的延迟
sudo bpftool-tcpconnect

# 输出：
# PID    COMM         IP  LADDR           LPORT  DADDR           DPORT  LAT(ms)
# 12345  java         4   10.0.1.5        43210  10.0.1.10       8080   2.3
# 12345  java         4   10.0.1.5        43211  10.0.1.10       8080   1547.8  ← 这个就不正常了
```

但这还不够直观。我们写了个更精准的脚本，**专门抓高延迟的TCP包**：

```bash
#!/usr/bin/env python3
# tcp_latency_anomaly.py
"""
检测TCP发送延迟异常
思路：在tcp_sendmsg里记录时间戳，tcp_write_xmit返回时计算差值
"""
from bcc import BPF
from datetime import datetime
import time

bpf_text = """
#include <uapi/linux/ptrace.h>
#include <net/sock.h>

struct tcp_event_t {
    u32 pid;
    u64 delta_us;
    u32 daddr;
    u16 dport;
    char comm[16];
};

BPF_HASH(start, u32, u64);
BPF_PERF_OUTPUT(events);

int trace_tcp_write_entry(struct pt_regs *ctx, struct sock *sk, struct sk_buff *skb) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 ts = bpf_ktime_get_ns();
    start.update(&pid, &ts);
    return 0;
}

int trace_tcp_write_ret(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 *tsp = start.lookup(&pid);
    if (!tsp) return 0;

    u64 delta = bpf_ktime_get_ns() - *tsp;
    start.delete(&pid);

    // 只关注延迟超过10ms的（10000000纳秒）
    if (delta < 10000000) return 0;

    struct tcp_event_t event = {};
    event.pid = pid;
    event.delta_us = delta / 1000;
    event.dport = 0; // 简化处理
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}
"""

b = BPF(text=bpf_text)

# 挂载点
if BPF.get_kprobe_functions(b"tcp_sendmsg"):
    b.attach_kprobe(event="tcp_sendmsg", fn_name="trace_tcp_write_entry")
    b.attach_kretprobe(event="tcp_sendmsg", fn_name="trace_tcp_write_ret")

def handle_event(cpu, data, size):
    event = b["events"].event(data)
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] ⚠️  TCP延迟异常 | "
          f"进程: {event.comm.decode()} PID: {event.pid} "
          f"延迟: {event.delta_us/1000:.1f}ms")

b["events"].open_perf_buffer(handle_event)
print("监听TCP发送延迟... (仅显示>10ms的)")
while True:
    b.perf_buffer_poll()
```

### 另一个杀器：抓内核丢包

```bash
# 查看内核协议栈各环节的丢包情况
sudo bpftool-dropcount

# 或者用下面这个更清晰的方式
sudo bpftool-drops

# 输出：
# FUNC                COUNT
# __sk_free_drop      3847   # socket被释放时丢包
# __kfree_skb_drop    1203   # skb异常
# __udp4_lib_rcv_drop 89     # UDP接收丢包（不该出现的）
```

### 真相

**是容器的网络命名空间问题**。

排查发现，抖动的时间窗口和另一个服务的滚动更新完全吻合。那个服务在更新时会创建/销毁容器，每次容器销毁时，对应的cgroup网络队列会瞬间产生一个处理延迟——**不是丢包，是处理慢了**。

而我们服务的连接池配置太小（只有10个），当某几个连接恰好命中这个窗口时，就会出现"看起来像超时"的现象。

**修复**：

1. 连接池从10调到30（给了更多缓冲）
2. TCP keepalive从600s调到60s（更早释放坏连接）
3. 那个服务的容器销毁流程加了优雅退出等待

---

## 五、实战案例三：锁竞争（那个莫名变慢的接口）

### 现象

一个核心接口的P99延迟从2ms涨到200ms，但：

- 代码没改过
- 流量没增加
- 机器配置没变
- CPU使用率才15%

### 传统排查思路

```bash
# 看进程在干嘛
strace -p <pid> -c -T
# 或者
perf top -p <pid>
# 或者
pidstat -p <pid> -t 1
```

能看到CPU在花，但看不出**为什么在花**——是在算数，还是在等锁？

### eBPF一看便知

```bash
# 方法1：用offcputime看进程花在"等待"上的时间
sudo bpftool-offcputime -p $(pgrep -f java) 5
# -p 指定进程，5秒采样周期
# 输出会显示每次off-cpu的时间花在哪个内核调用栈上
```

这个工具会告诉你进程**不在CPU上运行时都在干嘛**——等待I/O、等待锁、等待调度、还是在睡眠。

```bash
# 方法2：用runqlat看调度延迟
sudo bpftool-runqlat

# 如果一个核心的runqlat特别高，说明有大量任务在排队等CPU
# 但我们的情况不是这样
```

我们用offcputime抓到的结果：

```
@[
    __switch_to_asm+0x40
    schedule+0x36
    futex_wait_queue+0x9f
    do_futex+0x128
    __x64_sys_futex+0x136
    do_syscall_64+0x38
]: 847293    # 847ms！全花在futex等待上
```

**futex等待**——这就是用户态锁竞争的典型表现。

### 进一步定位是哪把锁

```bash
#!/usr/bin/env python3
# lock_contention.py - 精确到哪把锁在打架
from bcc import BPF
from time import sleep

bpf_text = """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

struct lock_event_t {
    u64 total_ns;
    u32 count;
    u32 pid;
    u64 tid;
    char comm[16];
};

BPF_HASH(lock_stats, u64, struct lock_event_t);
BPF_HASH(lock_start, u64, u64);

// 在 futex_wait_entry 入口记录开始时间
int trace_lock_entry(struct pt_regs *ctx) {
    u64 id = bpf_get_current_pid_tgid();
    u64 ts = bpf_ktime_get_ns();
    lock_start.update(&id, &ts);
    return 0;
}

// 在 futex_wait_return 出口计算等待时长
int trace_lock_return(struct pt_regs *ctx) {
    u64 id = bpf_get_current_pid_tgid();
    u64 *tsp = lock_start.lookup(&id);
    if (!tsp) return 0;

    u64 delta = bpf_ktime_get_ns() - *tsp;
    lock_start.delete(&id);

    // 只统计等待超过1ms的
    if (delta < 1000000) return 0;

    u64 key = bpf_ktime_get_ns() >> 20;  // 粗粒度聚合
    struct lock_event_t *val = lock_stats.lookup(&key);
    if (!val) {
        struct lock_event_t new_val = {
            .total_ns = delta,
            .count = 1,
            .pid = id >> 32,
            .tid = id,
        };
        bpf_get_current_comm(&new_val.comm, sizeof(new_val.comm));
        lock_stats.update(&key, &new_val);
    } else {
        val->total_ns += delta;
        val->count++;
    }
    return 0;
}
"""

b = BPF(text=bpf_text)

# 挂载到 futex 的等待入口和返回
b.attach_kprobe(event="futex_wait_queue_me", fn_name="trace_lock_entry")
b.attach_kretprobe(event="futex_wait_queue_me", fn_name="trace_lock_return")

sleep(10)  # 采样10秒

# 打印结果
for k, v in sorted(b["lock_stats"].items(), key=lambda x: x[1].total_ns, reverse=True):
    print(f"锁等待统计: 进程={v.comm.decode()} PID={v.pid} "
          f"总等待时间={v.total_ns/1000000:.1f}ms 次数={v.count} "
          f"平均={v.total_ns/v.count/1000:.1f}ms")
```

### 真相

**Redis连接池的全局锁竞争**。

我们用的是一个旧版本的Redis客户端库，它内部用了一把全局锁来管理连接池。当并发量上来后（200 QPS），所有线程都在抢这一把锁。

而且更坑的是，这个锁的粒度太大——获取连接时锁住整个池，释放连接时也锁。每次持锁期间还要跟Redis做一次ping/pong健康检查。

**修复**：换了个连接池实现（用CAS无锁队列），P99从200ms降到3ms。

---

## 六、eBPF速查表：遇到问题先跑这些命令

### 内存相关

```bash
# OOM事件追踪
sudo bpftool-oomkill

# 内存泄漏检测（指定进程，每5秒打印一次）
sudo bpftool-memleak -p <PID> 5

# 看slab内存消耗
sudo slabtop -o -s c | head -20

# 查看内存分配调用栈
sudo bpftool-memleak -p <PID> --alloc-page 10
```

### 网络相关

```bash
# TCP连接延迟分布
sudo bpftool-tcpconnect

# TCP重传追踪
sudo bpftool-tcpretrans

# TCP接收延迟
sudo bpftool-tcplife

# 网卡丢包统计
sudo bpftool-drops

# 抓TCP SYN/ACK（看三次握手延迟）
sudo bpftool-tcpconnect -X
```

### CPU/调度相关

```bash
# CPU off-time（进程不在CPU上干嘛）
sudo bpftool-offcputime -p <PID> 5

# 调度延迟（进程等了多久才轮到它）
sudo bpftool-runqlat

# 函数级CPU耗时
sudo bpftool-profile -p <PID> 5

# 系统调用统计
sudo bpftool-profile -af 5
```

### 文件系统相关

```bash
# 文件读写延迟
sudo bpftool-ext4slower 1

# 文件打开追踪
sudo bpftool-fileslower 1

# 缓存命中率
sudo bpftool-cachestat
```

---

## 七、线上使用注意事项

### 1. 性能影响

eBPF程序对业务的影响**极小**（通常<1%），但有几个例外：

- **高频tracepoint**：比如挂在每次内存分配上，每秒可能触发百万次
- **复杂的eBPF程序**：验证器会限制循环次数，但复杂的map操作还是有开销
- **perf_buffer满**：如果事件产出速度跟不上消费速度，会丢事件

```bash
# 建议：先用小范围测试
sudo bpftool-offcputime -p <PID> 1  # 先采1秒看看
# 确认没问题再加长时间
```

### 2. 权限与安全

```bash
# 最小权限方案
sudo setcap cap_bpf,cap_perfmon=eip /usr/sbin/bpftool-tcpretrans

# 或者用sudoers限制
# /etc/sudoers.d/bpf
%bpf ALL=(root) NOPASSWD: /usr/sbin/bpftool-*
```

### 3. 内核版本兼容性

```bash
# 检查内核是否支持eBPF
cat /boot/config-$(uname -r) | grep CONFIG_BPF
# 应该有 CONFIG_BPF=y CONFIG_BPF_SYSCALL=y

# 常见问题：内核太旧没有某些tracepoint
# 解决：升级内核或用kprobe替代
```

### 4. 生产环境最佳实践

- **先在测试环境跑一遍**，确认工具输出格式和字段含义
- **用完及时停止**，避免长期挂载影响系统稳定性
- **采样间隔不要太短**，1-5秒是合理范围
- **关注内核版本升级后的行为变化**，同一工具在不同内核上的输出可能不同
- **配合监控系统使用**，把eBPF抓到的数据推到Prometheus/Grafana

---

## 八、进阶方向：从工具到自定义

当你用熟了BCC自带的工具后，可以开始写自己的eBPF程序。

### 技术栈推荐

```
入门：  BCC (Python) → 写得快，调试方便
       ↓
进阶：  libbpf + CO-RE → 编译一次到处运行
       ↓
生产：  libbpf-rs (Rust) → 性能最好，生态活跃
       ↓
前沿：  aya (Rust) → 纯Rust实现，无C依赖
```

### 推荐学习资源

1. **BCC官方文档**：https://github.com/iovisor/bcc
2. **Brendan Gregg的BPF性能工具书**（必读）
3. **eBPF.io官方文档**：https://ebpf.io
4. **cilium/ebpf**（Go）：https://github.com/cilium/ebpf

---

## 总结

回到文章开头那个下午。后来我们用eBPF工具集搭建了一套"线上透视系统"——不改代码，不重启服务，随时能看到内核里发生的事。

```
传统排查（平均耗时）:     eBPF排查（平均耗时）:
  
内存问题  → 2-3小时       内存问题  → 10分钟
网络问题  → 4-8小时       网络问题  → 20分钟  
性能问题  → 1-2天         性能问题  → 1-2小时
```

eBPF不是银弹，但它改变了调试的范式——**从"事后猜测"变成了"实时观察"**。

你不需要成为内核开发者才能用eBPF。把 `bcc-tools` 装上，遇到问题先跑一下 `bpftool-xxx`，也许一行命令就能让你从凌晨三点的告警里解脱出来。

---

*如果你也在用eBPF排查过线上问题，欢迎在评论区分享你的故事。*

*觉得有用的话，点个在看转发给还在被线上Bug折磨的同事吧。*
