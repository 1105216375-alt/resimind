"""Two-span bridge showcase. Use --json for the complete evidence/decision trace."""
from fractions import Fraction
import argparse

from resimind.domains.bridge import demo_problem, run_demo, verified_case_results, verified_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full audit record")
    args = parser.parse_args()
    result = run_demo()
    if args.json:
        print(result.to_json())
        return
    print("ResiMind | Continuous bridge: 24 m + 30 m")
    print("Synthetic line beam; supplied factors/limits. Deflections below are MIDSPAN values.")
    for event in result.run_result.trace:
        print(f"  {event.decision.value.upper():6} {event.candidate.target}: {', '.join(event.reasons)}")
    print("\nCombination:pattern   Pier moment (kNm)      Reactions A / B / C (kN)")
    for case, row in verified_case_results(result).items():
        reactions = " / ".join(f"{float(value/1000):.2f}" for value in row["reactions"])
        print(f"  {case:19} {float(row['pier_moment']/1000):12.2f}       {reactions}")
    summary = verified_summary(result)
    if "envelope" in summary:
        env = summary["envelope"]
        for i, peak in enumerate(env["positive_max"], 1):
            print(f"  Span {i} positive maximum: {float(Fraction(peak['value'])/1000):.2f} kNm at x={float(Fraction(peak['x'])):.3f} m ({peak['case']})")
        for i, peak in enumerate(env["midspan_abs_max"], 1):
            print(f"  Span {i} absolute MIDSPAN deflection: {float(Fraction(peak['value'])*1000):.3f} mm ({peak['case']})")
        for metric, passed in summary["comparisons"].items():
            print(f"  {metric}: {passed}")
    print(f"\nStatus: {result.run_result.status}; remaining obligations: {len(result.run_result.residual.pending)}")


if __name__ == "__main__":
    main()
