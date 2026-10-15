# 外部垂域评测预检

这份预检把 ResiMind 当作**垂域 Agent 运行时**来评估：底模冻结，比较同一个底模接入 ResiMind 前后的增益。它不会把缺少数据、适配器或密钥的项目记成 0 分，也不会把受限 Lean fixture 的通过率冒充 MiniF2F 或 LeanDojo 成绩。

## 公平协议

三个域都固定模型、温度、思考设置、提示词接口、工具权限、题目划分、调用上限、token 上限、超时和随机种子。数学题的主指标是最终 Lean 编译通过率、错误接受率、未解决/超时、证明长度、调用数、token 和墙钟时间；规划题的主指标是官方评分器的最终计划成功率、约束违规率、拒答率、调用数、token 和墙钟时间。冷记忆和已核验热记忆分别报告，测试题或测试证明不得提前进入记忆。

三条臂为：

1. `base_same_model`：冻结底模的直接 Agent；
2. `resimind_cold`：接入 ResiMind，但不带题外规则；
3. `resimind_verified_warm`：只使用在此前训练/开发题上经过独立验证的规则，并重新核验后使用。

每题输出一条 JSONL 审计记录：`instance_id`、`status`、`proof_or_plan_valid`、`verification_trace`、`calls`、`tokens`、`wall_seconds`、`memory_mode`。最终比较使用配对差值和 bootstrap 95% CI；不把 MiniF2F、LeanDojo、ChinaTravel 的分数合成为一个总分。

## 当前预检事实

运行：

```sh
PYTHONPATH=src:. python -m benchmarks.external_benchmark_preflight \
  --output /tmp/resimind-external-preflight.json
```

当前仓库有 ChinaTravel 的官方适配器和评分器，但完整 154 题运行仍需要固定版本的数据、官方 sandbox、macOS `sandbox-exec`、本机 `DEEPSEEK_API_KEY`，以及 154 题 × 3 条件的 live 预算。MiniF2F 和 LeanDojo 现在有一个**实验性 manifest/编译适配器**（`benchmarks.external_theorem_adapter`）：调用方先准备带有唯一 `RESIMIND_PROOF_HOLE` 的可信 `.lean` 模板，再用已安装的指定 Lean 编译候选证明。API 在提供 `source_sha256` 时校验文件哈希；当前该字段可省略，`dataset_commit` 和 `mathlib_commit` 也只是调用方提供的版本声明，尚未与 checkout 的实际 Git revision 比对。正式评测协议要求逐文件哈希和实际版本核对，不能仅凭这些声明视为已经完成。

顶层命令的 `ready` 仅表示资源路径、可执行文件和密钥存在，不验证 manifest、实际数据集版本或 Mathlib 导入环境。`TheoremManifest.preflight()` 进一步检查模板、已提供的哈希和 Lean 版本，其 `ready` 仍不代表完整外部评测环境已经验收。适配器不下载数据、不解析任意 Lean 声明，也不提供不可信 Lean 源码的安全沙箱。没有经过独立核对的数据和项目环境时，评测结论仍是 `not_run/blocked`，不是 0 分。

准备好一个固定 checkout 后，可用以下方式做无模型预检（示例只说明接口，不包含外部数据）：

```python
from benchmarks.external_theorem_adapter import ExternalLeanCompiler, TheoremManifest

manifest = TheoremManifest.load("/path/to/manifest.json")
report = manifest.preflight("/path/to/pinned/checkout", lean="/path/to/lean")
assert report["status"] == "ready"
# This checks the adapter's narrow preflight only. Independently verify the
# checkout revisions, every source hash, and the project's import environment.
result = ExternalLeanCompiler(lean="/path/to/lean").compile(
    manifest.theorems[0], "/path/to/pinned/checkout", "by ...",
    project_root="/path/to/pinned/checkout",
)
```

只有 `status == "verified"` 的编译结果才可计入证明通过；缺文件、版本不匹配、哈希不匹配和 `sorryAx` 都单独记录为阻塞或未解决，不折算成题目失败。

manifest 的最小条目如下（`source` 文件必须只出现一次 `RESIMIND_PROOF_HOLE`）：

```json
{
  "schema": "resimind.external-theorem-manifest.v1",
  "dataset": "minif2f",
  "dataset_commit": "<frozen commit>",
  "lean_version": "4.29.0",
  "mathlib_commit": "<frozen commit>",
  "theorems": [{
    "id": "test/example",
    "split": "test",
    "source": "prepared/test/example.lean",
    "theorem": "example",
    "statement": "True",
    "source_sha256": "<64 lowercase hex chars>"
  }]
}
```

最新公开的 [Bridge Doctor 配置正式 12 题结果](evidence/chinatravel-same-model-bridge-doctor-v1.zh-CN.md) 为：官方 ReAct `0/12`、官方 NeSy `3/12`、ResiMind-ReAct `0/12`。它说明当前集成还没有显示出外部求解优势，不能外推到完整 154 题。其他记录中的 `5/12`、`1/12` 属于此前另一轮实验，不能与本轮合并。此前的本地数学 fixture 和 Lean 组合测试证明的是验证层可靠性，不是 MiniF2F/LeanDojo 排名。

## 解锁条件

- **MiniF2F**：固定题集和 Lean/Mathlib commit；用 manifest 适配器准备 goal-state、证明候选、完整编译和错误记录；先跑 50 题开发集，冻结后跑 test。
- **LeanDojo**：固定 LeanDojo 环境、定理语料和检索 API；将检索到的定理转换成带 proof hole 的固定源文件，禁止测试定理/证明污染记忆；同时报告检索命中率和最终 kernel success。
- **ChinaTravel**：沿用官方 scorer，先冻结完整 154-query manifest，再按同一 DeepSeek 配置跑三个条件。全量最坏是 462 个任务运行，每题 600 秒和 128 请求上限，必须记录实际成本并保留失败题位。

公开系统的论文数字不能当作同模型对照：例如 ReProver 在 MiniF2F-test 报告 26.5%，DeepSeek-Prover-V2 报告 88.9%，它们的模型、训练数据、搜索和预算都不同。公平结论应写成“architecture gain over a frozen base model”，而不是“超过这些模型”。
