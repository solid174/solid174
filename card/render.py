#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools", "rich"]
# ///
"""Draw the profile README's hero: my terminal, open on a Catppuccin desktop.

    ./card/render.py   # -> card/terminal.svg, card/terminal-narrow.svg, README alt text

A translucent Ghostty window over a soft wallpaper replays a short session at my starship
prompt: `fastfetch` with a VLAD logo, then `cat about.md` the way bat prints it (`cat` is
aliased to bat). Commands get zsh-syntax-highlighting colours as they are typed; the bat
part is bat's real ANSI output, converted, not imitated. Under the window a tagline types
itself in a loop.

Text is drawn as JetBrainsMono Nerd Font Mono outlines rather than <text>: an SVG inside
an <img> can't fetch a font, and GitHub serves it with CSP `default-src 'none'` on top.
Outlines are what makes it look the same in every browser and on the phone.
"""

import html
import math
import os
import random
import re
import subprocess
import sys
import textwrap
from itertools import pairwise
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from rich.console import Console
from rich.text import Text

HERE = Path(__file__).resolve().parent
FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path.home() / ".local/share/fonts"]

# Catppuccin Mocha
CRUST, BASE, SURFACE1, TEXT = "#11111b", "#1e1e2e", "#45475a", "#cdd6f4"
RED, PEACH, YELLOW, GREEN, TEAL = "#f38ba8", "#fab387", "#f9e2af", "#a6e3a1", "#94e2d5"
BLUE, MAUVE, PINK, ROSEWATER = "#89b4fa", "#cba6f7", "#f5c2e7", "#f5e0dc"
# Ghostty's Catppuccin Mocha palette 0–15: what fastfetch's colour blocks come out as
PALETTE = ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
           "#585b70", "#f37799", "#89d88b", "#ebd391", "#74a8fc", "#f2aede", "#6bd7ca", "#bac2de"]

# Ghostty: font-size 14, window-padding 15×12, background-opacity 0.85, under a 28px titlebar
SIZE, PAD_X, PAD_Y, OPACITY, TITLEBAR = 14, 15, 12, 0.85, 28
DESK = 28  # wallpaper showing around the window

LOGO = [  # "VLAD" in figlet's ANSI Shadow
    "██╗   ██╗██╗      █████╗ ██████╗",
    "██║   ██║██║     ██╔══██╗██╔══██╗",
    "██║   ██║██║     ███████║██║  ██║",
    "╚██╗ ██╔╝██║     ██╔══██║██║  ██║",
    " ╚████╔╝ ███████╗██║  ██║██████╔╝",
    "  ╚═══╝  ╚══════╝╚═╝  ╚═╝╚═════╝",
]
TITLE = ("solid", "mac")
INFO = [
    ("Role", "Software Engineer"),
    ("Approach", "Agent-first, spec-driven"),
    ("Coding", "since 14"),
    ("OS", "macOS · Linux · Windows"),
]
TAGLINES = ["spec → verify → ship", "automating the boring parts", "agents write the code, I own the spec"]
TYPE, ERASE, HOLD, GAP = 0.07, 0.03, 1.8, 0.5  # s per letter typed / erased, s on screen, s between

GUTTER, MEASURE = 7, 80  # bat's "   1 │ " in front of every line; wrap about.md here at most

CONSOLE = Console(color_system="truecolor")


class Font:
    """The face Ghostty renders with; each glyph becomes one <path> in <defs>."""

    def __init__(self):
        self.faces = {}
        self.defs = {}
        regular = self.face(False, False)
        self.scale = SIZE / regular["head"].unitsPerEm
        hhea, post = regular["hhea"], regular["post"]
        self.cell_w = regular["hmtx"][regular.getBestCmap()[ord("M")]][0] * self.scale
        self.cell_h = (hhea.ascent - hhea.descent + hhea.lineGap) * self.scale
        self.ascent = hhea.ascent * self.scale
        self.underline_y = -post.underlinePosition * self.scale
        self.underline_h = max(1, post.underlineThickness * self.scale)

    def face(self, bold, italic):
        style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
        if style not in self.faces:
            name = f"JetBrainsMonoNerdFontMono-{style}.ttf"
            found = next((p for d in FONT_DIRS if d.is_dir() for p in d.rglob(name)), None)
            if found is None:
                sys.exit(f"{name} not found: brew install --cask font-jetbrains-mono-nerd-font")
            self.faces[style] = TTFont(found)
        return self.faces[style]

    def glyph(self, char, bold, italic):
        """Id of char's <path>, defined on first use."""
        gid = f"g{bold:d}{italic:d}{ord(char):x}"
        if gid not in self.defs:
            face = self.face(bold, italic)
            glyphs = face.getGlyphSet()
            pen = SVGPathPen(glyphs)
            flip = TransformPen(pen, (self.scale, 0, 0, -self.scale, 0, 0))
            glyphs[face.getBestCmap()[ord(char)]].draw(flip)
            self.defs[gid] = f'<path id="{gid}" d="{pen.getCommands()}"/>'
        return gid


class Term:
    """Ghostty's cell grid inside the window: (row, col) to pixels."""

    def __init__(self, font, x0, y0):
        self.font, self.x0, self.y0 = font, x0, y0
        self.cw, self.ch = font.cell_w, font.cell_h

    def glyphs(self, x, y, s, fill, bold=False, italic=False, underline=False, attrs=""):
        """s with its baseline starting at (x, y), one cell per char."""
        out = "".join(f'<use href="#{self.font.glyph(c, bold, italic)}" x="{x + i * self.cw:.2f}" y="{y:.2f}"/>'
                      for i, c in enumerate(s) if c != " ")
        if underline:
            out += (f'<rect x="{x:.2f}" y="{y + self.font.underline_y:.2f}" '
                    f'width="{len(s) * self.cw:.2f}" height="{self.font.underline_h:.2f}"/>')
        return f'<g fill="{fill}" {attrs}>{out}</g>' if out else ""

    def text(self, row, col, s, *style, **kw):
        return self.glyphs(self.x0 + col * self.cw, self.y0 + row * self.ch + self.font.ascent, s, *style, **kw)

    def cells(self, row, col, n, fill, attrs="", bleed=0):
        """n solid cells, the way Ghostty draws █ itself; bleed reaches into the row below so
        stacked blocks don't show a seam."""
        return (f'<rect x="{self.x0 + col * self.cw:.2f}" y="{self.y0 + row * self.ch:.2f}" '
                f'width="{n * self.cw:.2f}" height="{self.ch + bleed:.2f}" fill="{fill}" {attrs}/>')

    def prompt(self, row):
        """starship: the directory in bold cyan, then `>` in bold green on the next line."""
        return self.text(row, 0, "~", TEAL, bold=True) + self.text(row + 1, 0, ">", GREEN, bold=True)


def at(t):
    """Hidden until t, then shown for good."""
    return f'class="on" style="animation-delay:{t:.2f}s"'


def during(start, end):
    """Shown only from start to end."""
    return f'class="win" style="animation-delay:{start:.2f}s;animation-duration:{end - start:.2f}s"'


def typing(term, row, command, shown, think):
    """`command` typed at the prompt on `row`, which appeared at `shown`. Returns (svg, Enter).

    zsh-syntax-highlighting paints the command word bold red until it resolves (no prefix of
    `fastfetch` or `cat` is a command) and underlines the path argument."""
    rng = random.Random(command)  # uneven like a person, the same on every render
    keys, t = [], shown + think
    for c in command:
        keys.append(t)
        t += 0.06 + 0.08 * rng.random() + (0.1 if c == " " else 0)
    enter = keys[-1] + 0.35
    verb = len(command.split()[0])
    resolved = keys[verb - 1]
    out = []
    for i, (c, t) in enumerate(zip(command, keys, strict=True)):
        if i < verb:
            if t < resolved:
                out.append(term.text(row, 2 + i, c, RED, bold=True, attrs=during(t, resolved)))
            out.append(term.text(row, 2 + i, c, GREEN, attrs=at(resolved)))
        else:
            out.append(term.text(row, 2 + i, c, TEXT, underline=c != " ", attrs=at(t)))
    stops = [shown, *keys, enter]
    out += [term.cells(row, 2 + i, 1, ROSEWATER, during(a, b)) for i, (a, b) in enumerate(pairwise(stops))]
    return "".join(out), enter


def fastfetch(term, row, cols):
    """fastfetch from `row`: the logo left of the info, or above it in a narrow terminal.
    Returns (svg, rows used, counting fastfetch's trailing blank line)."""
    logo_w = max(map(len, LOGO))
    beside = logo_w + 4 + max(len(k) + 2 + len(v) for k, v in INFO) <= cols
    logo_row, info_row, col = (row + 1, row, logo_w + 4) if beside else (row + 1, row + len(LOGO) + 2, 0)

    # █ runs share one gradient across the whole logo; the box-drawing shadow stays dim
    x, y = term.x0, term.y0 + logo_row * term.ch
    out = [(f'<linearGradient id="logo" gradientUnits="userSpaceOnUse" x1="{x:.2f}" y1="{y:.2f}" '
            f'x2="{x + logo_w * term.cw:.2f}" y2="{y + len(LOGO) * term.ch:.2f}">'
            f'<stop offset="0" stop-color="{MAUVE}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient>')]
    for r, line in enumerate(LOGO, start=logo_row):
        out += [term.cells(r, m.start(), len(m[0]), "url(#logo)", bleed=0.5) for m in re.finditer("█+", line)]
        out.append(term.text(r, 0, line.replace("█", " "), SURFACE1))

    user, host = TITLE
    out.append(term.text(info_row, col, user, MAUVE, bold=True) + term.text(info_row, col + len(user), "@", TEXT)
               + term.text(info_row, col + len(user) + 1, host, MAUVE, bold=True))
    out.append(term.text(info_row + 1, col, "-" * (len(user) + 1 + len(host)), TEXT))
    for r, (key, value) in enumerate(INFO, start=info_row + 2):
        out.append(term.text(r, col, key, BLUE, bold=True) + term.text(r, col + len(key), f": {value}", TEXT))
    blocks = info_row + 3 + len(INFO)
    out += [term.cells(blocks + i // 8, col + 3 * (i % 8), 3, c) for i, c in enumerate(PALETTE)]
    return "".join(out), max(logo_row + len(LOGO), blocks + 2) - row + 1


def bat(about, cols):
    """about.md as bat prints it in a terminal `cols` wide: one ANSI string per line."""
    width = min(cols - GUTTER - 1, MEASURE)
    blocks = [b if b.startswith("#") else textwrap.fill(" ".join(b.split()), width)
              for b in about.strip().split("\n\n")]
    return subprocess.run(  # --no-config: my ~/.config/bat/config, spelled out here
        ["bat", "--no-config", "--color=always", "--paging=never", "--wrap=never",
         f"--terminal-width={cols}", "--theme=Catppuccin Mocha", "--style=numbers,header,grid",
         "--italic-text=always", "--language=md", "--file-name=about.md"],
        input="\n\n".join(blocks) + "\n", stdout=subprocess.PIPE, text=True, check=True,
        env=os.environ | {"COLORTERM": "truecolor"},
    ).stdout.splitlines()


def runs(line):
    """(col, text, fg, bold, italic, underline) for each styled run of an ANSI line."""
    col = 0
    for seg in Text.from_ansi(line).render(CONSOLE):
        style = seg.style
        if style is None or style.color is None:
            fg = TEXT
        elif style.color.triplet is None:
            sys.exit(f"bat used a palette colour ({style.color.name}), not the theme's RGB")
        else:
            fg = style.color.triplet.hex
        bold, italic, underline = (bool(style and getattr(style, a)) for a in ("bold", "italic", "underline"))
        yield col, seg.text, fg, bold, italic, underline
        col += len(seg.text)


def tagline(term, x, top):
    """`❯ ` and TAGLINES typed and erased in a loop after it, at (x, top). SMIL rather than CSS:
    one discrete timeline per phrase instead of a keyframes rule per letter. A renderer without
    SMIL shows the first phrase standing still; reduced motion swaps in a still copy."""
    cw, ch, y = term.cw, term.ch, top + term.font.ascent
    prompt = term.glyphs(x, y, "❯", MAUVE, bold=True)
    x += 2 * cw
    events, t = [], 0.0  # (time, phrase, letters showing)
    for p, phrase in enumerate(TAGLINES):
        for k in range(1, len(phrase) + 1):
            t += TYPE
            events.append((t, p, k))
        t += HOLD
        for k in range(len(phrase) - 1, -1, -1):
            t += ERASE
            events.append((t, p, k))
        t += GAP
    cycle = t

    def animate(attr, points):
        keys = ";".join(f"{t / cycle:.4f}" for t, _ in points)
        values = ";".join(f"{v:.2f}" for _, v in points)
        return (f'<animate attributeName="{attr}" dur="{cycle:.2f}s" repeatCount="indefinite" '
                f'calcMode="discrete" keyTimes="{keys}" values="{values}"/>')

    first = len(TAGLINES[0]) * cw
    loop = []
    for p, phrase in enumerate(TAGLINES):
        points = [(0, 0)] + [(t, k * cw) for t, q, k in events if q == p]
        loop.append(f'<clipPath id="tag{p}"><rect x="{x:.2f}" y="{top:.2f}" width="{first if p == 0 else 0:.2f}" '
                    f'height="{ch:.2f}">{animate("width", points)}</rect></clipPath>'
                    + term.glyphs(x, y, phrase, TEXT, attrs=f'clip-path="url(#tag{p})"'))
    cursor = f'y="{top:.2f}" width="{cw:.2f}" height="{ch:.2f}" fill="{ROSEWATER}"'
    loop.append(f'<rect class="blink" x="{x + first:.2f}" {cursor}>'
                f'{animate("x", [(0, x)] + [(t, x + k * cw) for t, _, k in events])}</rect>')
    still = term.glyphs(x, y, TAGLINES[0], TEXT) + f'<rect x="{x + first:.2f}" {cursor}/>'
    return f'{prompt}<g class="loop">{"".join(loop)}</g><g class="still">{still}</g>'


def desktop(width, height, x, y, w, h):
    """The wallpaper (soft Catppuccin light over crust, with grain) and the translucent window
    on it. The window's shadow is cast only outside it, the way a compositor draws one."""
    glows = [(MAUVE, ".12", ".05", ".85", ".7"), (BLUE, ".98", ".55", ".7", ".55"),
             (PEACH, ".02", "1", ".65", ".5"), (PINK, ".88", "0", ".5", ".4")]
    gradients = "".join(f'<radialGradient id="glow{i}" cx="{cx}" cy="{cy}" r="{r}">'
                        f'<stop offset="0" stop-color="{c}" stop-opacity="{o}"/>'
                        f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>'
                        for i, (c, cx, cy, r, o) in enumerate(glows))
    full = f'width="{width}" height="{height}"'
    win = f'x="{x}" y="{y}" width="{w:.2f}" height="{h:.2f}" rx="12"'
    return f"""<defs>{gradients}
<clipPath id="desk"><rect {full} rx="16"/></clipPath>
<filter id="grain" x="0" y="0" width="1" height="1"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" stitchTiles="stitch"/><feColorMatrix values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 .07 0 0 0 0"/></filter>
<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/><feOffset dy="10"/></filter>
<mask id="outside"><rect {full} fill="#fff"/><rect {win} fill="#000"/></mask>
</defs>
<g clip-path="url(#desk)"><rect {full} fill="{CRUST}"/>{"".join(f'<rect {full} fill="url(#glow{i})"/>' for i in range(len(glows)))}<rect {full} filter="url(#grain)"/>
<rect {win} fill="#000" fill-opacity=".55" filter="url(#shadow)" mask="url(#outside)"/></g>
<rect {win} fill="{BASE}" fill-opacity="{OPACITY}" stroke="{TEXT}" stroke-opacity=".12"/>
{"".join(f'<circle cx="{x + 20 + 20 * i}" cy="{y + TITLEBAR / 2}" r="6" fill="{c}"/>' for i, c in enumerate((RED, YELLOW, GREEN)))}"""


def describe(about):
    """The card in plain sentences: the SVG's title and the README's alt text."""
    heading, *paragraphs = (" ".join(b.lstrip("#").split()) for b in about.strip().split("\n\n"))
    facts = "; ".join(f"{k}: {v}" for k, v in INFO)
    text = " ".join([f"{heading}.", f"{facts}.", *paragraphs])
    return html.escape(text, quote=False).replace('"', "&quot;")


def render(font, about, label, cols):
    term = Term(font, DESK + PAD_X, DESK + TITLEBAR + PAD_Y)

    # ~ > fastfetch
    body = [term.prompt(0)]
    typed, enter = typing(term, 1, "fastfetch", shown=0, think=0.5)
    fetched, used = fastfetch(term, 2, cols)
    row = 2 + used
    body += [typed, f'<g {at(enter)}>{fetched}{term.prompt(row)}</g>']

    # ~ > cat about.md, then the cursor goes back to blinking
    typed, enter = typing(term, row + 1, "cat about.md", shown=enter, think=0.9)
    lines = bat(about, cols)
    printed = "".join(term.text(r, *run) for r, line in enumerate(lines, start=row + 2) for run in runs(line))
    row += 2 + len(lines)
    blink = f'<g class="blink" style="animation-delay:{enter:.2f}s">{term.cells(row + 1, 2, 1, ROSEWATER)}</g>'
    body += [typed, f'<g {at(enter)}>{printed}{term.prompt(row)}{blink}</g>']

    win_w, win_h = 2 * PAD_X + cols * term.cw, TITLEBAR + 2 * PAD_Y + (row + 2) * term.ch
    tag_top = DESK + win_h + 18
    tag = tagline(term, DESK + PAD_X, tag_top)
    width, height = math.ceil(win_w + 2 * DESK), math.ceil(tag_top + term.ch + 22)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{label}">
<title>{label}</title>
<style>
.on{{animation:on 1ms both}}
.win{{opacity:0;animation:win 1ms step-end}}
.blink{{animation:blink 1.2s step-end infinite}}
.still{{display:none}}
@keyframes on{{from{{opacity:0}}}}
@keyframes win{{from,to{{opacity:1}}}}
@keyframes blink{{50%{{opacity:0}}}}
@media (prefers-reduced-motion:reduce){{.on,.win,.blink{{animation:none}}.loop{{display:none}}.still{{display:inline}}}}
</style>
<defs>{"".join(font.defs.values())}</defs>
{desktop(width, height, DESK, DESK, win_w, win_h)}
{"".join(body)}
{tag}
</svg>
"""


def main():
    about = (HERE / "about.md").read_text()
    label = describe(about)
    for name, cols in (("terminal.svg", 90), ("terminal-narrow.svg", 44)):
        font = Font()  # fresh <defs> per file: each SVG carries only the glyphs it uses
        (HERE / name).write_text(render(font, about, label, cols))
        print(f"card/{name}")
    # the card is an image: its words reach screen readers and search only through the alt
    readme = HERE.parent / "README.md"
    hero = r'(src="card/terminal\.svg" alt=")[^"]*'
    readme.write_text(re.sub(hero, lambda m: m[1] + label, readme.read_text()))
    print("README.md (alt)")


if __name__ == "__main__":
    main()
