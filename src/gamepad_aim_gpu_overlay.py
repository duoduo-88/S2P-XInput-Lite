"""Capture the aiming HUD canvas items for the GPU renderer."""

_PANEL_COLOR = "#0d1724"
_PANEL_OPACITY = 184 / 255
_ANCHOR_ALIGNMENT = {"ne": "right", "se": "right", "n": "center", "center": "center"}


def capture_overlay(canvas, items):
    """Return (rectangles, labels) for the visible HUD and center text item IDs."""
    overlays, labels = [], []
    fonts = {}
    for item in items:
        kind = canvas.type(item)
        if kind not in ("image", "rectangle", "text"):
            continue
        if canvas.itemcget(item, "state") == "hidden":
            continue
        if kind == "image":
            bounds = canvas.bbox(item)
            if bounds:
                overlays.append((*bounds, _PANEL_COLOR, _PANEL_OPACITY))
        elif kind == "rectangle":
            color = canvas.itemcget(item, "fill")
            if color:
                overlays.append((*canvas.coords(item), _rgb(canvas, color), 1.0))
        else:
            labels.extend(_capture_text(canvas, item, fonts))
    return tuple(overlays), tuple(labels)


def _rgb(canvas, color):
    if len(color) == 7 and color.startswith("#"):
        return color.lower()
    return "#" + "".join(f"{channel >> 8:02x}" for channel in canvas.winfo_rgb(color))


def _font_metrics(canvas, spec):
    tk = canvas.tk
    actual = tk.splitlist(tk.call("font", "actual", spec, "-displayof", canvas))
    options = dict(zip(actual[::2], actual[1::2]))
    size = int(options["-size"])
    # Windows Tk reports pixel-sized fonts in rounded points through font actual.
    if spec in tk.splitlist(tk.call("font", "names")):
        requested_size = int(tk.call("font", "configure", spec, "-size"))
    else:
        parts = tk.splitlist(spec)
        if parts and parts[0].startswith("-"):
            requested_size = int(dict(zip(parts[::2], parts[1::2])).get("-size", 0))
        else:
            requested_size = int(parts[1]) if len(parts) > 1 else 0
    pixels = -requested_size if requested_size < 0 else int(size * canvas.winfo_fpixels("1p") + 0.5)
    linespace = int(tk.call("font", "metrics", spec, "-displayof", canvas, "-linespace"))
    return max(1, pixels), options["-weight"] == "bold", linespace


def _capture_text(canvas, item, fonts):
    text = canvas.itemcget(item, "text")
    color = canvas.itemcget(item, "fill")
    if not text.strip() or not color:
        return
    color = _rgb(canvas, color)
    spec = canvas.itemcget(item, "font")
    if spec not in fonts:
        fonts[spec] = _font_metrics(canvas, spec)
    pixels, bold, linespace = fonts[spec]
    left, top, right, bottom = canvas.bbox(item)
    # Tk adds one pixel on both sides of the text layout for the selection border.
    left, right = left + 1, right - 1
    if bottom - top <= linespace:
        anchor = canvas.itemcget(item, "anchor")
        align = _ANCHOR_ALIGNMENT.get(anchor, "left")
        yield (left, top, right, bottom, text, color, pixels, bold, align)
        return

    align = canvas.itemcget(item, "justify")
    rows = range(top, bottom, linespace)
    # Tk lays out each line using its own wrap rules and font fallback. Read
    # the character index at each row's left edge and preserve those breaks.
    starts = [canvas.index(item, f"@{left - 2},{y}") for y in rows]
    starts.append(int(canvas.tk.call("string", "length", text)))
    for y, start, end in zip(rows, starts, starts[1:]):
        line = canvas.tk.call("string", "range", text, start, end - 1).rstrip()
        if line.strip():
            yield (left, y, right, y + linespace, line, color, pixels, bold, align)
