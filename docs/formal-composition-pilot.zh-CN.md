# 条件引理的残差组合：开发试验

这是在一般 Lean 目标试验之上的一个小步升级。旧版只能把一条已核验定理作为
整条目标的直接回忆；本版允许把它当作**带前提的条件规则**，在当前目标中
产生残差子目标，再用另一条规则或局部假设完成它。

## 做了什么

`resimind.integrations.formal.solve` 新增两个有界参数：

```python
result = solve(
    target,
    backend,
    memory=memory,
    recipes=(Recipe("simp"),),
    composition_depth=3,
    composition_budget=64,
)
```

规划器只输出四种类型化节点：

1. `intro`：把目标 `P → Q` 的 `P` 放入局部假设；
2. `recall`：把已核验的条件引理应用到当前残差；
3. `assumption`：使用当前局部假设；
4. `recipe`：在叶子调用白名单中的固定配方。

应用前先做类型化结构匹配，应用后得到的每个前提都必须继续进入有界搜索。
规划器不会把匹配结果当作答案；`FormalBackend.check_plan` 生成整棵安全的 tactic
树，复用版本、资源、公理摘要和 `Proof.olean` 产物检查。只有这次独立编译通过，
`solve` 才会返回 `verified`。

组合也有明确边界：最多 32 条记忆、深度 8、访问节点 256；不接受任意 Lean
文本、自由变量或无法由结论实例化的前提。`composition_depth=0` 是旧版固定
配方/整条回忆消融。

## 一个可检查的链

把两条已核验命题记为：

```text
P → (P ∨ Q)
(P ∨ Q) → (Q ∨ P)
```

目标为 `P → (Q ∨ P)`。在只给 `simp` 的弱配方消融中，直接目标未决；组合规划
会产生：

```text
intro h0
apply memory_1
  apply memory_0
    exact h0
```

这段展示的是**残差闭环**，不是把 `simp` 当成强基线后宣称胜出。对照必须同时
运行相同目标上的 `grind`，并记录完成率、未知、Lean 编译调用、运行时间、记忆
重验成本和计划深度。受限配方的成功只能说明组合实现工作，不能转化为 MiniF2F、
LeanDojo 或任何国际排行榜成绩。

## 当前证据与下一道硬门槛

新增回归覆盖了两条引理的组合、缺失中间引理、深度限制和完整计划的 Lean 重验。
现有开发题仍是人工构造的小型 Prop/Nat AST，`grind` 能直接解决其中许多题，
因此没有把这组链写成“击败强自动化”的结果。

可复现的单链诊断（输出放在仓库外）如下：

```bash
PYTHONPATH=src python -m benchmarks.run_formal_composition_pilot \
  --output /absolute/path/outside/repository/formal-composition.json
```

在本机 Lean 4.29.0 上，四行的状态分别是：强 `grind` 空记忆 `verified`，弱
`simp` 空记忆 `unresolved`，弱 `simp` 加组合 `verified`，强 `grind` 加组合
`verified`。这是 1 条人工构造链，不是外部题集成绩；首次 Lean 进程可能包含
工具链启动/缓存成本，不能用这四行推断速度优势。

要谈前沿位置，下一步必须冻结未参与开发的 Lean 4 题集（例如 miniF2F Lean 4
或 LeanDojo 的合法子集），固定同一模型、工具、时间和 token/编译预算，加入
无记忆、强搜索、条件组合和验证重试四组，并报告所有训练和重验开销。还需要
让规则从训练证明中自动抽象前提，检查长期序列上的新结构迁移和遗忘，当前仓库
尚未完成这些外部证据。
