"""Record a live terminal session to an animated GIF.

Runs a command in a real PTY, captures what it prints, and renders the frames
to a GIF that shows the output appearing as it happens.

    python tools/record_gif.py <out.gif> -- <command> [args...]

Stdlib plus Pillow. No asciinema needed.
"""
import os
import pty
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/NotoSansMono-Light.ttf",
    "/usr/share/fonts/truetype/meslo/MesloLGSDZNerdFontMono-Regular.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/NotoSansMono-Bold.ttf",
]

FLUSH_INTERVAL = 0.04  # seconds between rendered frames

BG = (16, 18, 24)
FG = (222, 226, 235)
DIM = (118, 126, 145)
GREEN = (126, 202, 138)
CYAN = (108, 193, 218)
YELLOW = (229, 192, 123)
RED = (224, 108, 117)
PROMPT = (126, 202, 138)

# xterm-ish 16 colour palette
PALETTE = {
    30: (60, 64, 72), 31: RED, 32: GREEN, 33: YELLOW, 34: CYAN, 35: (198, 120, 221),
    36: (86, 182, 194), 37: FG, 90: DIM, 91: (240, 130, 140), 92: (140, 214, 152),
    93: (240, 208, 140), 94: (130, 205, 225), 95: (212, 145, 232), 96: (110, 200, 210),
    97: (255, 255, 255),
}
BRIGHT = {k: v for k, v in PALETTE.items() if k < 90}


def pick_font(candidates, size):
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default()


def parse_ansi(text):
    """Split text into (text, color, bold) runs.

    Consumes complete CSI sequences. A sequence that is not a recognised SGR
    one is dropped: it must never reach the rendered frame as literal text.
    """
    runs = []
    color = FG
    bold = False
    i = 0
    buf = []

    def flush():
        nonlocal buf
        if buf:
            runs.append(("".join(buf), color, bold))
            buf = []

    while i < len(text):
        ch = text[i]
        if ch == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and not ("@" <= text[j] <= "~"):
                j += 1
            if j >= len(text):
                break
            final = text[j]
            params = text[i + 2:j]
            if final == "m":
                flush()
                for code in params.split(";") if params else ["0"]:
                    try:
                        n = int(code) if code else 0
                    except ValueError:
                        continue
                    if n == 0:
                        color, bold = FG, False
                    elif n == 1:
                        bold = True
                    elif n == 2:
                        color = DIM
                    elif n == 22:
                        bold = False
                    elif n in PALETTE:
                        color = PALETTE[n]
                    elif n in BRIGHT:
                        color = BRIGHT[n]
                    elif n == 39:
                        color = FG
            i = j + 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return runs


def run_capture(cmd, cols=100, rows=30, env=None):
    """Run cmd in a PTY, return (frames, exit_code).

    Each frame is the full screen text after a chunk of output arrived.
    """
    master, slave = pty.openpty()
    try:
        import fcntl
        import struct
        import termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass

    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                            env=env or os.environ.copy(), close_fds=True)
    os.close(slave)

    screen = [""]
    pending = ""
    frames = [list(screen)]
    last_flush = time.time()

    while True:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            pending += chunk.decode("utf-8", "replace")
            # Flush on a short interval so the output appears progressively
            # rather than in one lump at the end.
            now = time.time()
            if now - last_flush > FLUSH_INTERVAL:
                screen = apply_ansi(screen, pending, cols, rows)
                pending = ""
                frames.append(list(screen))
                last_flush = now
        else:
            if proc.poll() is not None:
                # drain
                try:
                    while True:
                        chunk = os.read(master, 65536)
                        if not chunk:
                            break
                        pending += chunk.decode("utf-8", "replace")
                except OSError:
                    pass
                break
    if pending:
        screen = apply_ansi(screen, pending, cols, rows)
        frames.append(list(screen))
    os.close(master)
    code = proc.wait()
    # a couple of identical trailing frames so the last state lingers
    frames.append(list(frames[-1]))
    return frames, code


def apply_ansi(screen, text, cols, rows):
    """Apply output to a simple line-based screen buffer.

    A PTY sends "text\r\n" for a newline, so CR must mean "return to the
    start of this line" and LF must mean "advance" -- clearing the line on CR
    (as a naive implementation does) throws the text away.
    """
    lines = list(screen)
    cy = len(lines) - 1
    col = 0
    i = 0
    while i < len(text):
        ch = text[i]
        i += 1
        if ch == "\n":
            lines.append("")
            cy += 1
            col = 0
        elif ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            pad = 8 - (col % 8)
            if cy >= len(lines):
                lines.append("")
            line = lines[cy]
            while len(line) < col:
                line += " "
            lines[cy] = (line + " " * pad)[:cols]
            col += pad
        elif ch == "\x1b":
            # Copy the escape sequence through verbatim. The renderer parses
            # it for colour, so it must survive into the frame buffer; it is
            # never printed as literal text.
            seq = "\x1b"
            if i < len(text) and text[i] == "[":
                seq += "["
                i += 1
                while i < len(text) and not ("@" <= text[i] <= "~"):
                    seq += text[i]
                    i += 1
                if i < len(text):
                    seq += text[i]
                    i += 1
            if cy >= len(lines):
                lines.append("")
            lines[cy] = (lines[cy] + seq)[:cols]
            # an escape sequence occupies no columns
            continue
        elif ch >= " ":
            if cy >= len(lines):
                lines.append("")
            line = lines[cy]
            if col < len(line):
                line = line[:col] + ch + line[col + 1:]
            else:
                line = line + " " * (col - len(line)) + ch
            lines[cy] = line[:cols]
            col += 1
    # keep the tail
    if len(lines) > rows:
        lines = lines[-rows:]
    return lines


def render(lines, font, bold_font, cols, rows, title=None):
    """Render one frame to a PIL image."""
    cw = font.getlength("M")
    ch = int(font.size * 1.45)
    pad = 14
    header = 30 if title else 0
    W = int(cw * cols) + pad * 2
    H = ch * rows + pad * 2 + header
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    if title:
        d.rectangle([0, 0, W, header], fill=(24, 27, 35))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([12 + i * 16, 10, 22 + i * 16, 20], fill=c)
        d.text((66, 8), title, font=font, fill=DIM)

    y = pad + header
    for line in lines:
        x = pad
        for txt, color, bold in parse_ansi(line):
            f = bold_font if bold else font
            d.text((x, y), txt, font=f, fill=color)
            x += font.getlength(txt)
        y += ch
    return img


def main():
    if "--" not in sys.argv:
        print("usage: record_gif.py <out.gif> -- <command> [args...]", file=sys.stderr)
        return 2
    split = sys.argv.index("--")
    out = Path(sys.argv[1])
    cmd = sys.argv[split + 1:]
    if not cmd:
        print("no command given", file=sys.stderr)
        return 2

    cols, rows = 104, 44
    fsize = 15
    font = pick_font(FONT_CANDIDATES, fsize)
    bold_font = pick_font(BOLD_CANDIDATES, fsize)

    title = "$ " + " ".join(cmd)
    frames, code = run_capture(cmd, cols=cols, rows=rows)
    print(f"captured {len(frames)} frames, exit={code}", file=sys.stderr)

    images = [render(f, font, bold_font, cols, rows, title=title) for f in frames]

    # keep the GIF small: cap frames, and downscale long runs of identical frames
    max_frames = 60
    if len(images) > max_frames:
        step = len(images) / max_frames
        images = [images[int(i * step)] for i in range(max_frames)]

    durations = []
    for i in range(len(images)):
        if i + 1 < len(images) and images[i] == images[i + 1]:
            durations.append(120)
        else:
            durations.append(700)

    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out, save_all=True, append_images=images[1:], duration=durations, loop=0,
        optimize=True, disposal=2,
    )
    print(f"wrote {out} ({out.stat().st_size // 1024} KiB, {len(images)} frames)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
