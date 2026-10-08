# How ResiMind is neuro-symbolic

[English home](../README.md) · [中文首页](../README.zh-CN.md)

ResiMind separates **model-generated proposals** from **independently checked facts**, then uses remaining obligations and verifier feedback to guide the next step. A real neural model can occupy the proposal slot; the default examples replace that slot with deterministic offline code so the whole loop runs without credentials.

## Implementation map

| Part | Concrete implementation | Responsibility |
| --- | --- | --- |
| Task orchestration | [`Agent.run`](../src/resimind/agent.py) | Collect registered evidence, select applicable reviewed routes, construct task adapters, return an audit result |
| Neural integration point | [`ModelProposer`](../src/resimind/adapters.py) | Call an injected text-generation callback; parse a constrained JSON candidate; expose actual verifier feedback to the next call |
| Symbolic mathematics | [`MathematicsVerifier`](../src/resimind/domains/mathematics.py) | Recompute exact rational normalization, solution, and original-equation checks; enforce prerequisites and evidence references |
| Symbolic engineering | [`AxialBarVerifier`](../src/resimind/domains/engineering.py) and [`units`](../src/resimind/units.py) | Check scope, declared assumptions, units and dimensions; recompute nominal stress and compare the supplied limit |
| Optimization certificates | [Optimization adapter](../src/resimind/domains/optimization.py) | Check exact LDLᵀ, feasibility, KKT, and a global-optimality polynomial certificate independently of active-set search |
| Continuous bridge | [Bridge adapter](../src/resimind/domains/bridge.py) | Check curvature, equilibrium, support conditions, rotation compatibility, and complete load-case envelopes |
| State transition | [`Engine`](../src/resimind/runtime.py) and [`Verdict`](../src/resimind/core.py) | Bind verification to candidate, state, and inputs; commit only verifier-produced facts after transition checks |
| Residual reconstruction | `Domain.rebuild(state)` | Derive outstanding goals, unknowns, and hard constraints from committed facts |
| Reviewed routes | [`RouteMemory`](../src/resimind/memory.py) | Reuse reviewed, applicable action sequences while requiring verification on current inputs |

Here, *symbolic* means explicit typed facts, rules, prerequisites, and exact or dimension-aware computation. It does not imply that the repository contains a universal formal logic or a theorem prover. The independent verifier is separate from the proposer, but it is still trusted application code that needs its own tests.

## Connect a real model

The reference adapters accept a callable with this contract:

```text
complete(prompt: str) -> str
```

Supply it as `build_agent(problem, complete=your_complete)`. Choose `QuadraticProgram(...)` or `ContinuousBridgeProblem(...)` for the headline examples; `LinearEquation(a, b, c)` and `AxialBarProblem(...)` are the smaller introductory adapters. Wrap your existing model client in `your_complete`, including its credentials, network timeouts, and resource limits. No model SDK is bundled.

The prompt includes task instructions, allowed actions, current facts, registered input evidence, residual obligations, the candidate JSON schema, and the most recent feedback. Return **one JSON object** containing only `id`, `action`, `target`, `claim`, and `refs`, or return the JSON literal `null` when there is no candidate. The domain supplies the allowed action names and exact claim format. For example, the first step of the bundled math demo can propose:

```json
{
  "id": "normalize-1",
  "action": "normalize_equation",
  "target": "mathematics:normalized",
  "claim": "3/2*x=2",
  "refs": ["equation:a", "equation:b", "equation:c"]
}
```

This proposal is only valid for the matching equation evidence. The verifier independently computes what the normalized equation must be. Extra fields such as `verified`, unknown actions, malformed JSON, and missing or mismatched references cannot authorize a commit. Returning a valid JSON object does not establish its mathematical or engineering correctness.

## What closes the loop

Let `S_k` be committed facts, `R_k` the remaining obligations, and `E` the task's input evidence:

```text
candidate = proposer(S_k, R_k, feedback)
verdict = verifier(candidate, S_k, R_k, E)
S_(k+1) = commit(S_k, verdict)       if accepted and transition checks pass
R_(k+1) = domain.rebuild(S_(k+1))
feedback = actual decision + reasons
```

A rejected candidate leaves facts and residual unchanged. Missing prerequisites can produce `defer`; `interrupt` stops the run. Completion requires no outstanding goal, unknown, or hard constraint according to the domain adapter. Step and stall budgets also stop runs that do not finish.

The math demo deliberately proposes `x=7/3`, receives `solution_claim_mismatch`, and only then proposes `x=4/3`. It must still check `5/3=5/3` by substitution. The engineering demo's offline proposer likewise changes its incorrect stress claim after receiving `stress_claim_mismatch`. These are executable feedback paths, not prewritten text traces. Their corrections are scripted for demonstration; they do not measure a model's ability to recover from mistakes.

“Residual” here means **unfinished reasoning obligations**, not a residual connection in a neural network or a numerical error norm. A domain may add explicit numerical error obligations, but the core does not infer them.

## What is and is not included

| Included | Not included |
| --- | --- |
| Provider-neutral callback for neural proposals | Trained weights or a neural training pipeline |
| Independent exact/rule-based reference verifiers | General natural-language proof or a universal theorem prover |
| Real decision feedback and residual reconstruction | An LLM success-rate, speed, token-saving, or reliability benchmark |
| Reviewed, revocable in-memory action routes | Learned weights, automatic knowledge distillation, or persistent memory |
| Interfaces for domain-specific verification | Ready-made CAS, SMT, proof-assistant, or simulation connectors |

The reference adapters cover rational linear equations, small strictly convex quadratic programs, declared uniform axial bars, and a two-span continuous beam under uniform load patterns. See the [optimization certificate](constrained-optimization.md) and [bridge model](continuous-bridge.md) for exact supported scopes. Engineering verifiers check declared assumptions; they cannot establish whether a real component satisfies them. A completed task may conclude that an equation has no solution or that the supplied limit is exceeded.

Model proposals, tool inputs, and verification are distinct trust boundaries. Correct checking of false real-world inputs still produces an inapplicable conclusion. Callback timeouts, isolation, input authenticity, and domain-model validity remain the integrator's responsibility. See [architecture](architecture.md) and [domain contract](domain-contract.md) for the full contract.

## 中文说明

ResiMind 的神经符号体现在三个可检查的层次：

1. **神经提议入口：** `ModelProposer` 接受实际模型的 `complete(prompt) -> str` 回调；默认示例用确定性的离线逻辑替代它，不携带训练权重。
2. **符号验证实现：** 数学适配器检查精确方程与优化证书；工程适配器检查前提、量纲、应力或连续梁的平衡、协调及工况完整性。正式事实由验证器产生。
3. **残差反馈闭环：** 未完成的目标、未知项和硬约束从正式事实重建；拒绝原因真实返回下一轮提议。得到候选答案并不等于验证义务全部完成。

领域构建器支持 `build_agent(problem, complete=your_complete)`，接入方将现有模型 SDK 包成回调即可。回调输出需符合当前任务声明的动作、目标、claim 格式与证据引用，不能凭 `verified` 字段自行通过验证。

准确的定位是**可注入神经模型的神经符号 Agent 架构**，不是已训练的通用神经符号模型。默认纠错路径是为了展示机制而脚本化实现的，不代表真实模型表现；仓库没有外部模型性能测评，也没有通用数学证明或完整工程设计能力。更多中文说明见[架构](architecture.md)与[领域契约](domain-contract.md)。
