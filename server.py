#!/usr/bin/env python3
"""DJV.TOOLS · Gen — Draw Things.

A thin, in-process wrapper over gen.py: every tool builds the argument list
`gen.parse_args` expects and runs gen's own pipeline (build_job -> render), so
the sizing, model and validation rules live in one place. Results are gen's JSON
payloads: `ok`, `display` (the files to attach), `files`, `seed`, `mode`, etc.
Errors come back as `ok:false` with `error` and `fix`.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import os
import tempfile
from pathlib import Path

import gen  # noqa: E402  (the engine, beside this file)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("djv-gen")

REPEAT = {"ref", "control", "lora"}


def _flags(**kw) -> list[str]:
    if not kw.get("out") and os.environ.get("DRAWTHINGS_OUTPUT_DIR"):
        kw["out"] = os.environ["DRAWTHINGS_OUTPUT_DIR"]
        kw["allow_outside"] = True
    out: list[str] = []
    for key, val in kw.items():
        if val is None or val is False or val == []:
            continue
        flag = "--" + key.replace("_", "-")
        if key in REPEAT:
            for item in val:
                out += [flag, str(item)]
        elif val is True:
            out.append(flag)
        else:
            out += [flag, str(val)]
    return out


def _await(coro):
    """Run a coroutine from sync code, even when a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


def _run(build, flags: list[str], prompt: str = "") -> dict:
    """Run gen's pipeline; always return a JSON-safe dict."""
    try:
        args = gen.parse_args(flags)
        if prompt:
            args.prompt = prompt
        job = build(args)
        if args.dry_run:
            return gen.job_report(job)
        if job.media == "video":
            gen.require_ffmpeg()
        return _await(gen.render(job))
    except gen.GenError as exc:
        return gen.fail(exc)
    except Exception as exc:  # never raise into the transport
        return {"ok": False, "error": gen.sanitize(exc)}


def _img(value: str | None, name: str) -> str | None:
    """Accept a path or a base64 / data-URL image; return a usable path."""
    if not value:
        return None
    if Path(value).expanduser().exists():
        return value
    data = value.split(",", 1)[-1] if value.startswith("data:") else value
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        return value  # let gen raise a clear "cannot read" error
    path = Path(tempfile.mkdtemp(prefix="dt-mcp-")) / f"{name}.png"
    path.write_bytes(raw)
    return str(path)


# --------------------------------------------------------------------------- tools


@mcp.tool()
def generate(
    prompt: str,
    quality: str | None = None,
    model: str | None = None,
    size: str | None = None,
    count: int | None = None,
    seed: int | None = None,
    steps: int | None = None,
    negative: str | None = None,
    lora: list[str] | None = None,
    out: str | None = None,
    allow_outside: bool = False,
    timeout: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Text-to-image. Don't ask the user for options — pick sensible ones.
    quality: draft|standard|hero (never combine with model). size: 16:9, 4:3,
    3:2, 1:1, 2:3, 3:4, 9:16, "1344x768", or "1024". count: 1-4.
    Models: klein4b (default), klein9b, dev (best, slow first load), ernie (photo).
    `dev` may need timeout >= 900 on first use. Attach every file in `display`.
    """
    return _run(
        gen.build_job,
        _flags(quality=quality, model=model, size=size, count=count, seed=seed,
               steps=steps, negative=negative, lora=lora, out=out,
               allow_outside=allow_outside, timeout=timeout, dry_run=dry_run),
        prompt,
    )


@mcp.tool()
def transform(
    prompt: str,
    init: str | None = None,
    strength: float | None = None,
    ref: list[str] | None = None,
    control: list[str] | None = None,
    quality: str | None = None,
    model: str | None = None,
    size: str | None = None,
    count: int | None = None,
    seed: int | None = None,
    steps: int | None = None,
    lora: list[str] | None = None,
    out: str | None = None,
    allow_outside: bool = False,
    timeout: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Edit or restyle an image. `init` is the source (path or base64/data-URL);
    strength defaults to 0.7 (0.9-1.0 repaints with the Klein models). `ref` adds
    reference images; `control` takes "FILE:TYPE" (e.g. pose.png:pose). With
    `init`, the source is <Picture 1>; without it, the first ref is.
    """
    return _run(
        gen.build_job,
        _flags(init=_img(init, "init"), strength=strength,
               ref=[_img(r, f"ref{i}") for i, r in enumerate(ref or [])],
               control=control, quality=quality, model=model, size=size,
               count=count, seed=seed, steps=steps, lora=lora, out=out,
               allow_outside=allow_outside, timeout=timeout, dry_run=dry_run),
        prompt,
    )


@mcp.tool()
def video(
    prompt: str,
    init: str | None = None,
    ref: list[str] | None = None,
    quality: str | None = None,
    model: str | None = None,
    size: str | None = None,
    seconds: float | None = None,
    frames: int | None = None,
    h264: bool = False,
    seed: int | None = None,
    steps: int | None = None,
    out: str | None = None,
    allow_outside: bool = False,
    timeout: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Render a short clip (h3 family). Mode follows inputs: t2v, i2v (init),
    flf2v (init + one ref), ref2v (model h3ref + refs). Default is a ProRes 4444
    .mov; h264=true gives a small .mp4. Use seconds or frames, not both. Needs
    ffmpeg. `display` holds the poster + contact sheet to attach — never the .mov.
    """
    return _run(
        gen.build_job,
        _flags(video=True, init=_img(init, "init"),
               ref=[_img(r, f"ref{i}") for i, r in enumerate(ref or [])],
               quality=quality, model=model, size=size, seconds=seconds,
               frames=frames, h264=h264, seed=seed, steps=steps, out=out,
               allow_outside=allow_outside, timeout=timeout, dry_run=dry_run),
        prompt,
    )


@mcp.tool()
def replay(
    source: str,
    quality: str | None = None,
    model: str | None = None,
    size: str | None = None,
    seed: int | None = None,
    count: int | None = None,
    seconds: float | None = None,
    h264: bool = False,
    out: str | None = None,
    allow_outside: bool = False,
    timeout: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Reproduce a previous render from a PNG (config embedded) or a job record
    (.json). Flags passed here override the record.
    """
    return _run(
        lambda a: gen.build_replay_job(a, gen.read_prompt(a)),
        _flags(**{"from": source}, quality=quality, model=model, size=size,
               seed=seed, count=count, seconds=seconds, h264=h264, out=out,
               allow_outside=allow_outside, timeout=timeout, dry_run=dry_run),
    )


@mcp.tool()
def status(probe: bool = False) -> dict:
    """Offline: version, models, and sizes. probe=true also checks whether the
    Draw Things API server answers (needs gRPC+TLS on port 7859).
    """
    out = {"version": gen.VERSION, "quality": gen.QUALITY,
           "models": gen.models_report(), "sizes": gen.sizes_report()}
    if probe:
        out["server"] = _await(gen.probe())
    return out


@mcp.tool()
def guide() -> dict:
    """Model cheat-sheet straight from gen.MODELS: each model's purpose
    (`note`), media, aliases, and limits (`refs_max`, `init`, `control`,
    `negative`), plus the quality->model tiers. Read-only.
    """
    return {**gen.models_report(), "quality": gen.QUALITY}


if __name__ == "__main__":
    mcp.run()
