"""Synthetic axial-bar Agent: reject, verify stress, then compare a declared limit.

Run ``python -m examples.engineering_agent`` from the source checkout.
Installed applications can import the same adapter from
``residual_agent.domains.engineering``. No design standard is implemented.
"""
from residual_agent.domains.engineering import AxialBarProblem, build_agent, run_demo

__all__ = ["AxialBarProblem", "build_agent", "run_demo"]


if __name__ == "__main__":
    print(run_demo().to_json())
