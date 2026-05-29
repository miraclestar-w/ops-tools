# RPA+AI Agent实战：讯飞astron-rpa让个人开发者也能做自动化

> 5000+ Star 的开源企业级 RPA 桌面应用，个人开发者怎么用它接活赚钱？

---

## 一、为什么突然聊 RPA？

做运维的同学应该都经历过这种场景：每个月初财务那边甩过来一个 Excel，让你帮忙把某个系统里的数据导出来填进去。或者 HR 找你说"能不能帮我们每天自动登录招聘网站，把收到的简历下载下来？"这些需求本质上都是重复的、规则明确的、跨系统的操作——恰恰是 RPA 最擅长的事情。

但过去做 RPA 的门槛不低。UiPath、影刀这些工具要么价格不菲，要么学习曲线陡峭，个人开发者很难真正上手。

直到讯飞开源了 AstronRPA。

## 二、AstronRPA 是什么？

[AstronRPA](https://github.com/iflytek/astron-rpa) 是科大讯飞开源的一款企业级 RPA 桌面应用，GitHub 上已经拿了 5000+ Star。它的核心卖点很直接：

**可视化拖拽 + 300 多个预置组件 + 一键 Docker 部署**

你不需要从零写 Python 脚本来操控鼠标键盘。打开它的可视化设计器，拖几个组件、连几条线，一个自动化流程就搭好了。内置组件覆盖了浏览器自动化（Chrome、Edge、IE）、桌面应用操作（WPS、Office、金蝶、用友）、Excel 数据处理、邮件收发、计算机视觉等场景。

架构上，前端是 Vue 3 + Electron 桌面应用，后端用 Java Spring Boot 和 Python FastAPI，引擎层基于 Python。服务端通过 Docker 一条命令就能起：

```bash
git clone https://github.com/iflytek/astron-rpa.git
cd astron-rpa/docker
cp .env.example .env
docker compose up -d
```

客户端从 [Release 页面](https://github.com/iflytek/astron-rpa/releases) 下载安装包，装好改一下 `conf.yaml` 里的服务端地址就能用。

## 三、Agent-ready：这才是拉开差距的地方

AstronRPA 最有意思的设计是它对 AI Agent 的原生支持。它配套了一个 [Astron Agent](https://github.com/iflytek/astron-agent) 平台，两者双向集成：

1. **Agent 调用 RPA**：Agent 判断出某封邮件需要处理，直接触发 RPA 去登录系统操作
2. **RPA 调用 Agent**：抓取到一批数据后，让 Agent 做分类、摘要、异常检测

这意味着你做的不只是"机械式的屏幕自动化"，而是一个能思考、能判断、能执行的完整链路。

同时 AstronRPA 支持 MCP 服务和 API 调用触发，可以直接对接到 LangChain、Dify 等现有 Agent 框架里，把 RPA 当成一个 Tool 来调用。

## 四、个人开发者怎么用它赚钱？

RPA 需求在中小企业里是刚需，但很多公司买不起商业方案，也养不起专门的自动化团队。这就是机会。

**1. 帮中小企业做自动化定制**

每周从 ERP 导数据到 Excel、每天登录多个平台发布内容、定期生成报表发邮件——用 AstronRPA 搭好流程交付给客户运行。报价几千到几万不等。

**2. 做模板化流程包**

把通用流程（"每日自动备份数据库并发送邮件""自动登录政务系统填报数据"）做成标准化模板，在闲鱼或技术社群出售。一次开发，反复销售。

**3. 接 Agent 自动化的活**

很多公司光有大模型没有执行层。AstronRPA 天然就是 Agent 的执行层，帮客户搭建"AI 决策 + RPA 执行"的完整方案，单价通常更高。

**4. 做企业内部 RPA 赋能**

在公司内部推动 RPA 普及，用开源工具帮公司省钱，也给自己攒经验和口碑。

## 五、上手建议

1. 先在自己的 Windows 机器上装好客户端
2. 服务端用 Docker 部署到你的开发机或云服务器上
3. 跟着官方文档做一个浏览器自动化的 demo
4. 尝试用内置组件组合出更复杂的流程
5. 对 AI Agent 感兴趣的话，再把 Astron Agent 搭起来玩一下

技术栈要求不高：服务端需要 Docker，客户端目前主要支持 Windows 10/11，内存 8GB 以上就行。

## 六、写在最后

讯飞把这个项目开源出来，给个人开发者打开了一扇窗。过去 RPA 赛道被商业厂商把持，个人想接自动化项目的活，总绕不开授权费。现在有了成熟的开源方案，剩下的就是你的业务理解和工程能力了。

项目地址：https://github.com/iflytek/astron-rpa

有问题可以去 GitHub Issues 或者 Discussions 里讨论，讯飞团队响应还算积极。

---

*如果你正在考虑用 RPA 做点什么，不妨先 star 一下这个项目，说不定下一个自动化项目就用上了。*
