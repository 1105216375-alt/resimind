# Exact constrained optimization: a certificate, not just a number

This showcase solves a coupled three-variable quadratic program with an equality,
nonnegativity constraints, and an active upper bound. The Agent rejects a plausible
stationary point, responds to the verifier's feedback, and finishes with a global
optimality identity checked over exact rational arithmetic.

```bash
python -m examples.constrained_optimization
python -m examples.constrained_optimization --json
```

The default proposer is a deterministic active-set search used to make the example
reproducible offline. Supply `complete(prompt: str) -> str` to use a language model.
The verifier and proof obligations are the same in both cases. This example does
not benchmark a neural model or claim that a model discovered a new theorem.

## The problem

Minimize

$$
f(x)=2x_1^2+x_1x_2+x_2^2+x_3^2-8x_1-3x_2-3x_3
$$

subject to

$$
x_1+x_2+x_3=3,\qquad x_1,x_2,x_3\geq0,\qquad x_1\leq1.
$$

The objective is `x.T @ Q @ x / 2 + c.T @ x`, with

```text
Q = [[4, 1, 0],     c = [-8, -3, -3]
     [1, 2, 0],
     [0, 0, 2]]
```

Solving only the equality-constrained stationarity equations gives
`x = (26/15, 1/5, 16/15)`. It violates `x1 <= 1`. A stationary point of a relaxed
problem is insufficient for the original problem, even if its objective looks
better. The verifier rejects this proposal and commits no primal or dual facts.

The corrected result is

$$
x^*=(1,\;3/4,\;5/4),\qquad f(x^*)=-73/8.
$$

The equality multiplier is `lambda = 1/2`. For inequalities ordered as
`-x1 <= 0, -x2 <= 0, -x3 <= 0, x1 <= 1`, the multipliers are
`mu = (0, 0, 0, 11/4)`. Only the upper bound has a positive multiplier.

## What the Agent must prove

Three actions discharge three obligations:

| Action | Independent verifier checks | Committed facts |
| --- | --- | --- |
| `certify_convexity` | Exact `Q = L diag(d) L.T`, unit lower-triangular `L`, every `d_i > 0` | Positive definiteness |
| `certify_primal_dual` | `Ax=b`, `Gx<=h`, exact objective/slacks, `mu>=0`, stationarity and complementary slackness | Primal and dual witnesses **together** |
| `certify_global` | All quadratic, linear and constant coefficients of a sum-of-squares gap identity | Explicit global optimality certificate |

The primal and dual witnesses commit atomically. A feasible but nonoptimal
candidate is rejected before it can lock the state to an unusable point. Its
feedback is available to the next proposal. After an accepted KKT witness, the
requested global proof artifact remains an outstanding obligation.

The default trace is:

```text
ACCEPT certify_convexity     positive_definiteness_verified      remaining=2
REJECT certify_primal_dual   primal_inequality_violation         remaining=2
ACCEPT certify_primal_dual   primal_and_kkt_verified              remaining=1
ACCEPT certify_global       global_gap_identity_verified        remaining=0
```

The verifier never calls the active-set solver or compares proposals against a
stored solution. It accepts algebraically valid alternative certificates, such
as rescaling a squared expression and compensating its positive weight. The
residual builder revalidates the committed certificate chain and its provenance.

## An explicit global proof

For any `x`, the checked identity is

$$
\begin{aligned}
f(x)+\frac{73}{8}
={}&2\left(x_1+\frac{x_2}{4}-\frac{19}{16}\right)^2
+\frac78\left(x_2-\frac34\right)^2
+\left(x_3-\frac54\right)^2\\
&+\frac{11}{4}(1-x_1)
+\frac12(3-x_1-x_2-x_3).
\end{aligned}
$$

On the feasible set, the equality term vanishes and the bound term is
nonnegative. Every square is nonnegative, so `f(x) >= -73/8`. At `x*`, all terms
vanish. The verified positive-definite Hessian establishes uniqueness.

The general certificate format is

$$
f(y)-f(x^*)=\sum_i w_i(v_i^Ty+t_i)^2
+\mu^T(h-Gy)+\lambda^T(b-Ay),\qquad w_i>0.
$$

The implementation expands this polynomial using `Fraction`; there is no
floating-point tolerance or sampled numerical check.

For background on convex quadratic programs and sufficiency of KKT conditions,
see Boyd and Vandenberghe, [*Convex Optimization*, §§4.4 and 5.5](https://web.stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf).
The concrete identity above is derived for this repository's synthetic problem.

## Reuse with another problem

```python
from resimind import Task
from resimind.domains.optimization import DOMAIN, QuadraticProgram, build_agent

problem = QuadraticProgram(
    q=((2, 0), (0, 2)),
    c=(-8, -2),
    a=((1, 1),), b=(2,),
    g=((-1, 0), (0, -1)), h=(0, 0),
)
result = build_agent(problem).run(Task("two-variable-qp", "Prove the minimum.", DOMAIN))
assert result.run_result.status == "solved"  # x=(2,0), objective=-12
```

To use a real model, pass `build_agent(problem, complete=my_model_callback)`.
The prompt includes the registered evidence, certificate schemas, current state,
remaining obligations, and the previous verdict. Return standard candidate JSON;
its `claim` is a JSON-encoded object:

```json
{
  "primal": {"x": ["1", "3/4", "5/4"], "objective": "-73/8", "slack": ["1", "3/4", "5/4", "0"]},
  "dual": {"lambda": ["1/2"], "mu": ["0", "0", "0", "11/4"]}
}
```

Every proposal cites all six original input evidence IDs and the prerequisite
certificate fact IDs. Input evidence is bound by value, subject, scope, units and
source, so coefficients from another problem cannot authorize these facts.

## Scope

- Exact rational, symmetric, strictly positive-definite quadratic objectives.
- One to six variables, at most `n` equality constraints and eight inequalities.
- Inputs accept integers, `Fraction`, or rational strings; floats and booleans
  are rejected. Rational text and canonical input representations are limited to
  256 characters; claim JSON is limited to 16,384 characters.
- The bundled proposer enumerates active sets and solves rational linear systems.
  It assumes independent equality rows; it does not classify infeasibility,
  handle indefinite or semidefinite objectives, or optimize large sparse models.
- `stalled` means the proposer failed to provide a certificate. It is neither an
  infeasibility proof nor permission to use an unverified optimum.
- Python verifier extensions are trusted code. Exact arithmetic and content
  binding do not provide a sandbox or certify arbitrary statements.

Tests include constraint violations, negative multipliers, complementary-slackness
failures, altered coefficients and provenance, skipped prerequisites, invalid
convexity factors, alternate valid square certificates, multiple other QPs, and
feedback-driven recovery from a feasible but nonoptimal proposal.
