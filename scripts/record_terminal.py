"""Record a real Linux command as a timed terminal cast, then render WebM and GIF."""

import argparse
import codecs
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import struct
import subprocess
import sys
import textwrap
import time

from PIL import Image, ImageDraw, ImageFont

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
WIDTH, HEIGHT, COLUMNS, ROWS, FPS = 1280, 800, 112, 31, 2


def readable(value):
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
        except (ValueError, TypeError):
            return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def claude_event(event):
    """Display public Claude messages and actual tool receipts without private reasoning."""
    kind = event.get("type")
    if kind in {"assistant", "user"}:
        message = event.get("message", {})
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                yield f"\nClaude: {block['text']}\n"
            elif block.get("type") == "tool_use":
                yield f"\nTool: {block.get('name', '')}\n{readable(block.get('input', {}))}\n"
            elif block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text")
                prefix = "Tool error" if block.get("is_error") else "Tool result"
                output = readable(content)
                if len(output) > 2400:
                    output = output[:1200] + "\n[... long tool output abbreviated for display ...]\n" + output[-1200:]
                yield f"\n{prefix}:\n{output}\n"
    elif kind == "result":
        if event.get("is_error"):
            yield f"\nClaude error: {event.get('result') or readable(event.get('errors', []))}\n"
        else:
            yield f"\nClaude completed ({event.get('subtype', 'success')}).\n"


def capture(args, prefix):
    master, slave = pty.openpty()
    import fcntl
    import termios
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    environment = dict(os.environ, TERM="dumb", NO_COLOR="1", COLUMNS=str(COLUMNS))
    process = subprocess.Popen(args.command, stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, env=environment,
                               start_new_session=True)
    os.close(slave)
    start = time.monotonic()
    events = []
    transcript = prefix.with_suffix(".txt")
    cast = prefix.with_suffix(".cast")
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pending = ""
    raw_file = args.raw_log.open("wb") if args.raw_log else None
    with transcript.open("w", encoding="utf-8") as text_file, cast.open("w", encoding="utf-8") as cast_file:
        cast_file.write(json.dumps(dict(version=2, width=COLUMNS, height=ROWS, timestamp=int(time.time()),
                                        title=args.title, env=dict(TERM="dumb"))) + "\n")

        def emit(value):
            value = ANSI.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
            if not value:
                return
            elapsed = round(time.monotonic() - start, 3)
            events.append((elapsed, value))
            cast_file.write(json.dumps([elapsed, "o", value], ensure_ascii=False) + "\n")
            cast_file.flush()
            text_file.write(value)
            text_file.flush()
            print(value, end="", flush=True)

        mode = "Claude Code public stream-json output | print mode" if args.stream_json else "Recorded Linux CLI output"
        emit(f"{args.title}\n{mode} | isolated local demo\n\n")
        if args.prompt_file:
            emit("Prompt:\n" + Path(args.prompt_file).read_text(encoding="utf-8").strip() + "\n")
        else:
            emit("$ " + shlex.join(args.command) + "\n")
        try:
            while True:
                ready, _, _ = select.select([master], [], [], 0.2)
                if not ready:
                    if process.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                if raw_file:
                    raw_file.write(data)
                    raw_file.flush()
                chunk = decoder.decode(data)
                if args.stream_json:
                    pending += chunk
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        try:
                            event = json.loads(line)
                        except ValueError:
                            emit(line + "\n")
                            continue
                        for formatted in claude_event(event):
                            emit(formatted)
                else:
                    emit(chunk)
            if pending.strip():
                emit(pending + "\n")
            code = process.wait(timeout=10)
            emit(f"\nProcess exit code: {code}\n")
        finally:
            os.close(master)
            if raw_file:
                raw_file.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    return events, code


def load_cast(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(event[0], event[2]) for line in lines[1:] if (event := json.loads(line))[1] == "o"]


def render(events, args, prefix):
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("No ffmpeg found. Pass --ffmpeg /absolute/path/to/ffmpeg (Playwright's bundled encoder supports WebM).")
    font_path = args.font or "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    font = ImageFont.truetype(font_path, 17)
    heading = ImageFont.truetype(font_path, 19)
    timeline, current = [], 0.0
    previous = 0.0
    for at, value in events:
        current += min(max(0, at - previous) / args.speed, args.idle_limit)
        wrapped = []
        for line in value.splitlines():
            wrapped.extend(textwrap.wrap(line.expandtabs(4), width=COLUMNS, replace_whitespace=False,
                                         drop_whitespace=False) or [""])
        for index in range(0, len(wrapped), 12):
            timeline.append((current, "\n".join(wrapped[index:index + 12])))
            current += 1.5 / args.speed
        previous = at
    duration = current + 4
    video_path = prefix.with_suffix(".webm")
    encoder = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe", "-vcodec", "mjpeg",
        "-r", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libvpx", "-b:v", "700k", "-crf", "20", str(video_path)], stdin=subprocess.PIPE)
    lines, offset, images = [], 0, []
    try:
        for frame_number in range(int(duration * FPS) + 1):
            moment = frame_number / FPS
            while offset < len(timeline) and timeline[offset][0] <= moment:
                for line in timeline[offset][1].splitlines():
                    lines.extend(textwrap.wrap(line.expandtabs(4), width=COLUMNS, replace_whitespace=False,
                                               drop_whitespace=False) or [""])
                offset += 1
            frame = Image.new("RGB", (WIDTH, HEIGHT), "#10151b")
            draw = ImageDraw.Draw(frame)
            draw.rounded_rectangle((16, 14, WIDTH - 16, HEIGHT - 15), radius=14, fill="#151d27", outline="#354458", width=1)
            draw.line((17, 65, WIDTH - 17, 65), fill="#354458", width=1)
            for index, color in enumerate(("#ef7970", "#e5bd6b", "#78bf8f")):
                x = 39 + index * 24
                draw.ellipse((x - 6, 34, x + 6, 46), fill=color)
            draw.text((128, 28), args.title, font=heading, fill="#e6edf3")
            for index, line in enumerate(lines[-ROWS:]):
                color = "#77dca3" if line.startswith(("Tool:", "Tool result:", "Claude completed")) else "#b7c7dd" if line.startswith("Claude:") else "#e6edf3"
                draw.text((37, 82 + index * 21), line, font=font, fill=color)
            note = f"Recorded CLI output | {args.speed:g}x, paced for reading | idle gaps limited to {args.idle_limit:g}s"
            draw.text((37, HEIGHT - 49), note, font=font, fill="#93a7bf")
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=87)
            encoder.stdin.write(buffer.getvalue())
            if args.gif and frame_number % 2 == 0:
                preview = frame.resize((960, 600)).convert("P", palette=Image.Palette.ADAPTIVE, colors=64)
                images.append(preview)
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError("Video encoder failed")
        if images:
            images[0].save(prefix.with_suffix(".gif"), save_all=True, append_images=images[1:], duration=1000, loop=0, optimize=True)
    finally:
        if encoder.poll() is None:
            encoder.kill()
            encoder.wait()
    print(f"\nSaved {video_path} ({video_path.stat().st_size / 1024 / 1024:.2f} MB, {duration:.1f}s)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Output filename prefix")
    parser.add_argument("--title", default="Claudex: Claude Code delegates to Codex")
    parser.add_argument("--stream-json", action="store_true", help="Format public Claude stream-json messages and tool receipts")
    parser.add_argument("--prompt-file", type=Path, help="Append this prompt to the real command and display its exact contents")
    parser.add_argument("--raw-log", type=Path, help="Optional private raw stdout/stderr log; review before sharing")
    parser.add_argument("--render-only", type=Path, help="Render an existing cast without running a command")
    parser.add_argument("--ffmpeg", help="Optional ffmpeg executable")
    parser.add_argument("--font", help="Optional monospace TrueType font")
    parser.add_argument("--speed", type=float, default=1)
    parser.add_argument("--idle-limit", type=float, default=4)
    parser.add_argument("--gif", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.speed <= 0 or args.idle_limit <= 0:
        parser.error("--speed and --idle-limit must be positive")
    prefix = args.output.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if args.render_only:
        events, code = load_cast(args.render_only), 0
    else:
        if args.command[:1] == ["--"]:
            args.command.pop(0)
        if not args.command:
            parser.error("Provide a command after --, or use --render-only")
        if args.prompt_file:
            args.command.append(args.prompt_file.read_text(encoding="utf-8"))
        events, code = capture(args, prefix)
    render(events, args, prefix)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
