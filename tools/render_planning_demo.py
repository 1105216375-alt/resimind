"""Render the real offline planning trace as a dependency-free SVG.

Run from a source checkout:
    PYTHONPATH=src python tools/render_planning_demo.py

The illustration uses actual verifier events and the checked plan. It fails if
the demonstrated rejection or correction changes, rather than inventing a
successful result. No live model or external travel service is called.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from resimind import Decision
from resimind.domains.planning import demo_problem, run_demo, verified_plan


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1100, 432
BG, PANEL, LINE = "#0b1220", "#131f31", "#26364c"
WHITE, MUTED, CYAN, GREEN, RED = "#f0f6ff", "#93a8c2", "#68deea", "#69e0b2", "#ff8190"


def clock(minutes: int) -> str:
    return f"{minutes // 60:02}:{minutes % 60:02}"


def money(cents: int) -> str:
    return f"¥{cents // 100}" if cents % 100 == 0 else f"¥{cents / 100:.2f}"


class Canvas:
    def __init__(self):
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
            f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">',
            '<title id="title">ResiMind: open-ended planning with independent verification</title>',
            '<desc id="desc">Actual offline fixture with synthetic places. A proposal exceeds the walking '
            'limit and is rejected without changing facts. Revised transport yields a checked feasible '
            'itinerary. Displayed measurements come from the verifier trace and checked output.</desc>',
            '<style>text{font-family:Arial,Helvetica,sans-serif}</style>',
        ]

    def rect(self, x, y, width, height, fill, stroke=None, radius=12):
        outline = f' stroke="{stroke}"' if stroke else ""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                          f'rx="{radius}" fill="{fill}"{outline}/>')

    def text(self, x, y, text, size=16, color=WHITE, bold=False, anchor="start"):
        weight = ' font-weight="700"' if bold else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
                          f'text-anchor="{anchor}"{weight}>{escape(str(text))}</text>')

    def line(self, x1, y1, x2, y2, color=LINE):
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}"/>')

    def finish(self):
        content = "\n".join(self.parts + ["</svg>", ""])
        ET.fromstring(content)
        return content


def render() -> str:
    problem = demo_problem("day-out")
    result = run_demo("day-out")
    run = result.run_result
    if run.status != "solved" or not run.residual.solved:
        raise RuntimeError("Demo is unfinished; do not render an accepted plan")
    if [event.decision for event in run.trace] != [Decision.REJECT, Decision.ACCEPT]:
        raise RuntimeError("Trace changed; review the storyboard")
    rejected, accepted = run.trace
    if rejected.before != rejected.after or rejected.residual_before != rejected.residual_after:
        raise RuntimeError("Rejection changed the committed state or residual")
    if rejected.reasons != ("walking_limit_exceeded",):
        raise RuntimeError("Rejection changed; review the walking annotation")
    if rejected.candidate is None:
        raise RuntimeError("No rejected candidate to render")
    candidate = json.loads(rejected.candidate.claim)
    travel = {route.id: route for route in problem.travel}
    rejected_routes = [travel[leg["route_id"]] for leg in candidate["legs"]]
    if any(route.mode != "walk" or route.walking_minutes is None for route in rejected_routes):
        raise RuntimeError("The rejected candidate is no longer an all-walking plan")
    rejected_walk = sum(route.walking_minutes for route in rejected_routes)
    if rejected_walk <= problem.max_walking_minutes:
        raise RuntimeError("The illustrated walking limit no longer fails")
    plan = verified_plan(result)
    if len(plan["stops"]) != 3 or "rationale" in plan or len(accepted.after.facts) != 1:
        raise RuntimeError("Checked output changed; review the layout")
    if not any(leg["mode"] == "transit" for leg in plan["legs"]):
        raise RuntimeError("The displayed transport correction changed")
    places = {place.id: place for place in problem.places}

    canvas = Canvas()
    canvas.rect(0, 0, WIDTH, HEIGHT, BG, radius=0)
    canvas.text(32, 33, "RESIMIND / OPEN PLANNING", 14, CYAN, True)
    canvas.rect(700, 14, 368, 29, "#1d3044", radius=7)
    canvas.text(884, 33, "OFFLINE FIXTURE · SYNTHETIC PLACES", 12, CYAN, True, "middle")
    canvas.text(32, 82, "Many valid plans. Every constraint checked.", 31, WHITE, True)
    canvas.text(32, 115, "Choose venues, timing and transport. Verify feasibility against the supplied snapshot.", 17, MUTED)

    canvas.rect(32, 144, 310, 238, PANEL, LINE)
    canvas.text(52, 175, "01  REJECT", 15, RED, True)
    canvas.text(52, 209, "A plausible all-walking day", 17, WHITE, True)
    canvas.text(52, 250, f"{rejected_walk} min > {problem.max_walking_minutes} min", 29, RED, True)
    canvas.text(52, 275, "Walking limit exceeded", 15, MUTED)
    canvas.line(52, 293, 322, 293)
    canvas.text(52, 321, f"State {rejected.before.revision} → {rejected.after.revision}  |  "
                f"facts {len(rejected.before.facts)} → {len(rejected.after.facts)}", 17, WHITE)
    canvas.text(52, 352, f"Remaining obligations: {rejected.residual_before.measure} → "
                f"{rejected.residual_after.measure}", 15, MUTED)

    canvas.rect(358, 144, 710, 238, PANEL, LINE)
    canvas.text(380, 175, "02  REVISE TRANSPORT + ACCEPT", 15, GREEN, True)
    for index, stop in enumerate(plan["stops"]):
        top = 209 + index * 29
        place = places[stop["place_id"]]
        canvas.text(380, top, f"{index + 1:02}", 15, CYAN, True)
        canvas.text(417, top, place.name, 17, WHITE)
        canvas.text(1046, top, f"{clock(stop['start_minute'])}–{clock(stop['end_minute'])}",
                    16, MUTED, anchor="end")
    metrics = (
        ("COST / BUDGET", f"{money(plan['total_cost_cents'])} / {money(problem.budget_cents)}"),
        ("WALK / LIMIT", f"{plan['total_walking_minutes']} / {problem.max_walking_minutes} min"),
        ("RETURN / DEADLINE", f"{clock(plan['return_minute'])} / {clock(problem.day_end_minute)}"),
    )
    for index, (label, value) in enumerate(metrics):
        left = 380 + index * 225
        canvas.rect(left, 292, 215, 69, "#0d1726", radius=7)
        canvas.text(left + 12, 315, label, 11, MUTED, True)
        canvas.text(left + 12, 345, value, 21, GREEN, True)

    canvas.text(32, 414, "A checked feasible plan. Enjoyment and the “best trip” remain subjective.", 15, MUTED)
    return canvas.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets/planning-demo.svg")
    args = parser.parse_args()
    content = render()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"Saved {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
