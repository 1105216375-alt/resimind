# 发布验证记录

本地验证日期：2026-10-08。版本：**0.6.0**（ResiMind）。[v0.5.0 历史记录](validation-v0.5.0.md)包含此前数学、桥梁和模型接入的详细验收范围。

## 自动化与安装验证

- Python 3.12：**558 项测试通过**；其中既有 454 项、客服领域 91 项、新增安装式 CLI 检查 6 项、客服示例与跨领域 LangGraph 检查 7 项。
- 构建 `resimind-0.6.0-py3-none-any.whl`，在独立环境安装后确认导入来自 site-packages，再用 `-o pythonpath=.` 运行完整 **558 项测试，全部通过**。
- 另一独立虚拟环境仅安装 pip 与 ResiMind，没有 OpenAI SDK、LangGraph 或其他运行时依赖。在仓库外使用 `python -m resimind` 和 `resimind` 两个入口执行全部三个客服场景，退出码和输出符合预期；原有数学与桥梁 JSON 演示也成功。
- 可选集成验收版本保持为 OpenAI Python SDK 2.54.0、LangGraph 1.2.14、HTTPX 0.28.1、pytest 8.4.2。新增测试以真实 LangGraph 子图运行客服领域：正常退款与超期复核完成，缺配送证据进入 `needs_review`。
- 双语首页、LangGraph 和客服文档中的五段离线 Python 示例在安装包上运行成功；客服真实模型代码段只额外做语法检查，没有把它作为新的联网尝试执行。
- 全部 Python 源码通过 3.10 语法解析，本地 Markdown 链接有效。实际运行环境为 Python 3.12，语法解析不代表其他 Python 版本已运行。

## 客服规则与未完成任务

默认场景使用商品实付 249 元、运费 10 元的合成订单，以及包含首尾日期的虚构 14 天商家规则。

| 场景 | 实际结果 |
| --- | --- |
| 正常申请 | 接受资格核验 → 拒绝包含运费的 259 元候选 → 接受 249 元报价 → 核验处理建议 |
| 缺签收日期 | 连续延后，保留 `return:delivery_date`，没有完整处理建议 |
| 超出示例期限 | 完成 `human_review` 路由建议，退款金额为空 |

客服测试覆盖期限边界、已退款金额、零金额、错订单和客户标识、错政策和证据、金额类型混淆、无效日期、缺少前置事实、篡改事实、额外事实污染和伪造付款执行标记。状态与输出必须能由声明的快照和规则重新核验。独立只读评审另做了 126 次边界与篡改检查，未计入上面的测试总数。

ID 对比只检查输入一致性，业务接入仍须先完成身份与权限校验。`solved` 表示处理建议核验完成，包括转人工建议；没有实际付款、发消息或承诺到账。这些规则是示例商家政策，不是法律判断。

## 一次真实 DeepSeek 客服运行

配置模型 `deepseek-flash`，使用同一合成退款场景，实际调用 **5 次**：资格核验通过后，两次提议因缺少快照引用被拒绝；模型补齐引用，退款金额与处理建议随后通过。结果为 **24900 分**，运费排除，`payment_executed=false`。

接口报告输入 16,987 token、输出 5,884 token，合计 22,871；请求设置为最多 8 次调用、每次 4,096 输出 token、90 秒 SDK 网络超时。完整记录、两次拒绝及复现命令见[真实客服运行审计](evidence/customer-support/README.md)。此次只有一次联网尝试，没有离线答案兜底，也不将单次成功视为准确率评测。

该客服记录与此前三份优化记录均使用发布版独立验证器离线重放，完整轨迹、事实、残差、停止原因和证据一致。重放检查验证行为，不提供第三方来源认证。OpenAI 路径仍仅经过真实 SDK 的模拟 HTTP 测试，没有新增真实 OpenAI 请求。

## 复现

```bash
python -m pip install '.[dev,deepseek,langgraph]'
python -m pytest -q -o pythonpath=.
python -m resimind demo --domain customer-support
python -m examples.customer_support --scenario expired --json
# 以下任务保留缺失信息，预期退出码为 1：
python -m examples.customer_support --scenario missing-delivery --json
python tools/replay_live_audits.py
```

[GitHub Actions 配置](github-actions-tests.yml)仍为模板，当前没有启用远程 CI。本记录反映本地实测，不表示生产环境验收或第三方认证。
