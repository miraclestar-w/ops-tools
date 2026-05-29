# 浏览器自动化新选择：vercel-labs/agent-browser 让 AI Agent 操控浏览器

## 一句话总结

Vercel Labs 开源的 `agent-browser` 是一个用 Rust 编写的浏览器自动化 CLI 工具，专门为 AI Agent 设计。它解决了传统 Puppeteer/Playwright 在 Agent 场景下的几个痛点：启动慢、选择器脆弱、缺少语义化的页面理解能力。

## 为什么 Agent 需要专门的浏览器工具

做运维或者开发的同学，大多用过 Puppeteer 或 Playwright 来做浏览器自动化。这两个工具确实好用，但它们的设计初衷是面向**人类脚本编写者**的——你需要写代码来精确定位元素，用 CSS 选择器或 XPath 去操控页面。

AI Agent 的工作方式完全不同。Agent 不会"写代码"去定位元素，它需要的是：

1. **理解页面结构**：知道当前页面有什么内容、哪些可以交互
2. **语义化的操作接口**：用可读的引用（比如 `@e2`）而不是脆弱的 CSS 选择器
3. **一次性的状态快照**：拿到页面全貌，然后决定下一步操作
4. **低延迟的反复调用**：Agent 每个决策步骤都要和浏览器交互，不能每次启动都慢半拍

Puppeteer 虽然也能做到这些，但你需要自己搭脚手架，把 CDP 协议的细节封装成 Agent 友好的接口。`agent-browser` 直接把这些都内置了。

## agent-browser 是什么

[vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) 是 Vercel Labs 出品的开源项目。核心卖点：

- **Rust 原生 CLI**：不是 Node.js 封装，是真正的原生二进制，启动速度极快
- **守护进程架构**：浏览器以 daemon 模式运行，CLI 命令复用同一个浏览器实例，避免反复启动的开销
- **Accessibility Tree 快照**：`snapshot` 命令输出页面的无障碍树，每个可交互元素都带引用编号（`@e1`、`@e2`...），Agent 直接读这个就能理解页面
- **自然语言操控**：内置 `chat` 命令，可以直接用自然语言控制浏览器
- **Batch 执行**：多条命令打包成一次调用，减少进程开销

## 安装和上手

安装非常简单，npm 全局装就行：

```bash
npm install -g agent-browser
agent-browser install  # 首次运行下载 Chrome for Testing
```

macOS 用户也可以用 Homebrew：

```bash
brew install agent-browser
agent-browser install
```

Linux 环境记得加 `--with-deps` 安装系统依赖：

```bash
agent-browser install --with-deps
```

## 实测案例：从登录到数据采集

分享几个我在实际场景中测试的案例。

### 场景一：访问网页并提取结构化数据

```bash
agent-browser open https://news.ycombinator.com
agent-browser snapshot
```

`snapshot` 命令会输出类似这样的内容：

```
@e1 [link] "Hacker News"
@e2 [link] "new | past | comments | ask | show | jobs | submit"
@e3 [link] "1. Show HN: ..."
@e4 [text] "327 points by user1 2 hours ago"
```

每个元素都有引用编号和语义信息。Agent 拿到这个输出后，直接用 `@e3` 去点击、用 `@e4` 去读文本，完全不需要关心 DOM 结构。

### 场景二：表单填写

```bash
agent-browser open https://example.com/login
agent-browser snapshot
agent-browser fill @e5 "admin@example.com"
agent-browser fill @e6 "mypassword"
agent-browser click @e7
agent-browser wait --text "Dashboard"
agent-browser screenshot dashboard.png
```

注意这里的 `fill` 和 `click` 直接用 `@e5`、`@e7` 这样的引用，不用写 `#email-input` 或 `.login-btn`。即使前端改了 DOM 结构，只要页面功能不变，引用编号就能对应上。

### 场景三：自然语言控制（内置 AI Chat）

`agent-browser` 内置了 AI Chat 模式：

```bash
agent-browser chat "打开 github.com/trending，帮我找到今天 star 数最多的仓库名称"
```

这条命令会启动一个 AI 驱动的浏览器操控会话，Agent 自动打开页面、解析内容、完成任务。对于快速原型验证非常方便。

### 场景四：Batch 批量操作

```bash
agent-browser batch \
  "open https://example.com" \
  "snapshot -i" \
  "click @e3" \
  "wait --networkidle" \
  "screenshot result.png"
```

所有命令打包成一次调用，避免了每条命令都启动新进程的开销。对 Agent 的 loop 来说，这是性能的关键提升。

## 和 Puppeteer 的关键差异

| 对比维度 | Puppeteer | agent-browser |
|---------|-----------|---------------|
| 语言 | Node.js | Rust 原生 |
| 启动方式 | 每次启动新浏览器 | 守护进程复用 |
| 元素定位 | CSS/XPath（脆弱） | Accessibility Tree + 语义引用 |
| AI 友好度 | 需要自行封装 | 原生设计 |
| Batch 支持 | 需要自己实现 | 内置 |
| 内存占用 | 较高 | 较低 |

核心区别在于：**Puppeteer 是给开发者写脚本用的，agent-browser 是给 AI Agent 做决策用的**。前者关注"怎么精确操控元素"，后者关注"怎么让 Agent 理解页面并做出正确操作"。

## 一些实用细节

- **认证状态保持**：`agent-browser state save` / `state load` 可以保存和恢复登录状态，不用每次都重新登录
- **多标签页管理**：`tab new`、`tab close` 支持标签页操作，标签 ID 稳定（`t1`、`t2`...），不会因为页面导航而失效
- **React/Vitals 支持**：如果你在做前端运维，`react tree` 和 `vitals` 命令可以直接在浏览器内查看组件树和 Web Vitals 指标
- **网络拦截**：`network route` 命令可以拦截和 mock 网络请求，方便做故障注入测试

## 总结

如果你正在做 AI Agent 相关的项目，需要让 Agent 和浏览器交互，`agent-browser` 值得认真看看。它把很多 Agent 场景下的基础设施问题都解决了——语义化的页面理解、低延迟的重复操作、自然语言接口。

对于传统运维场景（比如监控页面状态、自动化测试），它也是一个比 Puppeteer 更轻量的选择。Rust 原生的性能优势在反复调用的场景下非常明显。

开源地址：[github.com/vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)

---

*铁蛋运维 | 运维技术分享*
