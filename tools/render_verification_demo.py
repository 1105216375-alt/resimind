"""Render a small replay of actual verifier decisions as GIF and a PNG poster.

Developer utility, not a runtime dependency. Install Pillow, then run:
    PYTHONPATH=src python tools/render_verification_demo.py
Optional: --font-dir /path/to/DejaVu/ttf --output-dir /path/to/output

Each frame is a genuine TraceEvent from the offline optimization demo. The
script fails if the demo no longer exhibits the displayed rejection invariant.
It never synthesizes a successful result or invents an LLM execution.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from resimind import Decision
from resimind.domains.optimization import CONVEXITY_FACT, GLOBAL_FACT, PRIMAL_FACT, run_demo

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, SCALE = 1100, 432, 2
BG, PANEL, LINE = "#0b1220", "#131f31", "#26364c"
WHITE, MUTED, CYAN, GREEN, RED = "#f0f6ff", "#93a8c2", "#68deea", "#69e0b2", "#ff8190"


def find_fonts(directory: Path | None) -> tuple[str, str, str]:
    roots = [directory] if directory else []
    roots += [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]
    spec = importlib.util.find_spec("matplotlib")
    if spec and spec.origin:
        roots.append(Path(spec.origin).parent / "mpl-data/fonts/ttf")
    for root in roots:
        if root is None:
            continue
        names = tuple(root / name for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono.ttf"))
        if all(path.is_file() for path in names):
            return tuple(map(str, names))
    mac = Path("/System/Library/Fonts/Supplemental")
    mac_names = (mac / "Arial.ttf", mac / "Arial Bold.ttf", mac / "Courier New.ttf")
    if all(path.is_file() for path in mac_names):
        return tuple(map(str, mac_names))
    raise RuntimeError("Install DejaVu fonts or use --font-dir with DejaVuSans.ttf, DejaVuSans-Bold.ttf, DejaVuSansMono.ttf")


class Canvas:
    def __init__(self, fonts):
        self.image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = fonts

    def box(self, bounds, fill, outline=None, radius=12):
        self.draw.rounded_rectangle(tuple(int(v * SCALE) for v in bounds), radius=radius * SCALE,
                                    fill=fill, outline=outline, width=SCALE)

    def text(self, xy, value, size=16, color=WHITE, style=0, width=None):
        value = str(value)
        font = ImageFont.truetype(self.fonts[style], size * SCALE)
        while width is not None and self.draw.textlength(value, font=font) > width * SCALE:
            size -= 1
            if size < 10:
                raise ValueError(f"Text does not fit: {value}")
            font = ImageFont.truetype(self.fonts[style], size * SCALE)
        self.draw.text((xy[0] * SCALE, xy[1] * SCALE), value, fill=color, font=font, anchor="lt")

    def finish(self):
        return self.image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def facts_at(event):
    return {fact.id: json.loads(fact.value) for fact in event.after.facts}


def render_frame(trace, index, fonts):
    event = trace[index]
    canvas = Canvas(fonts)
    canvas.text((32, 19), "RESIMIND", 15, CYAN, 1)
    canvas.box((634, 14, 1068, 42), "#1d3044", radius=7)
    canvas.text((648, 21), "OFFLINE FIXTURE, NOT A LIVE LLM RUN", 13, CYAN, 1, width=406)
    canvas.text((32, 59), "Propose. Verify. Commit only what passes.", 30, WHITE, 1, width=1036)
    canvas.text((32, 103), "Exact constrained optimization  /  real verifier trace, replayed offline", 16, MUTED)
    canvas.box((32, 141, 1068, 370), PANEL, outline=LINE)
    titles = ("Positive definiteness", "Infeasible candidate", "Corrected primal + KKT", "Global certificate")
    for row, title in enumerate(titles):
        top = 156 + row * 50
        observed = row <= index
        color = (RED if trace[row].decision is Decision.REJECT else GREEN) if observed else MUTED
        if row == index:
            canvas.box((43, top - 3, 303, top + 42), "#22344a", radius=8)
        canvas.text((54, top + 4), str(row + 1).zfill(2), 17, color, 2)
        canvas.text((91, top + 2), title, 15, WHITE if observed else MUTED, 1, width=200)
        canvas.text((91, top + 23), trace[row].decision.value.upper() if observed else "PENDING", 11, color, 1)
    labels = (
        "Convexity certificate accepted",
        "A plausible stationary point is rejected",
        "Primal + KKT witnesses accepted together",
        "Unique global minimum certified",
    )
    canvas.text((329, 158), labels[index], 21, RED if index == 1 else GREEN, 1, width=708)
    facts = facts_at(event)
    if index == 0:
        diagonal = facts[CONVEXITY_FACT]["d"]
        canvas.text((329, 196), "Q = L D L^T", 23, WHITE, 2)
        canvas.text((329, 233), f"D = ({', '.join(diagonal)}) > 0", 21, CYAN, 2)
        canvas.text((329, 269), "Exact factorization proves a strictly convex objective.", 15, MUTED)
    elif index == 1:
        candidate = json.loads(event.candidate.claim)["primal"]
        canvas.text((329, 196), f"x = ({', '.join(candidate['x'])})", 23, WHITE, 2, width=700)
        canvas.text((329, 233), f"x1 = {candidate['x'][0]} > 1", 21, RED, 2)
        canvas.text((329, 269), "Rejected. No candidate facts enter committed state.", 15, MUTED)
    else:
        primal = facts[PRIMAL_FACT]
        canvas.text((329, 196), f"x* = ({', '.join(primal['x'])})", 23, WHITE, 2, width=700)
        canvas.text((329, 233), f"f(x*) = {primal['objective']}", 21, CYAN, 2)
        note = ("Feasibility, stationarity and complementarity all pass." if index == 2 else
                f"{len(facts[GLOBAL_FACT]['weights'])} positive weighted squares + constraint terms prove the bound.")
        canvas.text((329, 269), note, 15, MUTED, width=708)
    for column, (label, value, color) in enumerate((
        ("STATE REVISION", f"{event.before.revision} -> {event.after.revision}" + (" unchanged" if event.before == event.after else ""), RED if index == 1 else WHITE),
        ("OBLIGATIONS LEFT", str(event.residual_after.measure), RED if index == 1 else WHITE),
        ("COMMITTED FACTS", str(len(event.after.facts)), WHITE),
    )):
        left = 329 + column * 235
        canvas.box((left - 5, 305, left + 217, 358), "#0d1726", radius=7)
        canvas.text((left + 6, 313), label, 10, MUTED, 1)
        canvas.text((left + 6, 331), value, 16, color, 2, width=202)
    rejection = next(item for item in trace if item.decision is Decision.REJECT)
    canvas.text((32, 389), f"REJECTED STEP   state {rejection.before.revision} -> {rejection.after.revision}  |  "
                f"remaining {rejection.residual_before.measure} -> {rejection.residual_after.measure}", 14, RED, 1)
    canvas.text((891, 389), f"TRACE {index + 1} / {len(trace)}", 14, MUTED, 2)
    return canvas.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs/assets")
    args = parser.parse_args()
    result = run_demo().run_result
    if result.status != "solved":
        raise RuntimeError(f"Demo did not solve: {result.status}")
    trace = result.trace
    if [event.decision for event in trace] != [Decision.ACCEPT, Decision.REJECT, Decision.ACCEPT, Decision.ACCEPT]:
        raise RuntimeError("The trace changed; update storyboard before regenerating the assets")
    rejected = trace[1]
    if rejected.before != rejected.after or rejected.residual_before != rejected.residual_after:
        raise RuntimeError("Rejected proposal mutated the committed state or residual")
    if [event.candidate.action for event in trace] != ["certify_convexity", "certify_primal_dual", "certify_primal_dual", "certify_global"]:
        raise RuntimeError("The action sequence changed; review labels before rendering")
    if json.loads(rejected.candidate.claim)["primal"]["x"] != ["26/15", "1/5", "16/15"]:
        raise RuntimeError("The demonstration candidate changed; review the constraint annotation")
    fonts = find_fonts(args.font_dir)
    frames = [render_frame(trace, index, fonts) for index in range(len(trace))]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    poster = args.output_dir / "verification-demo.png"
    animation = args.output_dir / "verification-demo.gif"
    frames[-1].save(poster, optimize=True)
    palette_source = Image.new("RGB", (WIDTH, HEIGHT * len(frames)))
    for index, frame in enumerate(frames):
        palette_source.paste(frame, (0, HEIGHT * index))
    palette = palette_source.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(animation, save_all=True, append_images=indexed[1:],
                    duration=[2400, 3600, 2600, 4400], loop=0, optimize=True, disposal=2)
    # Verify frame count, dimensions and dwell times in the saved output.
    with Image.open(animation) as check:
        assert check.n_frames == 4 and check.size == (WIDTH, HEIGHT)
        for index in range(check.n_frames):
            check.seek(index)
            assert check.info["duration"] >= 2000
    print(f"Saved {animation} ({animation.stat().st_size:,} bytes) and {poster} ({poster.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
