"""Build the bilingual README SVGs; no runtime dependencies.

Run: python tools/render_readme_art.py
Optional PNG exports (developer dependency only):
  npm install --prefix /tmp/resimind-art @resvg/resvg-js@2.6.2
  node tools/render_readme_png.cjs /tmp/resimind-art/node_modules/@resvg/resvg-js

These are explanatory illustrations, not screenshots or benchmark plots.
The algebra examples are exercised by the documented JSON-lines tool workflow.
"""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets"
WHITE, MUTED, MINT, PURPLE, RED = "#F1F5FD", "#A6B4CC", "#88F4CB", "#B9AAFF", "#FF99A8"
FONT = "Arial, Helvetica, 'PingFang SC', 'Microsoft YaHei', sans-serif"


class SVG:
    def __init__(self, height, title, desc):
        self.parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="{height}" viewBox="0 0 1280 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>
<defs>
 <linearGradient id="bg" x2="1" y2="1"><stop stop-color="#151A34"/><stop offset="1" stop-color="#080E1B"/></linearGradient>
 <radialGradient id="glow"><stop stop-color="#7560D8" stop-opacity=".20"/><stop offset="1" stop-color="#7560D8" stop-opacity="0"/></radialGradient>
 <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#91A5CC" opacity=".12"/></pattern>
 <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L7 4L0 8" fill="none" stroke="#93A8C7" stroke-width="1.4"/></marker>
</defs>
<rect width="1280" height="{height}" rx="24" fill="url(#bg)"/>
<rect width="1280" height="{height}" rx="24" fill="url(#grid)"/>
<ellipse cx="880" cy="180" rx="550" ry="440" fill="url(#glow)"/>
<g font-family="{FONT}">''']

    def text(self, x, y, value, size=20, color=WHITE, weight=400, spacing=None):
        extra = f' letter-spacing="{spacing}"' if spacing is not None else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}"{extra}>{escape(value)}</text>')

    def box(self, x, y, w, h, fill="#101D30", stroke="#30415E", radius=14):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    def path(self, d, color="#93A8C7", arrow=False):
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"{marker}/>')

    def save(self, name):
        (OUT / name).write_text("\n".join(self.parts) + "\n</g></svg>\n", encoding="utf-8")


def hero(zh=False):
    s = SVG(720, "ResiMind — 可验证记忆，让推理积累" if zh else "ResiMind — Catch the error. Keep the insight.",
            "Explanatory illustration: reject a wrong square expansion, check a repaired equality, and reuse independently admitted rules. Bounded polynomial algebra; not a benchmark.")
    s.box(48, 42, 46, 46, "#182F32", "#46766A", 12)
    s.path("M60 65L68 73L83 55", MINT)
    s.text(110, 77, "ResiMind", 37, WHITE, 700, -1)
    s.text(880, 70, "OPEN SOURCE  /  MIT  /  v0.15", 17, MINT, 700, 1)
    s.text(48, 142, "神经符号 AGENT · 可验证记忆" if zh else "NEURO-SYMBOLIC AGENTS · VERIFIED MEMORY", 16, PURPLE, 700, 1)
    if zh:
        s.text(46, 246, "发现错误。", 76, WHITE, 700)
        s.text(46, 338, "留下方法。", 76, MINT, 700)
        s.text(50, 399, "让这一次的推导，成为下一题的能力。", 24, WHITE)
        s.text(50, 444, "残差驱动纠错 · 已验证规则复用", 22, MUTED)
        s.text(50, 481, "知识在模型之外增长，无需重训。", 22, MUTED)
    else:
        s.text(45, 244, "Catch the error.", 70, WHITE, 700, -2.5)
        s.text(45, 330, "Keep the insight.", 70, MINT, 700, -2.5)
        s.text(50, 395, "Give the next task a checked head start.", 25, WHITE)
        s.text(50, 441, "Residual-guided recovery. Reusable rules.", 23, MUTED)
        s.text(50, 480, "Knowledge grows. Model weights stay put.", 23, MUTED)
    for x, w, label in [(50, 143, "MCP TOOLS"), (207, 130, "AGENT SKILL"), (351, 177, "PYTHON CORE")]:
        s.box(x, 525, w, 39, "#1A243D", "#3C4969", 19)
        s.text(x + 16, 550, label, 15, WHITE, 700)

    s.box(663, 170, 567, 401, "#0D1526", "#495277", 20)
    s.text(688, 204, "一个错误，如何变成下一步的线索" if zh else "A WRONG STEP BECOMES USEFUL FEEDBACK", 15, MUTED, 700, .5)
    s.path("M688 223H1205", "#2E3A54")
    s.text(688, 256, "01  拦住错误" if zh else "01  REJECT THE CLAIM", 16, RED, 700)
    s.text(688, 296, "(x + 1)² = x² + 1", 29, WHITE, 700)
    s.text(688, 326, "x 项系数不一致：2 ≠ 0" if zh else "Coefficient mismatch at x: 2 ≠ 0", 19, RED)
    s.path("M1190 297V363", arrow=True)
    s.text(688, 380, "02  核验修正" if zh else "02  CHECK THE REPAIR", 16, MINT, 700)
    s.text(688, 420, "(x + 1)² = x² + 2x + 1", 29, WHITE, 700)
    s.text(688, 450, "精确系数核验通过" if zh else "Exact coefficient check passes", 19, MINT)
    s.box(684, 478, 525, 70, "#142E30", "#345B53", 10)
    s.text(704, 505, "下一题：检索 → 复用 → 再验" if zh else "NEXT TASK: RETRIEVE → REUSE → RECHECK", 17, MINT, 700)
    s.text(704, 531, "规则须先经过独立准入核验" if zh else "Rules require separate verified admission", 17, MUTED)

    s.path("M48 608H1232", "#303C57")
    s.text(48, 648, "数学 / 桥梁 / 客服 / 约束规划" if zh else "MATH / BRIDGES / SUPPORT / CONSTRAINED PLANNING", 17, WHITE, 700)
    s.text(48, 683, "实验性架构 · 各领域独立验证 · MCP 与规则学习目前限于有界多项式" if zh else "Experimental · Domain-specific checks · MCP & rule learning: bounded polynomial algebra", 16, MUTED)
    s.text(974, 648, "1105216375-alt/resimind", 18, MINT)
    s.save("social-preview.zh-CN.svg" if zh else "social-preview.svg")


def loop(zh=False):
    s = SVG(660, "ResiMind 的两层循环" if zh else "Two loops. One verification boundary.",
            "Within a task, propose, independently verify, and use residual feedback to retry. Across tasks, extract candidate rules, independently admit them, store them, and recheck each application.")
    s.text(44, 53, "RESIMIND / INSIDE THE AGENT", 16, PURPLE, 700, 1)
    s.text(44, 104, "这一题，改对；下一题，用上。" if zh else "Correct this attempt. Carry the method forward.", 37, WHITE, 700, -.5)
    s.text(44, 140, "神经提议探索解法，符号核验决定事实；未完成的目标继续留在残差里。" if zh else "Proposals explore. Independent checks authorize facts. Unfinished work stays visible.", 21, MUTED)

    items = [
        (44, "01 / PROPOSE", "提出步骤" if zh else "Propose a step", "模型 · 规则 · 符号工具" if zh else "Model · rules · symbolic tools", PURPLE),
        (460, "02 / VERIFY", "独立核验" if zh else "Check independently", "领域检查 · 可选 Lean 证明" if zh else "Domain checks · optional Lean", MINT),
        (876, "03 / COMMIT", "提交已验证事实" if zh else "Commit checked facts", "重建目标、未知项与硬约束" if zh else "Rebuild remaining obligations", MINT),
    ]
    for x, label, title, sub, color in items:
        s.box(x, 187, 360, 128, "#152239", "#394C70")
        s.text(x + 20, 218, label, 15, color, 700, 1)
        s.text(x + 20, 258, title, 27, WHITE, 700)
        s.text(x + 20, 291, sub, 18, MUTED)
    s.path("M409 251H451", arrow=True)
    s.path("M825 251H867", arrow=True)
    s.path("M637 320V354H224V322", arrow=True)
    s.text(257, 383, "未通过 → 残差反馈 → 换策略 / 拆解 / 重试" if zh else "Reject / defer → residual feedback → revise, decompose, retry", 20, RED)
    s.path("M1056 320V350")
    s.path("M1056 398V432", arrow=True)
    s.text(878, 382, "任务完成后，显式学习" if zh else "Completed task + explicit learning", 18, MINT)

    s.box(44, 443, 1192, 146, "#102C2E", "#345D54")
    s.text(68, 475, "ACROSS TASKS / VERIFIED MEMORY", 15, MINT, 700, 1)
    s.text(68, 520, "抽取候选规则 → 独立准入核验 → 规则库 → 检索、应用、再验" if zh else "Extract candidate rule → verify admission → store → retrieve & recheck", 28, WHITE, 700)
    s.text(68, 560, "Python 库支持持久化、冻结与重载复核；MCP 记忆仅在当前进程内保留。" if zh else "Python: persist, freeze, revalidate on reload. MCP memory: current process only.", 20, MUTED)
    s.path("M44 500H24V250H36", MINT, arrow=True)
    s.text(44, 630, "所有策略切换受预算约束。规则和路线偏好都不能绕过核验。" if zh else "Finite work budgets across strategy changes. A retrieved rule never bypasses verification.", 19, MUTED)
    s.save("reasoning-loop.zh-CN.svg" if zh else "reasoning-loop.svg")


if __name__ == "__main__":
    for language in (False, True):
        hero(language)
        loop(language)
    print(f"Wrote four explanatory SVGs to {OUT}")
