# Proxmox + GitOps：用代码管理虚拟化基础设施的自动化框架

## 前言

搞自托管的人都有这个痛点：Proxmox VE 装好之后，LXC 容器一个一个手动创建，配置文件散落在各处，哪天硬盘挂了或者要迁移，回忆"当时到底装了啥"比重建还累。

我去年把自己的 home lab 从手动管理迁移到了 GitOps 模式。整个方案基于三样东西：Proxmox VE 的 API、一套简单的声明式配置文件、一个 Git 仓库。用了半年，稳定性超出预期，迁移一台新宿主机从半天缩短到 20 分钟。这篇文章把核心思路和踩过的坑都记一下。

## 为什么是 LXC 而不是 VM

先说结论：个人开发者自托管，LXC 比 VM 实用得多。

LXC 容器共享宿主机内核，资源开销极低。同样的硬件，跑 10 个 LXC 容器和跑 3 个虚拟机完全不是一个概念。我的 N100 小主机上跑着 12 个 LXC 容器——Gitea、PostgreSQL、Nginx、Jellyfin、Home Assistant——日常内存占用不到 60%。

而且 LXC 的配置文件是纯文本，存放在 `/etc/pve/lxc/` 下面，天然适合版本管理。这为后面的 GitOps 方案打下了基础。

## GitOps 的核心思路

GitOps 说白了就一句话：**Git 仓库是唯一的事实来源，实际状态应该和仓库保持一致。**

在 Kubernetes 的世界里，ArgoCD 和 Flux 是成熟的 GitOps 工具。但 Proxmox 没有官方的 GitOps 工具链，所以我们需要自己搭。好在思路不复杂：

1. 用 YAML 声明每个容器应该是什么状态
2. 写脚本读取 YAML，调用 Proxmox API 执行创建/更新/删除
3. 把这个仓库管好，每次变更走 Git 流程

## 具体实现

### 项目结构

```
proxmox-infra/
├── inventories/
│   └── homelab.yaml          # 宿主机清单
├── containers/
│   ├── gitea.yaml
│   ├── postgres.yaml
│   ├── nginx.yaml
│   └── ...
├── templates/
│   └── debian-base.yaml      # 容器模板
├── scripts/
│   └── apply.py              # 执行引擎
├── .env.example              # API token 模板
└── Makefile                  # 常用命令封装
```

### 容器声明文件

每个容器一个 YAML 文件，结构尽量简单：

```yaml
# containers/gitea.yaml
name: gitea
host: pve-main
template: debian-12
cpu: 2
memory: 2048
disk: 20
network:
  bridge: vmbr0
  ip: 192.168.1.100/24
  gateway: 192.168.1.1
mounts:
  - volume: local-lvm:50
    path: /var/lib/gitea
features:
  - nesting
  - fuse
packages:
  - git
  - curl
  - apt-transport-https
```

这里没有用 Terraform 也没有用 Pulumi，而是直接手写了一层薄薄的封装。原因很简单：这些工具对 Proxmox 的支持要么是社区维护的 provider，要么需要额外装 provider 插件，对个人场景来说引入的复杂度大于收益。直接调 Proxmox 的 REST API（`pve-api-extension`）反而更可控。

### 执行引擎

核心脚本 `apply.py` 做三件事：

```python
# 伪代码，展示核心逻辑
def apply(container_config):
    current = get_container_state(container_config.name)

    if current is None:
        create_container(container_config)
        print(f"[create] {container_config.name}")
    elif needs_update(current, container_config):
        update_container(container_config)
        print(f"[update] {container_config.name}")
    else:
        print(f"[ok] {container_config.name}")
```

`needs_update` 函数做 diff：对比当前状态和声明状态的差异。Proxmox API 可以查询容器的 CPU、内存、磁盘、网络配置，逐项对比即可。有差异就更新，没差异就跳过。

创建容器时，先从模板克隆，再通过 API 逐步 apply 配置——设置 CPU/内存、挂载磁盘、配置网络、安装软件包。这个过程完全幂等，跑多少次结果都一样。

### 自动化流程

把脚本放进 Git 仓库之后，流程变成：

```
改 YAML → git commit → git push → CI 触发 → apply 脚本执行
```

CI 用的是 Gitea Actions（本身就是自托管的，不用 GitHub Actions 的公共额度）。工作流很简单：

```yaml
# .gitea/workflows/apply.yaml
name: Apply Infrastructure
on:
  push:
    branches: [main]
jobs:
  apply:
    runs-on: [self-hosted, proxmox]
    steps:
      - uses: actions/checkout@v4
      - run: make install-deps
      - run: make apply
        env:
          PROXMOX_API_TOKEN: ${{ secrets.PROXMOX_API_TOKEN }}
```

那个 `self-hosted, proxmox` runner 也跑在 Proxmox 的一个 LXC 容器里，算是真正的自举。

## 几个踩坑记录

**权限问题**：Proxmox 的 API Token 权限需要单独配。我最初给了 `PVEAuditor` 角色，结果发现创建容器需要 `PVEVMAdmin`。建议单独建一个 `infra-apply` 角色，只给需要的权限，别用超级管理员的 token。

**Template ID 问题**：`pveam list local` 查模板 ID，但不同版本的 Proxmox 模板 ID 可能变化。我在配置文件里加了 `template_id` 字段显式指定，避免被"自动发现"坑。

**网络配置冲突**：声明式管理最怕的是"漂移"——你在 Web UI 里手动改了 IP，但 YAML 里还是旧的。我加了一个 `preflight check` 步骤，apply 之前先检查有没有和声明不一致的容器，有就报警而不是静默覆盖，防止把正在运行的服务打挂。

**LXC 特性依赖**：部分应用（比如 Docker-in-LXC）需要开启 `nesting` 和 `fuse` 特性，这些在创建之后没法热修改，必须先停容器再改。所以声明文件里写清楚 features 很重要。

## 这套方案的局限

说句实话，这套方案不适合团队协作的生产环境。没有 RBAC、没有审批流、没有状态机、没有回滚机制。对于团队场景，还是建议上 Terraform Provider 或者直接用 Kubernetes。

但对个人开发者来说，它解决了一个真实的问题：**基础设施的可重复性和可恢复性。** 我的 Proxmox 配置全部在 Gitea 仓库里，哪天宿主机挂了，换一台新机器，装好 Proxmox，clone 仓库，跑一下 `make apply`，20 分钟所有容器全部恢复。这种确定性带来的安全感，比任何监控工具都实在。

## 写在最后

GitOps 不是什么新概念，但把它用在 Proxmox LXC 管理上确实是个小众场景。市面上的资料不多，我也是摸着石头过河。如果你也在搞自托管，不妨试试这个思路——不用一步到位，先把几个关键容器声明化，体验一下"用 Git 管基础设施"的感觉。

代码仓库我放在了 Gitea 上，暂时没开源，等整理干净了再放。有问题可以评论区交流。

---

*铁蛋运维 | 每周更新自托管实战经验*
