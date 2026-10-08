# Continuous bridge: a structural reasoning certificate

**Two unequal spans. Different stiffnesses. Eight load cases. A wrong structural idealization that passes equilibrium—and fails compatibility.**

This example models a synthetic bridge girder as a continuous Euler–Bernoulli line beam. It tests whether an Agent can keep the structural model, evidence, load patterns, exact calculations, and completion criteria consistent. The independent verifier accepts a polynomial certificate only after equilibrium, curvature, support displacement, and pier rotation continuity all hold.

中文说明：这是 **24 m + 30 m 不等跨连续梁桥示例**，包含两跨不同刚度、恒载与活载分跨布置、两组自定义组合系数、支反力、墩顶负弯矩、跨内正弯矩极值、跨中挠度和组合包络。Agent 首先错误地把连续梁按两根简支梁计算；虽然静力平衡成立，但墩顶转角不连续，因此被拒绝。收到具体错误反馈后才重新求解。此处桥梁、荷载和限值均为合成数据，不是实际桥梁承载能力鉴定。

![Continuous bridge load patterns and verified moment envelopes](assets/continuous-bridge.svg)

## Run it

From the installed source checkout:

```bash
python -m examples.continuous_bridge
python -m examples.continuous_bridge --json
```

The first command prints a compact decision trace and engineering results. `--json` emits the evidence, every proposal and verdict, committed facts, and remaining obligations. Both run offline without a provider key.

```text
ACCEPT bridge:model: declared_model_and_dimensions_verified
REJECT bridge:case:baseline:none: pier_rotation_continuity_failed
ACCEPT bridge:case:baseline:none: equilibrium_curvature_supports_and_continuity_verified
... seven more distinct load cases accepted ...
ACCEPT bridge:envelope: envelope_verified
ACCEPT bridge:comparisons: comparisons_verified
Status: solved; remaining obligations: 0
```

The correction is triggered by the actual rejection event. Repeating the proposal without delivering that feedback repeats the same error. A rejected candidate changes neither the committed state nor the residual.

## Declared bridge and load cases

The beam has supports A–B–C with lengths 24 m and 30 m. Vertical displacement is restrained at each support; end moments are zero. The beam is continuous across B, so both span rotations at B must agree. The supports are **bilateral vertical restraints**: this mathematical model permits a negative reaction. Such a result is reported as uplift demand, not silently clamped to zero.

| Input | Span AB | Span BC |
|---|---:|---:|
| Length | 24 m | 30 m |
| Flexural rigidity, EI | 36 × 10⁹ N·m² | 48 × 10⁹ N·m² |
| Declared dead load | 35 kN/m | 35 kN/m |
| Declared live-load intensity | 25 kN/m | 25 kN/m |

The declared dead load is the total supplied permanent line load. The adapter does not estimate self-weight from unprovided section geometry or density. EI is constant within each span and may differ between spans.

| Synthetic combination | Dead factor | Live factor |
|---|---:|---:|
| `baseline` | 1 | 1 |
| `amplified` | 1.2 | 1.5 |

**These factors are example inputs, not factors taken from a design standard.** Every combination requires the live-load patterns `none`, `left`, `right`, and `both`. Including `none` preserves the permanent-load response and permits reaction-envelope comparisons. Omitting any one case leaves the analysis unresolved.

The supplied comparison limits are 9000 kN·m for the largest absolute bending moment and 40 mm for each absolute **midspan** displacement. The same supplied limits apply to every declared combination in this example. These are arithmetic comparisons, not resistance calculations or serviceability checks under a named bridge code.

## Verified results

| Combination:pattern | Pier moment B, kN·m | Reaction A, kN | Reaction B, kN | Reaction C, kN |
|---|---:|---:|---:|---:|
| `baseline:none` | −3205.89 | 286.42 | 1185.44 | 418.14 |
| `baseline:left` | −4134.92 | 547.71 | 1555.12 | 387.17 |
| `baseline:right` | −4566.77 | 229.72 | 1662.51 | 747.77 |
| `baseline:both` | −5495.81 | 491.01 | 2032.19 | 716.81 |
| `amplified:none` | −3847.06 | 343.71 | 1422.53 | 501.76 |
| `amplified:left` | −5240.61 | 735.64 | 1977.05 | 455.31 |
| `amplified:right` | −5888.40 | 258.65 | 2138.13 | 996.22 |
| `amplified:both` | −7281.94 | 650.59 | 2692.65 | 949.77 |

| Envelope result | Value | Governing case |
|---|---:|---|
| Most negative pier moment | −7281.94 kN·m | `amplified:both` |
| Span AB maximum sagging moment | 3403.57 kN·m at x = 9.253 m from A | `amplified:left` |
| Span BC maximum sagging moment | 6241.85 kN·m at x = 17.469 m from B | `amplified:right` |
| Span AB largest absolute midspan displacement | 4.299 mm | `amplified:left` |
| Span BC largest absolute midspan displacement | 10.568 mm | `amplified:right` |

Full live loading controls the pier hogging moment, but **partial live loading controls the two span sagging maxima and midpoint displacements**. A single `both` case misses those envelopes. The moment extrema are obtained analytically from endpoints and in-span zero-shear locations; they are not values sampled only at the midpoint.

The displacement rows are envelopes **at the two fixed midspan locations**, not envelopes of the maximum displacement anywhere along each span. Finding the global displacement extrema requires solving for the stationary points of the quartic displacement field; this adapter does not claim that check.

All values remain `Fraction`-exact until display. For example, the governing pier moment is `-225740250/31` N·m, and the two absolute midpoint displacement envelopes are `133281/31000000` m and `1677321/158720000` m. The displayed table is rounded only for reading.

## What the verifier proves

The candidate solver uses the three-moment relation for unyielding supports, zero outer moments, and constant EI within each span:

$$
M_B=-\frac{q_1L_1^3/EI_1+q_2L_2^3/EI_2}{8(L_1/EI_1+L_2/EI_2)}.
$$

Continuity is central to this force-method formulation; see the [NPTEL three-moment lecture](https://archive.nptel.ac.in/content/storage2/courses/105101085/Slides/Module-5/Lecture-4/5.4_2.html). The implementation's verifier **does not call this solver or compare with its output**. It checks the submitted fields against governing equations instead.

For each span, the certificate gives

$$
v(x)=c_1x+c_2x^2+c_3x^3+c_4x^4,\qquad
M(x)=M_\mathrm{left}+V_\mathrm{left}x-\frac{qx^2}{2}.
$$

Sign conventions are downward-positive load, upward-positive displacement and reactions, and sagging-positive moment. Thus `EI v'' = M`. This is the equivalent upward-displacement version of the Euler–Bernoulli relations described in the [TU Delft beam workbook](https://mude.citg.tudelft.nl/workbook-2026/assignments/WS1.3/1-bending_beam.html).

The verifier checks all of the following with exact rational arithmetic:

1. **Evidence and applicability:** declared subject/scope/source, dimensions, positive L/EI/limits, nonnegative loads/factors, and explicitly declared modeling assumptions.
2. **Pattern and combination:** q₁ and q₂ must be the declared dead and live intensities with the correct factors and live-load placement.
3. **Equilibrium:** total vertical force and total moment of the whole beam, plus the two span end moments.
4. **Curvature:** `2 EI c2 = M_left`, `6 EI c3 = V_left`, and `24 EI c4 = −q`.
5. **Kinematic compatibility:** zero displacement at A, B and C, and the same rotation at B from both spans.
6. **Envelope completeness:** every required case exists with its correct scope and provenance; span endpoints and admissible zero-shear locations determine moment extrema.
7. **Comparisons:** committed envelopes are compared with supplied limits, and signed minimum reactions are checked for uplift demand.

Treating both spans as simply supported produces zero pier moment and reactions that can satisfy equilibrium. It fails the rotation-continuity check, illustrating why an equilibrium-only check is insufficient for a statically indeterminate bridge.

## Use your own synthetic case

```python
from resimind.agent import Task
from resimind.domains.bridge import (
    DOMAIN, ContinuousBridgeProblem, LoadCombination,
    build_agent, verified_case_results, verified_summary,
)

problem = ContinuousBridgeProblem(
    spans=(18, 27),
    flexural_rigidities=(30_000_000_000, 42_000_000_000),
    dead_loads=(28, 32),
    live_loads=(20, 25),
    combinations=(LoadCombination("study", "1", "1"),),
    moment_limit=8000,
    midspan_deflection_limit=35,
    linear_elastic=True,
    small_deflection=True,
    prismatic_per_span=True,
    no_support_settlement=True,
    continuous_at_pier=True,
)
result = build_agent(problem).run(Task("bridge-study", "Verify the declared case", DOMAIN))
assert result.run_result.status == "solved"
print(verified_case_results(result))   # exact Fraction certificates
print(verified_summary(result))        # envelope and comparison facts
```

Registered bridge-specific unit tokens are `Npm`/`kNpm` (N/m, kN/m), `Nm`/`kNm` (moment), and `Nm2`/`kNm2` (flexural rigidity). Length uses the shared `m`/`cm`/`mm` registry. Use exact integers or decimal/rational strings, not binary floats. Units normalize to SI before calculation; arbitrary unit expressions are not evaluated.

Assumption flags default to `None`: they must be declared explicitly. The verifier checks the declarations' consistency with its model; it cannot establish that a real bridge satisfies them. Missing EI, omitted assumptions, incompatible units, and foreign-scope evidence leave obligations unresolved.

## Neural proposals and certificate format

`build_agent(problem, complete=your_callback)` uses the same provider-neutral `ModelProposer` as the other domains. The callback accepts a prompt string and returns one JSON candidate containing exactly `id`, `action`, `target`, `claim`, and `refs`. The prompt includes input evidence, current verified state, residual obligations, the latest actual verifier feedback, and the domain's action/claim schemas.

The default showcase uses an offline deterministic proposer. No live neural model run, learned beam solver, or LLM performance claim is implied. A real model may propose the same certificates, but it cannot approve its own answer.

For `solve_continuous_case`, the target is `bridge:case:<combination>:<pattern>`, the reference is `bridge:declared_model`, and the string-valued `claim` contains JSON with exactly these keys:

```json
{
  "case": "study:left",
  "q": ["<q1 in N/m>", "<q2 in N/m>"],
  "pier_moment": "<M_B in N m>",
  "reactions": ["<R_A in N>", "<R_B in N>", "<R_C in N>"],
  "v1": ["<c1>", "<c2>", "<c3>", "<c4>"],
  "v2": ["<c1>", "<c2>", "<c3>", "<c4>"]
}
```

The angle-bracket fields above are schema descriptions, not runnable numbers. Every numeric field must be an exact string such as `"-225740250/31"`. With x and v in meters, the coefficient dimensions are respectively 1, m⁻¹, m⁻², m⁻³. The constant coefficient is zero by construction at each span's left support. A certificate is committed under the case-specific scope only after all equations pass.

The remaining actions are `declare_bridge_model`, `envelope_bridge_cases`, and `compare_bridge_limits`; their complete schemas are supplied in the callback prompt. For deterministic ties the envelope retains the first declared case; moment candidates within a span are considered at x = 0, x = L, then the admissible zero-shear point.

## Validation and boundaries

The bridge tests include the equal-span benchmark `M_B = −qL²/8`, reactions `(3qL/8, 5qL/4, 3qL/8)`, midpoint displacement `qL⁴/(192EI)`, an unequal-span/unequal-EI benchmark, wrong-sign and wrong-support certificates, evidence tampering, missing patterns, unit conversion, load-limit exceedance, zero load, and uplift.

An additional test oracle releases the middle support and computes its redundant reaction by exact virtual-work integrals, `R_B = ∫M₀m/EI / ∫m²/EI`. Five distinct geometries/stiffness/load sets, each with all eight combination/pattern cases, are checked against this independently implemented method. This checks the proposal solver without duplicating the three-moment calculation.

`solved` means all declared analysis obligations are complete. It can coexist with `moment_within_supplied_limit=false`, `midspans_within_supplied_limit=false`, or `all_support_reactions_nonnegative=false`.

The model excludes moving axle trains, impact, load distribution across multiple girders, torsion, shear deformation, cracking, prestress, nonlinear materials, buckling, fatigue, settlement, bearings that lift off, and code-specific capacity or serviceability provisions. A support with uplift demand may violate the physical applicability of a compression-only bearing; this model reports that demand and does not solve a changed contact/support configuration. Original project reports and real bridge records are not part of this synthetic example.
