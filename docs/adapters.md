# 接入自己的 Agent

先运行 `python -m examples.agent_demo`，阅读完整的 [Agent 示例](../examples/agent_demo.py)。它使用合成工具和假的模型响应，因此不需要 API 密钥。其他三个示例分别讲解验证循环、缺证据与路线审核。

## 任务与取证

创建 `Task(id, instruction, domain, context)`；上下文是不可变的键值对 tuple。工具实现 `name` 和 `collect(task) -> tuple[Evidence, ...]`。只注册可信工具，对网络、文件和数据库工具配置必要超时与访问权限。

一份输入可以写成：

```python
Evidence(
    id="stock-001", subject="synthetic-item", metric="stock",
    value=12, unit="item", source="synthetic-fixture", scope="demo-batch",
)
```

输入与事实有意分开：只有验证器输出且通过提交门控的 `Fact` 才进入正式状态。

## 用模型提出候选

`ModelProposer` 接受 `complete(prompt: str) -> str` 回调。你可以把已有模型 SDK 包装成这个函数，并在包装层设置超时、重试与凭据读取；框架不绑定任何服务商。

```python
ModelProposer(
    complete=my_model_callback,
    allowed_actions=("compute_balance",),
    evidence=evidence,
    instruction=task.instruction,
)
```

模型应返回单个 JSON object，或 `null` 表示没有候选。候选示意：

```json
{
  "id": "candidate-1",
  "action": "compute_balance",
  "target": "balance",
  "claim": "12",
  "refs": ["stock-001"]
}
```

上述只是 schema 示例，目标 ID、动作与引用须匹配你的领域。`claim` 为非空字符串。未知动作、额外字段、重复 JSON key 或格式错误都会失败关闭。接口仅产生候选，不自动执行 action，也不接受模型携带的校验结论。

## 独立验证与重建

验证器实现 `verify(candidate, state, residual, evidence)`。先按 ID 提取证据，再检查对象、指标、单位、范围及领域规则，最后重新计算结果。使用：

```python
Verdict.for_candidate(
    candidate, state, Decision.ACCEPT,
    evidence=evidence,
    facts=(verified_fact,),
    reasons=("independent recomputation matched",),
)
```

拒绝、延后、中断时不返回事实。验证器可以纠正提议而输出经过验证的值，但必须明确自己的语义契约；示例选择拒绝不匹配的 claim。`Fact.evidence_refs` 必须是被候选直接或通过已有事实间接引用的输入证据。

领域适配器实现 `rebuild(state) -> Residual`。返回仍未完成的 `goals`、`unknowns`、`hard_constraints`，所有 ID 全局唯一。不要从候选文字中读取 solved 标记，也不要忽略尚未满足的硬约束。

## 装配

`Agent` 使用任务工厂创建领域适配器与验证器；提议器工厂额外收到已适用的路线与采集的证据：

```text
domain_factory(task) -> Domain
verifier_factory(task) -> Verifier
proposer_factory(task, routes, evidence) -> tuple[Proposer, ...]
```

给 `Agent` 传入这些工厂、工具 tuple、可选 `memory` 及预算，调用 `run(task)`。结果可用 `to_dict()` 或 `to_json()` 导出。此架构是任务型同步 Agent；没有内置长期聊天服务、循环中动态取证或后台自动任务。

## 接入前应覆盖的领域测试

正确输入、错误 claim、缺失证据、跨对象证据、错范围/单位、相互矛盾的数据、工具异常、验证器异常，以及仍有硬约束时不可完成。先确定证据与验证契约，再增加更强的模型提议器。
