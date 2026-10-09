# ResiMind 发布文案

以下文案对应 main 分支已公开的知识增长实现。离线复用实验与真实模型实验分别报告；发布时保留对应结果链接。

## 短版：朋友圈、群聊、动态

**我把 ResiMind 开源了：让 Agent 把做对的推导，变成下一题的能力。**

推导一次，验证入库，让下一题接着用。

这是一套独立运行的神经符号 Agent 架构，沿用你的模型：模型探索步骤，代码独立核验，残差指出还差什么。完成的推导经过检查，才能成为可复用的规则；换一道题，应用仍要重验。

有界代数已经跑通这条知识增长链路。我们也固定同一个 DeepSeek，做了四组真实对照，把成功、失败、实际复用和学习成本一起公开。验证条件和来源跟着规则一起保存，哪一步成立、哪里还没解决，都能回查。

同一个核心，还能跑开放式规划、客服退款、数学优化证明和连续梁桥分析。**Python 核心零依赖，MIT 开源，离线示例无需 API Key。**

欢迎带着你的领域规则、难题和反例来试。觉得有用，给个 Star，一起把它做强。

[GitHub 与快速上手](https://github.com/1105216375-alt/resimind/blob/main/README.zh-CN.md) · [完整四组对照](https://github.com/1105216375-alt/resimind/blob/main/docs/algebra-live-evaluation.zh-CN.md)

## 长版：技术社区发布

**ResiMind：让 Agent 推导、验证、积累，再解决下一题**

一次推导做对了，能不能留下来，让下一道题少走几步？

我把这个过程做进了 ResiMind：一套独立运行的**神经符号 Agent 架构**，模型通过回调接入，推理流程由 Agent 自己执行。

模型提出步骤，领域代码独立核验。通过检查的事实才能提交；未解决的目标、约束与证据形成残差，告诉下一步还需要处理什么。完成任务后，Agent 可以从推导中提炼候选规则，经另一次独立检查后写入持久知识库。新任务检索到规则，仍要核验本次应用。

**推导出的知识，真正回到后续推理中。**

现在的代数示例从基础步骤推导平方、立方恒等式，核验后入库；保存、重载、重新验证，再冻结知识库运行 10 道新表达式任务。两组沿用同一确定性提议器、同一预算：

| 指标 | 固定知识库 | 增长知识库 |
| --- | ---: | ---: |
| 经独立评分确认完成 | 10/10 | 10/10 |
| 迁移提案次数 | 84 | **24** |
| 实际跨题规则应用 | 0 | **8** |

迁移提案减少 **71.4%**，学习两条规则另花 **13 次提案**。这组实验没有调用模型，任务是公开开发示例，证明了这批任务上的知识积累和复用收益。方法、逐题结果、学习成本和复现代码都在仓库。

**再固定同一个模型，测 Agent 架构带来了什么。**

同一 `deepseek-flash`、相同模型参数、每题最多 8 次调用，运行 8 道预先冻结的代数题：

| Agent 策略 | 正确完成 | 错误答案交付 | 迁移调用 |
| --- | ---: | ---: | ---: |
| 自建 ReAct 式基线 | 5/8 | 3 | 8 |
| 验证后重试 | 6/8 | 0 | 22 |
| 残差推理 | 6/8 | 0 | 22 |
| 残差 + 已验证知识增长 | **7/8** | **0** | **20** |

增长组发生了 **3 次实际跨题规则应用**，但这三题其他组也能完成；额外完成的那一题没有已提交规则应用，7/8 不能直接归因于规则复用。学习三条规则另需 **3 次调用**。学习加迁移为 23 次，不能把它宣传成总体更省钱。ReAct 式基线有可选检查工具，但模型未调用；这里测的是四种具体实现，不是对所有 ReAct 系统的排名。任务为人工构造的小样本，单次试验尚不足以证明普遍优势。

**当前开发结果也一起公开：**针对首次试验里重复算错的情况，我们增加了精确系数差异反馈，重跑同一组已见题。ReAct 式基线 **5/8、8 次调用**，验证后重试 **7/8、17 次**，残差推理 **6/8、22 次**，知识增长 **7/8、20 次，学习另计 3 次**。三种强制验证组仍无错误交付。增长组与重试完成数持平，调用更多；残差和增长两组本次没有提升完成数。这是看过结果后的开发重跑，不是新的盲测，原始冻结结果完整保留。

完整协议、逐题成绩、token 与学习成本一并公开，原始请求日志保留在本机。

**知识怎么进库，比“记住了多少”更关键。**

ResiMind 在当前支持范围内检查推导证书和适用条件：未知结果不能晋升，序列化文件里的“已验证”标签不能代替证明，重载时重新核验，规则可以撤销，每次复用还需过当前任务的验证器。库里存的是带条件和来源的候选知识，经检查后才获得使用资格。

这套核心也能处理没有唯一答案的任务。一次真实 DeepSeek 规划实验中，我们把“喝咖啡”改成必选：旧行程被拒绝，模型用 **1 次新增调用**重新组合地点，新方案以 **73 元、步行 35 分钟、13:04 返回**通过预算、时间和路线检查。模型负责选择，代码检查明确条件；场所与交通资料为合成数据。

另有可直接运行的示例：

- **客服退款：**规则算出 249 元，259 元的提议被拒；缺少签收证据，任务保持未决。
- **数学优化：**三变量约束二次优化，检查精确 LDLᵀ、KKT 条件与全局最优性证书。
- **桥梁工程：**24 m + 30 m 两跨连续梁，覆盖八个荷载工况，检查平衡、位移协调与给定限值。

当前的知识增长实例聚焦有界代数，其他领域通过独立验证器接入。规划核验给定资料下的可行性；客服输出处理建议；桥梁示例是合成线梁分析。

**沿用你的模型，扩展你的领域。Python 3.10+，核心零依赖，MIT 开源。**

先跑示例，再把你最在意的验收条件接进去。欢迎提交反例、补充领域适配器，也欢迎一起做同模型、同预算的对照实验。

[项目地址](https://github.com/1105216375-alt/resimind) · [中文快速上手](https://github.com/1105216375-alt/resimind/blob/main/README.zh-CN.md#三分钟跑起来) · [知识增长方法与结果](https://github.com/1105216375-alt/resimind/blob/main/docs/knowledge-growth.zh-CN.md) · [真实规划记录](https://github.com/1105216375-alt/resimind/blob/main/docs/evidence/open-planning/README.md)

## English launch post

**ResiMind: an Agent that builds on what it proves.**

I open-sourced ResiMind, a standalone neuro-symbolic Agent architecture. Your model proposes steps. Independent code checks them. Residual obligations guide what still needs solving. Completed, verified derivations can become reusable rules for later tasks.

The bounded algebra adapter now runs the full loop: derive an identity, independently verify its certificate, save it, re-verify it on reload, retrieve it for a new task, and check the new application.

In a ten-task offline comparison, both arms solved **10/10**. The growing library reduced transfer proposals from **84 to 24**, with **8 committed cross-task rule applications**. Learning the rules cost **13 additional proposals**. These are deterministic development results, with no model calls in this study.

The first frozen pilot (v1) uses the same **deepseek-flash** model on eight algebra tasks. ReAct-style completes **5/8**, verify-and-retry **6/8**, residual reasoning **6/8**, and residual plus verified growth **7/8**. The three gated arms deliver no invalid answer in this run. Growth uses **20 transfer calls plus 3 learning calls**, with **3 committed cross-task rule applications**. Those applications occur on tasks all arms solve; the extra completed task has no committed rule application, so causality is not established. This is one small, handcrafted pilot; the ReAct-style model used none of its optional check-tool calls. The full four-arm protocol, costs, and task results are published.

**Current development results are published too.** After reviewing v1, we added exact coefficient-discrepancy feedback and reran the same seen tasks. ReAct-style scores **5/8 in 8 calls**, verify-and-retry **7/8 in 17**, residual **6/8 in 22**, and growth **7/8 in 20, plus 3 discovery calls**. All gated arms again deliver no invalid answer. Growth now ties retry on completion and uses more calls; residual and growth completion did not improve. This is post-result development, not a fresh held-out test, and the original frozen result remains intact.

The same core also runs open-ended planning, customer-support checks, exact optimization certificates, and a continuous-beam engineering example. Bring your own model callback and domain verifier. The core has no third-party runtime dependencies and is MIT-licensed.

Try a demo, inspect a proof trace, or bring a counterexample. Contributions from people building agents that need both exploration and explicit acceptance checks are welcome.

[Repository](https://github.com/1105216375-alt/resimind) · [Quick start](https://github.com/1105216375-alt/resimind#try-it-in-three-minutes) · [Offline method and results](https://github.com/1105216375-alt/resimind/blob/main/docs/knowledge-growth.md) · [Same-model pilot](https://github.com/1105216375-alt/resimind/blob/main/docs/algebra-live-evaluation.md)

## 配图与事实来源

- 首图：[知识增长与两条推理循环](assets/knowledge-growth.svg)。图中数字对应离线迁移实验。
- 同模型真实调用：[四组协议与结果](algebra-live-evaluation.zh-CN.md)、[冻结 v1 摘要](evidence/algebra-live-v1/summary.json)、[旧题开发 v2 摘要](evidence/algebra-feedback-v2/summary.json)。
- 知识增长：[完整方法与结果](knowledge-growth.zh-CN.md)、[机器可读摘要](evidence/knowledge-growth-v1/summary.json)、[本地验证](validation-knowledge-growth.md)。
- 真实 DeepSeek 需求变更：[提议、拒绝与修正记录](evidence/open-planning/README.md)。
- 数学与桥梁：[数学优化](constrained-optimization.md)、[连续梁](continuous-bridge.md)。默认示例离线执行，错误候选为有意构造。
- 原始项目：[1105216375-alt/resimind](https://github.com/1105216375-alt/resimind)；作者与引用见 [CITATION.cff](../CITATION.cff)。

宣传中的量化主张对应明确的任务与实验条件；当前结果不构成通用性能排名或生产可靠性保证。
