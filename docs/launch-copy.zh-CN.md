# ResiMind 发布文案

基于 v0.7.0 已公开代码与验证记录整理。以下内容可作为作者发布时的文案，数字均对应这一版本。

## 短版：朋友圈、群聊、动态

**我把 ResiMind 开源了：让 AI 放开想，让结果经得起查。**

这是一套独立运行的**神经符号 Agent 架构**。模型负责探索方案，独立代码检查明确规则，未完成的约束和缺失证据继续推动下一步推理。

一次真实 DeepSeek 实验里，我们把“喝咖啡”改成必选：旧行程被拒绝，模型根据反馈重新组合地点，新方案通过预算、时间和交通检查。场所资料为合成数据，完整提议与验证记录已经公开。

同一个 Agent 核心，接入开放式规划、客服退款、数学优化证书、连续梁桥分析。**核心零依赖，MIT 开源，离线示例无需 API Key。**

欢迎拿你最难验收的任务来试；有帮助的话，给个 Star，让更多人看到。

[GitHub：ResiMind](https://github.com/1105216375-alt/resimind) · [中文介绍](https://github.com/1105216375-alt/resimind/blob/main/README.zh-CN.md)

## 长版：技术社区首发

**ResiMind 开源：让 AI 放开想，让结果经得起查**

一个方案写得再漂亮，超预算、漏证据、算错退款，照样没法用。

我把 ResiMind 开源了：一套围绕**神经提议、符号验证、残差反馈**设计的独立 Agent 架构。它把“接下来怎么做”交给模型，把“这一步能不能接受”交给领域验证器。通过检查的事实才会提交；被拒绝的提议保留反馈，缺失证据继续留在待办中。

最直观的例子，是一次真实 DeepSeek 需求变更实验。

模型原先安排“画廊 → 面馆 → 阅览室”，满足原来的要求。随后，我们在结构化需求里把“喝咖啡”改成必选。验证器重新检查这份旧方案，发现必选活动缺失，拒绝提交。

DeepSeek 读到反馈后，用 **1 次新增调用**重新组合成“画廊 → 阅览室 → 咖啡馆”。新方案 **73 元、步行 35 分钟、13:04 返回**，通过全部给定硬约束。这个虚构场所目录中的咖啡馆同时提供餐食和咖啡。

模型可以选择不同的地点、顺序和交通方式，验证器不要求照抄固定答案。它检查方案是否满足明确约束；尚未满足的部分形成残差，继续驱动推理。这就是这个项目想展示的神经符号协作。

同一套核心还提供了这些可运行示例：

- **客服：**配置规则算出可退 249 元，提议 259 元会被拦下；缺少签收证据，就保留未决事项。
- **数学：**三变量约束二次优化，检查精确 LDLᵀ、KKT 条件和多项式全局最优性证书。
- **桥梁：**24 m + 30 m 两跨连续梁，检查位移协调与八个荷载工况；只满足力的平衡，还不能通过完整核验。

这些默认演示离线运行，包含有意构造的错误候选；真实模型响应另有独立审计记录。规划使用合成场所资料，客服给出处理建议，桥梁采用合成梁模型和给定限值。每个领域明确自己检查什么。

**Python 3.10+、核心零依赖、MIT 开源。**v0.7.0 已在 Python 3.12 本地通过 **660 项测试**，源码与安装后的包均做过验证。可以接入 DeepSeek，也可以接入自己的模型回调与领域验证器。

从离线示例开始跑，看看你的任务里，哪些错误值得让代码真正拦下来。欢迎提交 Issue、补充领域适配器；如果对你有用，也欢迎 Star。

[项目地址](https://github.com/1105216375-alt/resimind) · [中文快速上手](https://github.com/1105216375-alt/resimind/blob/main/README.zh-CN.md#三分钟跑起来) · [真实需求变更记录](https://github.com/1105216375-alt/resimind/blob/main/docs/evidence/open-planning/README.md) · [v0.7.0 验证记录](https://github.com/1105216375-alt/resimind/blob/main/docs/validation.md)

## English launch post

**ResiMind: let AI explore, verify what you trust**

I open-sourced ResiMind, a standalone neuro-symbolic Agent architecture. Models propose steps, independent code checks explicit domain rules, and unresolved obligations drive the next proposal.

In a recorded DeepSeek experiment, we added a mandatory coffee stop to a previously valid day plan. The verifier rejected the old itinerary. One new model call rearranged the venues, and the revision passed the budget, time, and route checks. The venue data is synthetic; the proposals and verification decisions are public.

The same core also runs customer-support policy checks, exact constrained-optimization certificates, and a continuous-beam engineering example. The core has no third-party runtime dependencies, uses the MIT license, and includes offline demos that need no API key.

Try a demo, inspect the audit, or bring your own model and domain verifier. I'd love feedback from people building agents whose outputs need explicit acceptance checks.

[Repository](https://github.com/1105216375-alt/resimind) · [Recorded experiment](https://github.com/1105216375-alt/resimind/blob/main/docs/evidence/open-planning/README.md)

## 配图与事实来源

- 首图：[项目横幅](assets/banner.svg)。
- 离线纠错配图：[规划轨迹](assets/planning-demo.svg)，标注“离线演示，错误候选为有意构造”。
- 数学动图：[拒绝、修正、证明](assets/verification-demo.gif)，同样属于离线示例。
- 真实 DeepSeek 需求变更：[原始记录与调用计数](evidence/open-planning/README.md)。旧方案在原要求下有效，本次因新增约束而被拒绝。
- 版本验证：[660 项本地测试与安装验证](validation.md)。

这些材料展示可核查的实现与实验，不提供模型准确率排名、零错误保证或真实工程认证。
