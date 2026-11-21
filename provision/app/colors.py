"""Theme colors shared by the web UI and the screen: validation and readable text colors."""
import re

DEFAULT_MAIN = "#ee0000"
DEFAULT_ACCENT = "#ffcc00"
DARK_BG, LIGHT_BG = "#0a0a0a", "#ffffff"
_HEX = re.compile(r"#[0-9a-f]{6}")


def parse(s):
    """'#RRGGBB' normalised to lower case, else None."""
    s = (s or "").strip().lower()
    return s if _HEX.fullmatch(s) else None


def resolve(configured, env):
    """Config tab value, else the environment default, else None (use the built-in look)."""
    return parse(configured) or parse(env)


def to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def on_rgb(rgb):
    """Black or white, whichever reads better on `rgb`."""
    return (0, 0, 0) if contrast(rgb, (0, 0, 0)) >= contrast(rgb, (255, 255, 255)) else (255, 255, 255)


def readable(h, bg, minimum=4.5):
    """Nudge `h` toward white (dark page) or black (light page) until it reads on `bg`."""
    c, b = to_rgb(h), to_rgb(bg)
    target = (255, 255, 255) if luminance(b) < 0.5 else (0, 0, 0)
    for step in range(21):
        t = step / 20
        mixed = to_hex(tuple(c[i] + (target[i] - c[i]) * t for i in range(3)))
        if contrast(to_rgb(mixed), b) >= minimum:
            return mixed
    return to_hex(target)


def theme_css(main, accent):
    """CSS overrides for the web UI; empty when no custom colors are set."""
    if not (main or accent):
        return ""
    main, accent = main or DEFAULT_MAIN, accent or DEFAULT_ACCENT
    on = to_hex(on_rgb(to_rgb(main)))
    return (f":root{{--red:{main};--yellow:{accent};--on-red:{on};"
            f"--red-ink:{readable(main, DARK_BG)};--yellow-ink:{readable(accent, DARK_BG)}}}"
            f"@media (prefers-color-scheme:light){{:root{{"
            f"--red-ink:{readable(main, LIGHT_BG)};--yellow-ink:{readable(accent, LIGHT_BG)}}}}}")
