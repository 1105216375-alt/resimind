# 可审计的验证记忆生命周期

前沿版的“记住规则”不能等同于把一段成功输出塞进库里。形式记忆现在给每条
已由 Lean 核验的引理附加一组**建议性元数据**：来源任务、证据引用、适用标签、
创建/过期时间和核验器版本。

```python
from resimind.integrations.formal import LemmaMetadata

metadata = LemmaMetadata(
    source_task_id="task:commutativity",
    evidence_refs=("lean:proof.olean",),
    applicability=("prop:commutative",),
    created_at="2026-01-01T00:00:00+00:00",
    expires_at="2026-02-01T00:00:00+00:00",
    verifier_id="lean-4.29.0",
)
memory, proof = memory.admit(goal, recipe, backend, metadata=metadata)
```

这些字段不构成证明。准入仍然先调用 Lean，重载时仍然使用当前后端重新编译；
篡改元数据或证明配方不会绕过内核。没有元数据的旧 `format: 1` 文件仍可读取，
重新保存会写成 `format: 2`。

## 检索、过期和冲突审计

`memory.active_lemmas(at=...)` 只返回没有过期、且满足调用方适用标签的引理。
适用标签只是检索过滤，应用仍须验证当前任务的前提。`memory.audit(at=...)` 返回
总数、活动数、过期数、带来源记录数和来源任务数，可用于持续运行中的退化监控。
过期只会阻止回忆，不会删除原始记录；需要撤销时应保留外部审计记录并重新生成
冻结库。

形式证明中“同一个命题有两条证明”不是矛盾，因而库不会擅自删除候选。领域适配器
应在进入库前定义规范化命题键，并对同键、不同结论或不同适用条件的候选报告冲突；
元数据本身永远不能消除冲突。

## 跨题迁移的可报告指标

评测脚本可把每个任务的 `task_id`、`solved` 和可选的 `recalled` 来源任务交给
`memory.transfer_report(rows)`。它只统计回忆数、回忆且解决数、迁移解决率和跨题
回忆数，不把“检索到规则”冒充因果增益。正式研究仍应与冻结空库、固定库和增长库
做配对比较，并报告旧任务回放、未见结构组合、调用数、token、时间和 95% 区间。

这组生命周期元数据补上了可信记忆的工程接口，但不代表已经完成长期跨域迁移。
要声称前沿，需要在未参与开发的 MiniF2F/LeanDojo 子集和完整 ChinaTravel 上，固定
同一底座模型与工具预算，发布可复现的冷启动、冻结记忆、增长记忆及撤销回放结果。
