"""Run the packaged exact rational-equation Agent with an offline callback.

From the repository root: ``python -m examples.mathematics_agent``.
The example rejects a wrong solution, uses feedback to correct it, and then
checks substitution. See ``residual_agent.domains.mathematics.build_agent``
for injecting an actual provider's ``complete(prompt)`` callback.
"""

from residual_agent.domains.mathematics import LinearEquation, build_agent, run_demo

__all__ = ["LinearEquation", "build_agent", "run_demo"]


if __name__ == "__main__":
    print(run_demo().to_json())
