"""Record actual pixels from an explicitly selected, isolated Linux X11 display."""

import argparse
import io
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

from PIL import Image, ImageDraw, ImageGrab


def highlight_pointer(screen, x, y, pulse):
    """Add only a pointer highlight at the position observed on the X11 display."""
    overlay = Image.new("RGBA", screen.size)
    draw = ImageDraw.Draw(overlay)
    if pulse is not None:
        px, py, progress = pulse
        radius = 8 + progress * 24
        draw.ellipse((px - radius, py - radius, px + radius, py + radius),
                     outline=(154, 205, 255, int(190 * (1 - progress))), width=3)
    arrow = [(x, y), (x, y + 24), (x + 6, y + 18), (x + 11, y + 28),
             (x + 15, y + 26), (x + 10, y + 16), (x + 20, y + 15)]
    draw.polygon([(px + 1, py + 1) for px, py in arrow], fill=(0, 0, 0, 110))
    draw.polygon(arrow, fill=(255, 255, 255, 255), outline=(25, 32, 42, 255), width=2)
    return Image.alpha_composite(screen.convert("RGBA"), overlay).convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", required=True, help="Dedicated local Xvfb display, for example :97")
    parser.add_argument("--output", type=Path, required=True, help="Output .webm video")
    parser.add_argument("--ffmpeg", help="ffmpeg executable with MJPEG decoding and libvpx encoding")
    parser.add_argument("--duration", type=float, default=180, help="Maximum real recording duration in seconds")
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--bitrate", default="900k")
    parser.add_argument("--cursor", action="store_true", help="Highlight the actual X11 pointer and observed button presses; requires python-xlib")
    parser.add_argument("--stop-file", type=Path, help="Finish the recording when this file exists")
    parser.add_argument("--ready-file", type=Path, help="Write capture information after the first frame")
    parser.add_argument("--qa-dir", type=Path, help="Private directory for unmodified PNG screen grabs")
    parser.add_argument("--qa-every", type=float, default=10)
    args = parser.parse_args()
    if not re.fullmatch(r":\d+(?:\.\d+)?", args.display):
        parser.error("--display must explicitly name a local dedicated X11 display")
    if args.duration <= 0 or not 1 <= args.fps <= 30 or args.qa_every <= 0:
        parser.error("Duration and QA interval must be positive; FPS must be 1-30")
    if args.output.suffix != ".webm":
        parser.error("--output must end in .webm")
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("Pass --ffmpeg when ffmpeg is outside PATH")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.qa_dir:
        args.qa_dir.mkdir(parents=True, exist_ok=True)
    pointer_display = None
    pointer_log = None
    buttons_before, clicked_at, clicked_position = 0, -1.0, (0, 0)
    if args.cursor:
        try:
            from Xlib import X, display
        except ImportError:
            parser.error("--cursor requires the optional python-xlib package")
        pointer_display = display.Display(args.display)
        pointer_root = pointer_display.screen().root
        button_mask = X.Button1Mask | X.Button2Mask | X.Button3Mask
        if args.qa_dir:
            pointer_log = (args.qa_dir / "pointer.jsonl").open("w", encoding="utf-8")
    stop = False

    def finish(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, finish)
    signal.signal(signal.SIGTERM, finish)
    encoder = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-vcodec", "mjpeg",
        "-r", str(args.fps), "-i", "pipe:0", "-an", "-c:v", "libvpx", "-b:v", args.bitrate, "-crf", "20", str(args.output)],
        stdin=subprocess.PIPE)
    started, frames, next_qa = time.monotonic(), 0, 0.0
    qa = []
    try:
        while not stop:
            elapsed = time.monotonic() - started
            if elapsed >= args.duration or args.stop_file and args.stop_file.exists():
                break
            screen = ImageGrab.grab(xdisplay=args.display)
            frame = screen
            if pointer_display:
                pointer = pointer_root.query_pointer()
                x, y = pointer.root_x, pointer.root_y
                buttons = pointer.mask & button_mask
                if buttons & ~buttons_before:
                    clicked_at, clicked_position = elapsed, (x, y)
                buttons_before = buttons
                progress = (elapsed - clicked_at) / 0.45
                pulse = (*clicked_position, progress) if clicked_at >= 0 and 0 <= progress < 1 else None
                frame = highlight_pointer(screen, x, y, pulse)
                if pointer_log:
                    pointer_log.write(json.dumps(dict(elapsed=round(elapsed, 3), x=x, y=y, buttons=buttons)) + "\n")
                    pointer_log.flush()
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=91)
            encoder.stdin.write(buffer.getvalue())
            frames += 1
            if frames == 1:
                info = dict(display=args.display, size=screen.size, fps=args.fps, output=str(args.output), cursor_highlight=args.cursor)
                if args.ready_file:
                    args.ready_file.write_text(json.dumps(info))
                print(json.dumps(info), flush=True)
            if args.qa_dir and elapsed >= next_qa:
                filename = f"frame-{elapsed:07.2f}.png"
                screen.save(args.qa_dir / filename)
                qa.append(dict(elapsed=round(elapsed, 3), file=filename))
                next_qa += args.qa_every
            delay = started + frames / args.fps - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        encoder.stdin.close()
        if encoder.wait(timeout=30) != 0:
            raise RuntimeError("Screen video encoder failed")
        if args.qa_dir:
            ImageGrab.grab(xdisplay=args.display).save(args.qa_dir / "final.png")
            (args.qa_dir / "frames.json").write_text(json.dumps(qa, indent=2))
        print(json.dumps(dict(frames=frames, video_seconds=round(frames / args.fps, 2),
                              elapsed_seconds=round(time.monotonic() - started, 2), bytes=args.output.stat().st_size)), flush=True)
    finally:
        if pointer_log:
            pointer_log.close()
        if pointer_display:
            pointer_display.close()
        if encoder.poll() is None:
            encoder.kill()
            encoder.wait()


if __name__ == "__main__":
    main()
