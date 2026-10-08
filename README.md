# Residual Agent

**让模型提议，让验证器决定，让残差驱动下一步。**

面向**数学、工程和其他结构化任务的通用神经符号 Agent 架构**。共享任务、工具取证、模型提议、独立验证、残差执行循环和经审路线记忆；各领域接入自己的规则与计算工具。来自「桥梁医生」的实践，以零运行时依赖的 Python 框架独立实现。

[English](README.en.md) · [架构与信任边界](docs/architecture.md) · [工程与数学适配](docs/domain-contract.md) · [扩展接口](docs/adapters.md) · [来源与范围](docs/provenance.md)

> **v0.2.0 / 实验阶段。** 这是验证门控的编排框架。领域验证器的质量决定了结论的质量；它不是任意自然语言的自动证明器。离线示例使用确定性提议器，不需要模型账号。

## Agent 架构

```mermaid
flowchart TD
    T[Task 任务与上下文] --> A[Agent 编排器]
    A --> ET[EvidenceTool 工具取证]
    ET --> E[类型化输入证据]
    A --> R
    E --> V[独立领域验证器]
    R[残差：目标 / 未知项 / 硬约束] --> P[提议器：规则 / 检索 / 工具 / 模型]
    P --> C[候选动作与证据引用]
    C --> V
    V --> D{四级决策}
    D -->|accept| COMMIT[校验绑定并原子提交事实]
    D -->|reject / defer| R
    D -->|interrupt| H[停止并交接]
    COMMIT --> B[领域适配器重算残差]
    B --> R
    COMMIT --> OUT[AgentResult 与结构化审计记录]
    M[经审核且满足适用条件的路线记忆] --> P
```

「残差」指当前还没有完成的义务：需要回答的目标、尚缺的信息，以及未满足的硬约束。每一步都从实际提交的事实重算残差。候选不能靠自称“已验证”或“已完成”减少残差。

验证结果绑定候选内容、状态和输入证据。正式事实只能来自验证器的输出；模型提议本身不会自动成为事实。记忆保存经过审核的动作路线，复用时仍须根据当前证据重新验证。

## 快速开始

需要 Python 3.10 或更新版本。在本仓库根目录运行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m examples.mathematics_agent
python -m examples.engineering_agent
python -m examples.agent_demo
python -m examples.inventory
python -m examples.document_review
python -m examples.route_memory
python -m pytest -q
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活环境。运行时不依赖任何第三方包；安装工具和测试工具可能需要下载。也可以跳过安装，用 `PYTHONPATH=src python -m examples.inventory` 运行示例（POSIX shell）。

数学示例运行精确方程的“归一化 → 求解 → 回代”，工程示例运行“量纲与前提检查 → 应力计算 → 限值比较”。错误候选会被拒绝，拒绝原因反馈给下一轮提议。默认使用离线提议器，可通过 `complete(prompt) -> str` 接入实际模型。

同一个任务和结果接口，可以调用不同领域适配器：

```python
from residual_agent import Task
from residual_agent.domains.mathematics import LinearEquation, build_agent as math_agent
from residual_agent.domains.engineering import AxialBarProblem, build_agent as engineering_agent

jobs = [
    (math_agent(LinearEquation(2, 3, 11)),
     Task("math-1", "求解并回代验证 2*x+3=11", "mathematics")),
    (engineering_agent(AxialBarProblem(
        force_value=100, force_unit="kN",
        area_value=1000, area_unit="mm2",
        allowable_stress_value=150, allowable_stress_unit="MPa",
        axial_static=True, is_uniform=True, no_local_effects=True,
    )), Task("engineering-1", "核算合成杆件名义应力并比较给定限值", "engineering")),
]
for agent, task in jobs:
    result = agent.run(task)
    print(result.run_result.status)
    print(result.to_json())
```

这两个适配器随安装包提供。数学当前支持精确有理数的一元一次方程；工程当前支持静态轴向等截面杆的名义应力模型，示例限值是合成输入。`solved` 表示任务的验证义务完成；结论仍可能是“无解”或“超限”。

## 可以看到什么

| 示例 | 提议与验证 | 残差的作用 |
| --- | --- | --- |
| 数学 Agent | 精确有理数归一化、求解、回代与退化情形核验 | 推导和回代义务都完成才收口 |
| 工程 Agent | 检查模型前提、量纲、应力公式与输入限值 | 缺前提保留残差，超限作为已核验结果 |
| 完整 Agent | 任务 → 工具 → 模型回调 → 验证 → 已核验结果 | 错误提议被拒绝后继续执行 |
| 库存计算 | 从合成库存证据重新计算，拒绝错误候选 | 只有结果核验通过才能完成目标 |
| 资料完整性 | 核对同一对象、同一范围的所需资料 | 缺资料保留未知项，不伪造完整结论 |
| 路线记忆 | 待审不可检索，审核后可提示动作，撤销后不可用 | 路线只指导步骤，当前事实重新计算 |

## 扩展到自己的领域

由 `Agent` 装配 `Task`、`EvidenceTool`、可选 `RouteMemory`，并为任务创建三个扩展接口：

1. `Proposer.propose(...)`：根据已提交状态和残差提出候选。可以接模型、规则或检索服务。
2. `Verifier.verify(...)`：独立核对证据、对象、单位、适用范围和领域规则，返回四级决策及可提交事实。
3. `Domain.rebuild(...)`：从实际事实重建未完成义务，决定是否完成。

从 [数学 Agent](examples/mathematics_agent.py) 或 [工程 Agent](examples/engineering_agent.py) 开始，阅读 [领域契约](docs/domain-contract.md) 与 [适配器指南](docs/adapters.md)。模型适配器只是候选生产者；不要把模型自报的正确性当成验证结果。

## 已实现与边界

- `Agent.run(Task)` 任务编排、注册工具取证、可注入模型回调和结构化结果。
- 不可变数据契约、证据引用检查、验证绑定、冲突检查和事务提交。
- `accept / reject / defer / interrupt`、轮换提议器、尝试次数与无进展停止策略。
- JSON 运行记录和明确的停止原因。
- 进程内路线记忆，带适用条件、审核记录和撤销。
- 可复用的数学、工程适配器，以及基于 `Fraction` 的单位换算与量纲运算组件。

工具在任务开始时采集证据；循环内动态取证、自动多角色规划尚未实现。

不包含模型权重、托管推理、任意代码执行、CAS 证明、自动知识蒸馏、持久化数据库或分布式运行。没有 token、工具费用或强制墙钟限时保障；阻塞的回调须由调用方设置超时与进程隔离。哈希绑定用于发现串用结果，不能认证恶意验证器。

本仓库仅包含通用实现和合成示例，不包含原应用的业务档案、客户数据、账号凭据、模型运行日志或比赛材料。原始输入的真实性仍需由接入方保证。详细限制见 [架构说明](docs/architecture.md)。

## 贡献与许可

欢迎增加新的领域适配器与验证失败测试，见 [CONTRIBUTING.md](CONTRIBUTING.md)。本项目采用 [MIT License](LICENSE)。
