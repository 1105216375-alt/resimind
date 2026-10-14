# ChinaTravel 同模型正式评测：Bridge Doctor DeepSeek 配置

本记录是一次真实的、可审计的 ChinaTravel 正式小 cohort，不是完整 154-query 成绩。ResiMind 作为垂域 Agent runtime，和官方 ReAct、官方 LLM-guided NeSy 共享同一 DeepSeek 服务、温度、请求/时间/token 上限和官方 scorer。

## 固定设置

- 数据：ChinaTravel 官方 Human-Val，154 条数据全部按 pinned revision 准备并校验；正式 cohort 按预注册哈希规则选取 12 条，开发保留 2 条。
- 模型：runner 固定 `deepseek-flash`；Bridge Doctor 本机 DeepSeek 配置的 smoke 请求返回同一服务模型。密钥只在本机父进程读取，没有进入 worker、日志或仓库。
- 温度：`0`；reasoning effort：`none`；单请求最多 8,192 output tokens。
- 每题每臂：最多 128 次请求、16 MB 累计 prompt、65,536 output tokens、600 秒墙钟；SDK retries 为 `0`。
- 三条臂：`official_react`、`official_nesy`、`resimind_react`。正式 manifest：`ef4712877ff9ae971593d921154918b9b56834fb36f8c2747ebf8473e1ede5e2`。
- 评分：未改动的 ChinaTravel schema/common-sense/逻辑官方 scorer；失败、超时、无计划都保留在 12 题分母里。

## 正式 12 题结果

| 条件 | 最终通过 | 交付计划 | 技术失败 | 预算耗尽 | 平均 API 请求 | 平均墙钟 |
|---|---:|---:|---:|---:|---:|---:|
| Official ReAct | **0/12** | 12/12 | 0 | 0 | 69.0 | 78.5 s |
| Official NeSy | **3/12 (25.0%)** | 3/12 | 4 | 5 | 64.3 | 120.4 s |
| ResiMind-ReAct | **0/12** | 0/12 | 0 | 0 | 95.3 | 122.5 s |

ResiMind 的 12 个 run 都正常结束，但都停在 `max_steps` 或 gate/revision 循环后，没有交付最终计划；因此它们不是“模型答案错了以后被 scorer 判负”，而是运行时没有交付可评分 artifact。这个结果说明当前 whole-plan gate 在真实开放旅行规划上过于昂贵或保守，不能声称带来成功率提升。

开发 cohort（2 题 × 3 臂）同方向：Official ReAct `0/2`，Official NeSy `1/2`，ResiMind `0/2`。开发阶段发现的两类基础设施问题（sandbox Python 路径和 OpenAI SDK 版本）已修复并在正式 cohort 前重新冻结；这些失败记录没有混入正式结果。

## 解释边界

这不是完整 ChinaTravel 154-query 评测，不能外推为 154 条的准确率。完整三臂运行是 462 个独立任务，按每题 600 秒上限最坏约 25.7 小时，且会产生显著 API 成本；在 ResiMind 正式 12 题交付率为 0/12 的情况下，继续全量运行不会增加有意义的领先证据。

这次结果不否定 ResiMind 的验证层：本地数学/Lean 试验仍有 0/16 false accept 和 kernel recheck 证据。但它明确指出，下一项工程优先级不是宣传或扩题，而是把 `resimind_react` 的 gate/revision 从“反复审查但不交付”改成有剩余预算感知的候选压缩、失败类型分流和安全 fallback，然后在新的冻结 cohort 上重测。
