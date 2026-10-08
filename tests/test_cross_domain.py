"""One installed Agent contract supports different verification semantics."""
import json

from resimind import Agent, AgentResult, Task
from resimind.domains.mathematics import LinearEquation, build_agent as math_agent
from resimind.domains.engineering import AxialBarProblem, build_agent as engineering_agent


def bar(**values):
    return AxialBarProblem(axial_static=True, is_uniform=True,
                           no_local_effects=True, **values)


def test_same_agent_contract_drives_mathematics_and_engineering():
    jobs = (
        (math_agent(LinearEquation(2, 3, 11)),
         Task("math-cross-domain", "Solve and check", "mathematics")),
        (engineering_agent(bar()),
         Task("engineering-cross-domain", "Compute and compare", "engineering")),
    )
    for agent, task in jobs:
        assert type(agent) is Agent
        result = agent.run(task)
        assert type(result) is AgentResult
        assert result.run_result.status == "solved"
        assert result.run_result.residual.solved
        assert any(event.decision.value == "reject" for event in result.run_result.trace)
        assert sum(event.decision.value == "accept" for event in result.run_result.trace) >= 2
        assert json.loads(result.to_json())["task"]["domain"] == task.domain


def test_complete_analysis_can_report_no_solution_or_exceeded_limit():
    no_solution = math_agent(LinearEquation(0, 1, 2)).run(
        Task("inconsistent-equation", "Classify the solution set", "mathematics")
    )
    overload = engineering_agent(bar(allowable_stress_value=50)).run(
        Task("exceeded-limit", "Compare against the supplied limit", "engineering")
    )
    assert no_solution.run_result.status == overload.run_result.status == "solved"
    mathematical_answer = next(f for f in no_solution.run_result.state.facts
                               if f.metric == "solution")
    engineering_answer = next(f for f in overload.run_result.state.facts
                             if f.metric == "within_allowable_stress")
    assert mathematical_answer.value == ("no_solution",)
    assert engineering_answer.value is False


def test_adapters_reject_tasks_for_another_domain_before_reasoning():
    result = math_agent(LinearEquation(2, 3, 11)).run(
        Task("wrong-domain", "Compute", "engineering")
    )
    assert result.run_result.status == "error"
    assert result.run_result.state.facts == ()
    assert not result.run_result.residual.solved
