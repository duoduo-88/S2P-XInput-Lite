"""Build the Gamepad Tester icon set from the 32 px pixel-art source.

Sizes that are whole multiples of 32 are nearest-neighbour enlargements, so
they match the artwork exactly.  Smaller and in-between sizes are plain
area-averaged shrinks of the same artwork (soft, but never redrawn).

    python image/make_tester_icon.py   (needs Pillow)
"""

from pathlib import Path

from PIL import Image

IMAGE = Path(__file__).resolve().parent
SOURCE = IMAGE / "testicon_source.png"


def load_source():
    source = Image.open(SOURCE).convert("RGBA")
    if source.size != (32, 32):
        raise SystemExit("testicon_source.png must be the 32 px artwork")
    data = source.load()
    for y in range(32):
        for x in range(32):
            if data[x, y][3] == 0:
                data[x, y] = (0, 0, 0, 0)  # no hidden colour that could bleed
    return source


def shrink(source, size):
    """Area-average with premultiplied alpha, so edges get no dark fringe."""
    return source.convert("RGBa").resize((size, size), Image.Resampling.BOX).convert("RGBA")


def main():
    source = load_source()
    frames = {size: shrink(source, size) for size in (16, 20, 24, 40, 48)}
    for size in (32, 64, 128, 256):
        frames[size] = source.resize((size, size), Image.Resampling.NEAREST)
    order = sorted(frames)
    frames[256].save(
        IMAGE / "gamepad_tester.ico",
        sizes=[(size, size) for size in order],
        append_images=[frames[size] for size in order if size != 256],
    )
    frames[256].save(IMAGE / "testicon.png")
    frames[32].save(IMAGE / "testicon32.png")
    frames[16].save(IMAGE / "testicon16.png")
    print("wrote", ", ".join(f"{size}px" for size in order))


if __name__ == "__main__":
    main()
