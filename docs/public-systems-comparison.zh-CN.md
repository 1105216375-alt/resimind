# ResiMind 与国内外相关方案的量化对标

核对日期：2026-10-09（北京时间）。ResiMind 审计版本：`9751e6da3f33714229639ecae9d312a9e1ab95d0`。

**结论：当前开源版已经实现可运行的验证、反馈和状态提交机制，但还没有证据证明它达到或超过相关先进系统的求解水平。** 最明确的差距是标准基准覆盖，以及自然语言约束形式化、符号搜索／求解和多步任务的实证。

本轮对标 Agent 架构与规划／约束处理能力，未测数学证明或桥梁工程水平。本次完成的是**官方论文成绩核对、本地代码审计和比较条件分析**，新增模型 API 请求为 0。下面的外部成绩来自作者报告，未在本机复现；它们不是 ResiMind 与对方同模型、同题、同预算的对跑结果。选取的是机制相关且有公开证据的代表性工作，不是截至今日的全球完整排行榜。

## 1. 可核验的公开数字

FPR（Final Pass Rate）在以下旅行论文中表示完整计划通过规定约束的比例；不同基准的约束并不相同。LLMFP 的最优率还要求目标值最优，不能与 FPR 混用。

| 方案／机构 | 模型与配置 | 数据与规模 | 作者报告的成绩 | 口径和预算 |
| --- | --- | --- | --- | --- |
| LLM-Modulo／美国亚利桑那州立大学 | GPT-4o；完整信息预先提供的 sole-planning | TravelPlanner validation，180 题 | Direct **8.33%**；LLM-Modulo **23.89%** | 最终通过率；最多 10 轮，temperature=0；不是等实际调用量比较。[论文表 1](https://arxiv.org/html/2411.14484v1) |
| Formal Travel Planner／MIT、Harvard、MIT-IBM | Claude-3-Opus-20240229；原版流程 | TravelPlanner test，1,000 题 | **93.9% FPR** | Z3 求解每题最多 30 分钟；全流程 token 上限未统一报告。[论文表 1](https://arxiv.org/html/2404.11891v3) |
| Formal Travel Planner／同上 | GPT-4；额外 JSON 提取阶段 | TravelPlanner test，1,000 题 | **97.0% FPR** | 增强配置，不能把它写成上一行原版成绩。[论文附录 D.1、表 8](https://arxiv.org/html/2404.11891v3) |
| NeSy Planning／南京大学、华为诺亚方舟实验室 | DeepSeek-V3；普通输入，未给 Oracle Translation | ChinaTravel 中文 Easy 300／Human-Val 154／Human-Test 1,000 题 | **52.6%／37.0%／23.3% FPR** | 每题符号搜索最多 5 分钟，不含模型推理；总 token 上限未明确。[论文 v5 表 3](https://arxiv.org/html/2412.13682v5) |
| LLMFP／MIT、MIT-IBM | Claude 3.5 Sonnet；零样本形式化编程 | 9 个领域，共 2,012 题 | 5 类多约束任务平均最优率 **80.7%**；4 类多步任务 **91.8%** | 最多 5 轮，solver 最长 15 分钟；两个分组指标分别报告。[论文 v3 表 1、2](https://arxiv.org/html/2410.12112v3) |
| ResiMind／本仓库 | deepseek-flash；固定规则与结构化证据 | 自建单日规划 12 题，其中 8 题有解 | 可行完成 **8/8**；无法核验交付 **0/12** | 最多 3 次逻辑模型调用／题；未运行上述标准基准。[本地实测](evaluation-summary.zh-CN.md) |

**不能从这张表得出“100% > 97% > 23.3%”的排名。** 题目、模型、输入、预算、评分器和指标不同；ResiMind 的 8/8 还存在明显难度天花板。不同论文即便使用相同基准名称，也不自动意味着配置完全相同。

国内对照还有一个有用的同文献结果：ChinaTravel v5 中，DeepSeek-V3 的 ReAct one-shot 在 Easy／Human-Val 上为 **5.33%／2.59% FPR**，对应 NeSy 为 **52.6%／37.0%**。这支持在那个实验配置下进一步研究符号搜索的作用，不能据此计算 ResiMind 的提升幅度；两种方法的实际计算量也不同。[同一论文表 3](https://arxiv.org/html/2412.13682v5)

版本边界：LLM-Modulo 使用 2024-11-20 的论文版本；Formal Travel Planner 使用 2025-01-29 v3；ChinaTravel 使用 2026-04-29 v5；LLMFP 使用 2025-07-09 v3。ChinaTravel 官方仓库在 **2026.08 修订了评测器**，所以这些历史论文成绩不能冒充最新代码复跑成绩。[官方变更记录](https://github.com/LAMDA-NeSy/ChinaTravel)

## 2. 机制差距，而非主观打分

| 比较对象 | 公开实现／论文中的关键机制 | ResiMind 当前状态 |
| --- | --- | --- |
| LLM-Modulo | 模型生成候选，外部验证器检查，反馈后重试 | 已有相近的验证反馈循环；该总体思想已有研究先例。还没有同条件对照证明本运行时带来额外收益。[论文](https://arxiv.org/html/2411.14484v1) |
| Formal Travel Planner | 把自然语言需求编译为形式化问题，Z3 搜索解，并可返回无解核 | 当前规划适配器只验证模型提出的完整行程，没有 NL→SMT 编译、正式求解器或无解核。求解保证也仅针对正确编码后的问题。[论文](https://arxiv.org/html/2404.11891v3) |
| ChinaTravel NeSy | 自然语言→DSL，结合模型推荐和符号回溯构造计划 | 没有对应 DSL 或符号回溯搜索。固定 schema 的检查能力不能等同于任意组合约束处理。[论文](https://arxiv.org/html/2412.13682v5) |
| LLMFP | 将多个领域的问题形式化后交给求解器，评估跨领域最优解 | 仓库有多个领域示例，但当前配对量化实验只覆盖一个规划领域，不能把示例数当泛化成绩。[论文](https://arxiv.org/html/2410.12112v3) |

ResiMind 已实现的工程特点包括证据引用、绑定状态的验证决定、受控事实提交、`defer` 和残差记录。这些接口值得继续验证；“更可靠”“更省钱”“更通用”仍需对照实测，不能只由接口存在推导。[架构与信任边界](architecture.md)

尤其需要澄清：当前规划示例把一整个行程作为候选，验收后一次提交；其三个固定未决项随后一起清空。**这轮试点没有测到逐步分解、局部修复或残差记忆的独立收益。** 这不等于运行时不能扩展，而是当前证据尚未覆盖。[实际领域实现](../src/resimind/domains/planning.py)

## 3. 我们已完成多少验证

下表限定为已归档的配对量化试点，不把零散联网演示合并成准确率；“0”表示尚未做相应实验，不是准确率为零。

| 项目 | 已有证据 |
| --- | ---: |
| 冻结合成任务 | 12：8 可行、2 不可行、2 缺证据 |
| 模型 | 1 |
| 每题共享首轮独立样本 | 1 |
| 同配置额外重复实验 | 0 |
| 方法臂 | 3：直接生成、自检、ResiMind |
| 物理 API 请求 | 31，全部完成 |
| 标准公开基准成绩 | 0 份；上述标准基准性能为未测 |
| 官方先进系统同场复现 | 0 组 |
| 验证／反馈／残差消融 | 0 组 |
| 可行任务上相对自检的配对胜／负／平 | 0／0／8 |

计数来自[冻结清单](evidence/planning-pilot-v1/manifest.json)、[完整评分](evidence/planning-pilot-v1/scored-results.json)及[本次证据登记](evidence/public-comparison-2026-10-09.json)。8/8 的描述性 Wilson 95% 区间为 **67.6%–100%**，且题目并非随机抽样，不能当真实用户总体准确率。

## 4. 为什么现在不能直接报同场名次

当前 `TripProblem` 只表达单日 0–1440 分钟、1–12 个地点、最多 256 条固定路线、walk/transit 和同一起点往返；工具在循环前取证。它不能原样表达官方旅行基准中的日期、多日住宿、跨城航班／列车、人数计价及组合约束。[领域代码](../src/resimind/domains/planning.py) · [工具执行边界](architecture.md)

删掉这些要求再把数据转成现有格式，只能得到衍生题，不能报官方基准成绩。真实对跑需要新增完整的评测适配器，而不是替换文件名。[TravelPlanner 官方任务](https://github.com/OSU-NLP-Group/TravelPlanner) · [ChinaTravel 官方约束](https://github.com/LAMDA-NeSy/ChinaTravel/blob/main/chinatravel/symbol_verification/readme.md)

复现条件也必须单列：LLM-Modulo 官方仓库主要发布提示和示例；Formal Travel Planner 有求解代码；LLMFP 的仓库说明仍列有未补齐的评价脚本；ChinaTravel 有多种算法实现，但其 LLM-modulo 模式要求 `--oracle_translation`，向算法暴露正确约束，不能与普通自然语言输入混排。[LLM-Modulo 代码](https://github.com/Atharva-Gundawar/LLM-Modulo-prompts) · [Formal Travel Planner 代码](https://github.com/yih301/LLM_Formal_Travel_Planner) · [LLMFP 代码](https://github.com/yih301/LLMFP) · [ChinaTravel 入口](https://github.com/LAMDA-NeSy/ChinaTravel/blob/main/run_exp.py)

AgentScope、Qwen-Agent、LangGraph 属于框架层。若后续实测，应标明其上实际运行的算法；不能把某个底座模型的榜单成绩或另一项目实现的 ReAct 成绩直接挂到框架名下。[AgentScope](https://github.com/agentscope-ai/agentscope) · [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)

## 5. 由证据决定的下一步

**首先接入官方评测，再决定需要增强哪些机制。** 建议以 ChinaTravel 为首个共同测试环境：其中已有国内 NeSy 与 LLM-Modulo 参考实现，便于明确记录输入权限和复现改动。

1. 固定上游 commit、数据哈希、官方评分器、题目 UID 和语言，保留所有原始约束。新增独立评测适配目录，不修改已冻结的 v1 源文件与清单。
2. 普通输入赛道对比官方 NeSy、官方 ReAct 与 ResiMind；需要 Oracle Translation 的方法另开“给定正确约束”赛道，并向同赛道所有方法开放相同信息。
3. 用同一底座，预先约定调用／token／墙钟／搜索预算，保留超时、格式失败和弃答。先用不进入正式计分的开发题验证接口，再冻结未调过的正式测试题；同时报告原生配置和受控预算配置，避免强行削弱某一方法。
4. 报告完整约束通过率、交付率、不可核验／无效交付、错误拒绝、实际调用成本和延迟；在有独立真值的无解／缺证据集合上另报结果，不把不交付当完成。
5. 再用多步依赖任务做验证、反馈、残差的消融。只有出现稳定差异，才能决定是否增加符号搜索、约束编译或局部修复，数学／桥梁另用对应的可信裁判。

本次没有把公开论文百分比当作本机实测，没有新增架构功能，也没有据此宣布领先或落后多少个百分点。**当前判断是：方向与已有神经符号路线相近，工程核心可继续发展，但先进性和残差机制收益仍待证明。**
