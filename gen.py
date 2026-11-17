#!/usr/bin/env python3
"""Gen — images, edits, and short videos with local Draw Things models.

One command, one JSON object on stdout. Run ``gen.py --help`` for the flags.

The module imports only the standard library at startup. ``drawthings_py``,
Pillow, numpy, and imageio-ffmpeg are imported inside the functions that need
them, so ``--version``, ``--models``, ``--sizes``, and ``--dry-run`` answer in
a few milliseconds and work with the server offline.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

VERSION = "2.0.0"

HERE = Path(__file__).resolve().parent
PRESETS = HERE / "presets"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7859
DEFAULT_TIMEOUT = 600
DEFAULT_VIDEO_TIMEOUT = 3600
DEFAULT_STRENGTH = 0.7
MAX_SEED = 2**32 - 1
MAX_PROMPT_BYTES = 32 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_COUNT = 4
MIN_DIM = 128
MAX_DIM = 2048
GRID = 64
MIN_FRAMES = 1
MAX_FRAMES = 362

#: Exact sizes for a 1 MP image preset. The two numbers are a multiple of 64
#: and close to the requested shape; the model never has to do this arithmetic.
RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (1344, 768),
    "4:3": (1280, 960),
    "3:2": (1152, 768),
    "1:1": (1024, 1024),
    "2:3": (768, 1152),
    "3:4": (960, 1280),
    "9:16": (768, 1344),
}

#: Video tiers are a fraction of the preset canvas area.
TIERS = {"draft": 0.4, "standard": 1.0, "hero": 2.0}

CONTROL_TYPES = ("depth", "pose", "scribble", "color", "shuffle", "custom")

MODELS: dict[str, dict[str, Any]] = {
    "klein4b": {
        "label": "FLUX.2 Klein 4B",
        "preset": "flux_2_klein_4b.json",
        "media": "image",
        "aliases": ("klein", "4b", "fast", "klein-4b"),
        "refs": 4,
        "init": True,
        "control": True,
        "negative": False,
        "note": "Default. Fastest, about 2s at 1024.",
    },
    "klein9b": {
        "label": "FLUX.2 Klein 9B KV",
        "preset": "flux_2_klein_9b_kv.json",
        "media": "image",
        "aliases": ("9b", "kv"),
        "refs": 4,
        "init": True,
        "control": True,
        "negative": False,
        "note": "More detail, still 4 steps.",
    },
    "dev": {
        "label": "FLUX.2 dev",
        "preset": "flux_2_dev.json",
        "media": "image",
        "aliases": ("flux-dev", "flux2-dev", "quality", "hero", "best"),
        "refs": 6,
        "init": True,
        "control": True,
        "negative": False,
        "note": "Best detail, 28 steps, roughly a minute.",
    },
    "ernie": {
        "label": "ERNIE Image Base 1.0",
        "preset": "ernie_image_base.json",
        "media": "image",
        "aliases": ("ernie-base",),
        "refs": 0,
        "init": True,
        "control": True,
        "negative": True,
        "note": "Photographic alternative, 30 steps. Negative prompts work.",
    },
    "h3": {
        "label": "MiniMax H3",
        "preset": "minimax_h3_fl2va.json",
        "media": "video",
        "aliases": ("minimax", "minimax-h3", "fl2va"),
        "refs": 1,
        "init": True,
        "control": False,
        "negative": False,
        "fps": 24,
        "note": "Video with audio. First frame, or first and last frame.",
    },
    "h3ref": {
        "label": "MiniMax H3 [ref2va]",
        "preset": "minimax_h3_ref2va.json",
        "media": "video",
        "aliases": ("h3-ref", "ref2v", "minimax-ref"),
        "refs": 9,
        "init": False,
        "control": False,
        "negative": False,
        "fps": 24,
        "note": "Video from up to 9 reference images.",
    },
    "ltx": {
        "label": "LTX-2.3 22B distilled",
        "preset": "ltx_2_3_distilled.json",
        "media": "video",
        "aliases": ("ltx2", "ltx-distilled"),
        "refs": 0,
        "init": True,
        "control": False,
        "negative": False,
        "fps": 25,
        "note": "Fast video without references.",
    },
}

QUALITY = {
    "image": {"draft": "klein4b", "standard": "klein9b", "hero": "dev"},
    "video": {"draft": "ltx", "standard": "h3", "hero": "h3"},
}

ALIASES: dict[str, str] = {}
for _key, _entry in MODELS.items():
    ALIASES[_key] = _key
    for _alias in _entry["aliases"]:
        ALIASES[_alias] = _key

STOP_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "of", "in", "on",
    "at", "with", "and", "or", "for", "to", "from", "my", "our", "your",
}


# ---------------------------------------------------------------------------
# Errors and output
# ---------------------------------------------------------------------------


class GenError(Exception):
    """A failure the caller can report; ``fix`` is one concrete next step."""

    def __init__(self, message: str, fix: str | None = None) -> None:
        super().__init__(message)
        self.fix = fix


class UsageError(GenError):
    """The request itself is wrong."""


def sanitize(value: Any, limit: int = 300) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def log(message: str) -> None:
    print(f"[gen] {sanitize(message, 500)}", file=sys.stderr, flush=True)


def fail(exc: GenError) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": sanitize(exc)}
    if exc.fix:
        payload["fix"] = exc.fix
    return payload


# ---------------------------------------------------------------------------
# Data and settings
# ---------------------------------------------------------------------------


def load_preset_file(name: str) -> dict[str, Any]:
    path = PRESETS / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GenError(f"Cannot read preset {name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GenError(f"Preset {name} is not valid JSON: {exc}") from exc
    configuration = data.get("configuration", data)
    if not isinstance(configuration, dict):
        raise GenError(f"Preset {name} does not contain a configuration object.")
    return dict(configuration)


def load_preset(key: str) -> dict[str, Any]:
    return load_preset_file(str(MODELS[key]["preset"]))


def model_base(file_name: str) -> str:
    """``flux_2_klein_4b_q6p.ckpt`` -> ``flux_2_klein_4b``."""
    return re.sub(
        r"_(q\d+p|i\d+x|f16|bf16|f32)$", "", Path(file_name).stem
    ).lower()


def key_for_model(file_name: str | None) -> str | None:
    if not file_name:
        return None
    wanted = model_base(file_name)
    for key in MODELS:
        if model_base(str(load_preset(key).get("model") or "")) == wanted:
            return key
    return None


def resolve_installed(requested: str, files: Sequence[str]) -> str:
    """Pick an installed variant of the preset model (q6p, i8x, ...)."""
    by_lower = {name.lower(): name for name in files}
    if requested.lower() in by_lower:
        return by_lower[requested.lower()]

    base = model_base(requested)
    candidates = [name for name in files if model_base(name) == base]
    if not candidates:
        raise GenError(
            f"The model {Path(requested).name} is not installed on the Draw "
            "Things server.",
            fix="Download it in Draw Things, then retry. Images still work "
            "with klein4b.",
        )

    preference = ("i8x", "q6p", "q8p", "f16", "bf16", "f32")

    def rank(name: str) -> tuple[int, str]:
        match = re.search(r"_(q\d+p|i\d+x|f16|bf16|f32)$", Path(name).stem)
        suffix = match.group(1) if match else ""
        return (preference.index(suffix) if suffix in preference else 99, name)

    return sorted(candidates, key=rank)[0]


def installed_files(info: Any) -> list[str]:
    models = getattr(info, "models", None) or []
    names = {item["file"] for item in models if isinstance(item, dict) and item.get("file")}
    names.update(name for name in getattr(info, "files", None) or [] if isinstance(name, str))
    return sorted(names, key=str.lower)


def connection() -> tuple[str, int]:
    config = Path(os.environ.get("DRAWTHINGS_CONFIG", HERE / "config.env"))
    if config.is_file():
        try:
            lines = config.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    host = os.environ.get("DRAWTHINGS_HOST", "").strip() or DEFAULT_HOST
    try:
        port = int(os.environ.get("DRAWTHINGS_PORT", "") or DEFAULT_PORT)
    except ValueError as exc:
        raise UsageError("DRAWTHINGS_PORT must be a number.") from exc
    return host, port


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------


def snap(value: float) -> int:
    number = int(round(value / GRID) * GRID)
    return max(MIN_DIM, min(MAX_DIM, number))


def validate_size(width: int, height: int) -> tuple[int, int]:
    for value, name in ((width, "width"), (height, "height")):
        if not MIN_DIM <= value <= MAX_DIM:
            raise UsageError(
                f"{name} must be between {MIN_DIM} and {MAX_DIM} pixels."
            )
        if value % GRID:
            raise UsageError(f"{name} must be a multiple of {GRID} (got {value}).")
    return width, height


def parse_exact(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)\s*[x:]\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"(\d+)", text)
    if match:
        side = int(match.group(1))
        return side, side
    return None


def normalize_ratio(text: str) -> str:
    return text.strip().lower().replace("x", ":").replace(" ", "")


def ratio_value(ratio: str) -> float:
    width, height = (int(part) for part in ratio.split(":"))
    return width / height


def image_size(spec: str | None, settings: dict[str, Any]) -> tuple[int, int]:
    if not spec:
        return validate_size(
            int(settings.get("width") or 1024), int(settings.get("height") or 1024)
        )
    text = spec.strip().lower()
    if "@" in text:
        raise UsageError("Video tiers (@draft, @standard, @hero) do not apply to images.")
    ratio = normalize_ratio(text)
    if ratio in RATIOS:
        return RATIOS[ratio]
    exact = parse_exact(text)
    if exact:
        return validate_size(*exact)
    raise UsageError(
        f"--size must be a ratio ({', '.join(RATIOS)}), WxH, or a single "
        f"side (got {sanitize(spec, 40)})."
    )


def video_size(
    spec: str | None, settings: dict[str, Any]
) -> tuple[int, int]:
    if not spec:
        return validate_size(
            int(settings.get("width") or 832), int(settings.get("height") or 512)
        )

    text = spec.strip().lower()
    tier = "standard"
    if "@" in text:
        text, tier = (part.strip() for part in text.split("@", 1))
    if tier not in TIERS:
        raise UsageError(
            "--size tier must be draft, standard, or hero (got "
            f"{sanitize(tier, 20)})."
        )

    ratio: str | None = None
    short: int | None = None
    if text in TIERS:
        tier = text
    elif text:
        normalized = normalize_ratio(text)
        if normalized in RATIOS:
            ratio = normalized
        elif text.isdigit():
            short = int(text)
        else:
            exact = parse_exact(text)
            if exact:
                if "@" in spec:
                    raise UsageError("Use either an exact size or @tier, not both.")
                return validate_size(*exact)
            raise UsageError(
                f"--size must be a ratio, WxH, a tier, or a short edge "
                f"(got {sanitize(spec, 40)})."
            )

    ratio = ratio or "16:9"
    base_width = int(settings.get("width") or 832)
    base_height = int(settings.get("height") or 512)

    if short is not None:
        if not MIN_DIM <= short <= MAX_DIM:
            raise UsageError(f"The short edge must be {MIN_DIM}-{MAX_DIM} px.")
        short = snap(short)
        value = ratio_value(ratio)
        if value >= 1:
            return validate_size(snap(short * value), short)
        return validate_size(short, snap(short / value))

    area = base_width * base_height * TIERS[tier]
    value = ratio_value(ratio)
    if value >= 1:
        width = snap((area * value) ** 0.5)
        return validate_size(width, snap(width / value))
    height = snap((area / value) ** 0.5)
    return validate_size(snap(height * value), height)


# ---------------------------------------------------------------------------
# Inputs, filenames, and output folders
# ---------------------------------------------------------------------------


def read_prompt(args: argparse.Namespace) -> str:
    """The prompt for this job; the client passes it directly."""
    return (args.prompt or "").replace("\x00", "").strip()


def image_path(raw: str, flag: str) -> Path:
    path = Path(raw).expanduser()
    try:
        info = path.stat()
    except OSError as exc:
        raise UsageError(f"{flag} {raw}: cannot read the image ({exc}).") from exc
    if not path.is_file():
        raise UsageError(f"{flag} {raw} is not a file.")
    if info.st_size > MAX_INPUT_BYTES:
        raise UsageError(f"{flag} {raw} is larger than 64 MiB.")
    return path.resolve()


def control_specs(values: Sequence[str]) -> tuple[tuple[Path, str], ...]:
    controls: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for raw in values:
        if ":" not in raw:
            raise UsageError(
                "Use --control FILE:TYPE, for example --control pose.png:pose."
            )
        name, kind = raw.rsplit(":", 1)
        kind = kind.strip().lower()
        if kind not in CONTROL_TYPES:
            raise UsageError(
                f"Control type must be one of: {', '.join(CONTROL_TYPES)}."
            )
        if kind in seen:
            raise UsageError(f"Only one --control image per type ({kind}).")
        seen.add(kind)
        controls.append((image_path(name, "--control"), kind))
    return tuple(controls)


def slugify(prompt: str) -> str:
    text = (
        unicodedata.normalize("NFKD", prompt)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    words = re.findall(r"[a-z0-9]+", text)
    while words and words[0] in STOP_WORDS:
        words.pop(0)
    kept: list[str] = []
    for word in words:
        if len("_".join(kept + [word])) > 24:
            break
        kept.append(word)
    while kept and kept[-1] in STOP_WORDS:
        kept.pop()
    return "_".join(kept) or "image"


def output_dir(raw: str | None, allow_outside: bool) -> Path:
    project = Path.cwd().resolve()
    out = Path(raw).expanduser() if raw else project / "gen"
    if not out.is_absolute():
        out = project / out
    out = out.resolve()
    if not allow_outside and not out.is_relative_to(project):
        raise UsageError(
            "--out must stay inside the current project folder. Ask the user "
            "before writing elsewhere, then add --allow-outside."
        )
    if out.exists() and not out.is_dir():
        raise GenError(f"Output path is not a folder: {out}")
    return out


def allocate(directory: Path, prefix: str, suffix: str = ".png") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(1, 10000):
        path = directory / f"{prefix}_{number:04d}{suffix}"
        try:
            with open(path, "xb"):
                pass
            return path
        except FileExistsError:
            continue
    raise GenError(f"Could not find a free filename for {prefix!r}.")


# ---------------------------------------------------------------------------
# The resolved job
# ---------------------------------------------------------------------------


@dataclass
class Job:
    mode: str
    media: str
    model: str
    settings: dict[str, Any]
    prompt: str
    negative: str | None
    seed: int
    width: int
    height: int
    count: int
    out_dir: Path
    slug: str
    refs: tuple[Path, ...] = ()
    init: Path | None = None
    controls: tuple[tuple[Path, str], ...] = ()
    strength: float | None = None
    loras: tuple[tuple[str, float], ...] = ()
    fps: int | None = None
    frames: int | None = None
    master: bool = True
    timeout: float = DEFAULT_TIMEOUT
    source: Path | None = None


def infer_mode(job: Job) -> str:
    if job.media == "video":
        if job.init is not None and job.refs:
            return "flf2v"
        if job.init is not None:
            return "i2v"
        if job.refs:
            return "ref2v"
        return "t2v"
    if job.refs:
        return "ref2img"
    if job.init is not None:
        return "img2img"
    return "txt2img"


def select_model(
    args: argparse.Namespace, media: str, fallback: str | None
) -> tuple[str, dict[str, Any]]:
    """Resolve --quality / --model / fallback to a known model for this media."""
    if args.quality and args.model:
        raise UsageError("Use --quality or --model, not both.")
    if args.model:
        requested = args.model.strip().lower()
        key = ALIASES.get(requested) or ALIASES.get(requested.replace("_", "-"))
        if not key:
            raise UsageError(
                f"Unknown model {sanitize(args.model, 40)}. Use one of: "
                f"{', '.join(MODELS)}."
            )
        if MODELS[key]["media"] != media:
            raise UsageError(
                f"{key} makes {MODELS[key]['media']}s; this request is "
                f"{'a video' if media == 'video' else 'an image'}."
            )
        return key, MODELS[key]
    if args.quality:
        key = QUALITY[media][args.quality]
        return key, MODELS[key]
    if fallback:
        return fallback, MODELS[fallback]
    raise UsageError(f"No model for this {media}; pass --model.")


def parse_loras(values: Sequence[str]) -> tuple[tuple[str, float], ...]:
    parsed: list[tuple[str, float]] = []
    for raw in values:
        name, _, weight_text = raw.partition(":")
        name = name.strip()
        if not name:
            raise UsageError("--lora needs a file name.")
        try:
            weight = float(weight_text) if weight_text else 1.0
        except ValueError as exc:
            raise UsageError(f"--lora weight must be a number: {raw}") from exc
        if not 0.0 <= weight <= 3.0:
            raise UsageError("--lora weight must be between 0.0 and 3.0.")
        parsed.append((name, weight))
    return tuple(parsed)


def fit_frames(value: int, key: str) -> int:
    if key == "ltx":
        value = max(9, min(257, value))
        return 8 * round((value - 1) / 8) + 1
    return max(MIN_FRAMES, min(MAX_FRAMES, value))


def choose_seed(value: int | None) -> int:
    if value is None:
        return random.SystemRandom().randint(1, MAX_SEED)
    if not 0 <= value <= MAX_SEED:
        raise UsageError(f"--seed must be between 0 and {MAX_SEED}.")
    return value


def check_inputs(
    entry: dict[str, Any],
    refs: Sequence[Path],
    init: Path | None,
    controls: Sequence[tuple[Path, str]],
    media: str,
    key: str,
) -> None:
    """Input rules a model imposes; shared by build_job and build_replay_job."""
    if len(refs) > int(entry["refs"]):
        raise UsageError(
            f"{entry['label']} accepts at most {entry['refs']} reference "
            f"image(s); got {len(refs)}."
        )
    if init is not None and not entry["init"]:
        raise UsageError(f"{entry['label']} does not take --init.")
    if controls and not entry["control"]:
        raise UsageError(f"{entry['label']} does not take --control images.")
    if media == "video" and key == "h3ref" and not refs:
        raise UsageError("h3ref needs at least one --ref image.")
    if media == "video" and key == "h3" and refs and init is None:
        raise UsageError(
            "h3 uses a --ref as the last frame; add --init for the first frame."
        )


def resolve_strength(
    args: argparse.Namespace, init: Path | None, media: str, default: float | None
) -> float | None:
    if media == "video":
        if args.strength is not None:
            raise UsageError("--strength is image-only; --init is the first video frame.")
        return None
    if args.strength is not None:
        if init is None:
            raise UsageError("--strength only applies together with --init.")
        value = args.strength
    elif init is None:
        return None
    else:
        value = DEFAULT_STRENGTH if default is None else float(default)
    if not 0.0 <= value <= 1.0:
        raise UsageError("--strength must be between 0.0 and 1.0.")
    return value


def resolve_frames(
    args: argparse.Namespace,
    entry: dict[str, Any],
    settings: dict[str, Any],
    key: str,
    media: str,
    recorded_fps: Any = None,
    recorded_frames: Any = None,
) -> tuple[int | None, int | None]:
    if media != "video":
        if args.seconds is not None or args.frames is not None:
            raise UsageError("--seconds and --frames only apply to video.")
        return None, None
    if args.seconds is not None and args.frames is not None:
        raise UsageError("Use --seconds or --frames, not both.")
    fps = int(recorded_fps or entry.get("fps") or settings.get("fps") or 24)
    if args.frames is not None:
        return fps, fit_frames(args.frames, key)
    if args.seconds is not None:
        if not 0 < args.seconds <= 60:
            raise UsageError("--seconds must be between 0 and 60.")
        return fps, fit_frames(int(round(args.seconds * fps)), key)
    default = recorded_frames or settings.get("numFrames") or 49
    return fps, fit_frames(int(default), key)


def build_job(args: argparse.Namespace) -> Job:
    prompt = read_prompt(args)
    if not prompt:
        raise UsageError("No prompt was supplied.")
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise UsageError("The prompt is larger than 32 KiB.")

    media = "video" if args.video else "image"
    key, entry = select_model(args, media, "h3" if media == "video" else "klein4b")
    settings = load_preset(key)

    refs = tuple(image_path(value, "--ref") for value in args.ref or ())
    init = image_path(args.init, "--init") if args.init else None
    controls = control_specs(args.control or ())
    loras = parse_loras(args.lora or ())
    check_inputs(entry, refs, init, controls, media, key)

    if args.h264 and media == "image":
        raise UsageError("--h264 only applies to video.")

    if media == "image":
        width, height = image_size(args.size, settings)
        count = 1 if args.count is None else args.count
        if not 1 <= count <= MAX_COUNT:
            raise UsageError(f"--count must be between 1 and {MAX_COUNT}.")
    else:
        width, height = video_size(args.size, settings)
        count = 1
        if args.count is not None and args.count != 1:
            raise UsageError("Video renders one clip per run; --count is image-only.")

    if args.steps is not None:
        if args.steps < 1:
            raise UsageError("--steps must be at least 1.")
        settings["steps"] = args.steps

    strength = resolve_strength(args, init, media, None)
    fps, frames = resolve_frames(args, entry, settings, key, media)

    negative = (args.negative or "").strip()
    if negative and not entry["negative"]:
        log(f"{entry['label']} ignores negative prompts; dropping --negative.")
        negative = ""

    job = Job(
        mode="",
        media=media,
        model=key,
        settings=settings,
        prompt=prompt,
        negative=negative or None,
        seed=choose_seed(args.seed),
        width=width,
        height=height,
        count=count,
        out_dir=output_dir(args.out, args.allow_outside),
        slug=slugify(prompt),
        refs=refs,
        init=init,
        controls=controls,
        strength=strength,
        loras=loras,
        fps=fps,
        frames=frames,
        master=not args.h264,
        timeout=args.timeout
        or (DEFAULT_VIDEO_TIMEOUT if media == "video" else DEFAULT_TIMEOUT),
    )
    job.mode = infer_mode(job)
    return job


# ---------------------------------------------------------------------------
# Replay: the PNG's own XMP is the job record
# ---------------------------------------------------------------------------


def read_xmp(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - installed with the SDK
        raise GenError("Pillow is missing from the skill environment.") from exc

    try:
        info = Image.open(path).info
    except OSError as exc:
        raise GenError(f"Could not read {path.name}: {exc}") from exc
    raw = info.get("xmp") or info.get("XML:com.adobe.xmp")
    if not raw:
        raise GenError(
            f"{path.name} has no Draw Things metadata; it was stripped or not "
            "made by this tool.",
            fix="Pass the original image, or re-run the prompt.",
        )
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    match = re.search(
        r"<exif:UserComment>.*?<rdf:li[^>]*>(.*?)</rdf:li>", text, re.S
    )
    if not match:
        raise GenError(f"{path.name} has no Draw Things settings in its XMP.")
    try:
        payload = json.loads(html.unescape(match.group(1).strip()))
    except json.JSONDecodeError as exc:
        raise GenError(f"{path.name} has unreadable Draw Things settings.") from exc
    settings = payload.get("v2")
    if not isinstance(settings, dict):
        raise GenError(f"{path.name} does not contain a v2 Draw Things config.")
    return {
        "prompt": payload.get("c") or "",
        "negative": payload.get("uc") or "",
        "settings": settings,
        "seed": settings.get("seed"),
        "width": settings.get("width"),
        "height": settings.get("height"),
        "model_file": settings.get("model"),
        "fps": settings.get("fps"),
        "frames": settings.get("numFrames") or settings.get("num_frames"),
    }


def read_source(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GenError(f"Could not read the job record {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise GenError(f"{path.name} is not a job record.")
        return data

    if suffix in {".mp4", ".mov"}:
        sidecar = path.with_suffix(".json")
        if not sidecar.is_file():
            raise GenError(
                f"{path.name} has no job record next to it ({sidecar.name}).",
                fix="Replay the video's .json record instead.",
            )
        return read_source(sidecar)

    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise GenError(f"Cannot replay {path.name}; use a PNG or a .json record.")

    xmp = read_xmp(path)
    record = {
        "version": VERSION,
        "mode": "",
        "media": "image",
        "model": key_for_model(xmp.get("model_file")),
        "prompt": xmp["prompt"],
        "negative": xmp["negative"],
        "seed": xmp["seed"],
        "width": xmp["width"],
        "height": xmp["height"],
        "settings": xmp["settings"],
        "fps": xmp.get("fps"),
        "frames": xmp.get("frames"),
    }

    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        try:
            extra = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            extra = {}
        if isinstance(extra, dict):
            for field in ("init", "refs", "controls", "strength", "count", "master"):
                if field in extra and field not in record:
                    record[field] = extra[field]
    return record


def build_replay_job(args: argparse.Namespace, prompt: str) -> Job:
    source = Path(args.replay).expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    if not source.is_file():
        raise UsageError(f"--from {args.replay} is not a file.")
    record = read_source(source)

    record_settings = record.get("settings")
    source_key = record.get("model") or (
        key_for_model(record_settings.get("model"))
        if isinstance(record_settings, dict)
        else None
    )
    source_key = source_key if source_key in MODELS else None
    source_media = MODELS[source_key]["media"] if source_key else record.get("media", "image")
    if source_media not in {"image", "video"}:
        raise GenError(f"{source.name} does not say whether it is an image or a video.")
    if args.video and source_media != "video":
        raise UsageError("--video does not apply to a recorded image.")

    if not source_key and not args.model and not args.quality:
        raise UsageError(
            f"{source.name} does not name a known model; pass --model.",
            fix="For example: --model klein4b.",
        )
    key, entry = select_model(args, source_media, source_key)
    media = entry["media"]
    if args.h264 and media == "image":
        raise UsageError("--h264 only applies to video.")
    if args.strength is not None and media == "video":
        raise UsageError("--strength is image-only; --init is the first video frame.")
    if args.count is not None and media == "video" and args.count != 1:
        raise UsageError("Video renders one clip per run; --count is image-only.")
    source_settings = record.get("settings")
    same_model = (
        isinstance(source_settings, dict)
        and key_for_model(str(source_settings.get("model") or "")) == key
    )
    settings = dict(source_settings) if same_model else load_preset(key)

    loras: list[tuple[str, float]] = []
    if same_model:
        raw_loras = record.get("loras")
        if raw_loras is None and isinstance(source_settings, dict):
            raw_loras = source_settings.get("loras")
        for item in raw_loras or []:
            if isinstance(item, dict) and item.get("file"):
                loras.append((str(item["file"]), float(item.get("weight", 1.0))))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                loras.append((str(item[0]), float(item[1])))

    if args.size:
        width, height = (
            image_size(args.size, settings)
            if media == "image"
            else video_size(args.size, settings)
        )
    else:
        try:
            width = int(record.get("width") or settings.get("width") or 0)
            height = int(record.get("height") or settings.get("height") or 0)
        except (TypeError, ValueError) as exc:
            raise GenError(f"{source.name} has no usable size.") from exc
        width, height = validate_size(width, height)

    refs_raw = record.get("refs") or []
    if isinstance(refs_raw, str):
        refs_raw = [refs_raw]
    refs = tuple(Path(value).expanduser() for value in refs_raw if value)
    init_raw = record.get("init")
    init = Path(init_raw).expanduser() if init_raw else None
    controls_raw = record.get("controls") or []
    controls: list[tuple[Path, str]] = []
    for item in controls_raw:
        if isinstance(item, list) and len(item) == 2:
            controls.append((Path(item[0]).expanduser(), str(item[1])))
        elif isinstance(item, str) and ":" in item:
            name, kind = item.rsplit(":", 1)
            controls.append((Path(name).expanduser(), kind))

    if args.init or args.ref or args.control:
        refs = tuple(image_path(value, "--ref") for value in args.ref or ())
        init = image_path(args.init, "--init") if args.init else None
        controls = list(control_specs(args.control or ()))

    for label, path in [("--init", init), *[("--ref", path) for path in refs]]:
        if path is not None and not path.is_file():
            raise GenError(
                f"The recorded input {path} is missing.",
                fix=f"Put the file back, or pass a new {label}.",
            )
    for path, _ in controls:
        if not path.is_file():
            raise GenError(f"The recorded control image {path} is missing.")

    check_inputs(entry, refs, init, controls, media, key)

    count = record.get("count") or 1
    if args.count is not None:
        count = args.count
    if media == "video":
        count = 1
    if not 1 <= int(count) <= MAX_COUNT:
        raise UsageError(f"--count must be between 1 and {MAX_COUNT}.")

    strength = resolve_strength(args, init, media, record.get("strength"))
    fps, frames = resolve_frames(
        args, entry, settings, key, media, record.get("fps"), record.get("frames")
    )

    negative = args.negative if args.negative is not None else record.get("negative")
    if negative and not entry["negative"]:
        log(f"{entry['label']} ignores negative prompts; dropping it.")
        negative = None

    if args.steps is not None:
        if args.steps < 1:
            raise UsageError("--steps must be at least 1.")
        settings["steps"] = args.steps

    text = prompt or str(record.get("prompt") or "")
    if not text:
        raise UsageError(f"{source.name} has no prompt; pass one to replay it.")
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise UsageError("The prompt is larger than 32 KiB.")

    master = record.get("master")
    if args.h264:
        master = False
    elif master is None:
        master = True

    job = Job(
        mode="",
        media=media,
        model=key,
        settings=settings,
        prompt=text,
        negative=str(negative) if negative else None,
        seed=choose_seed(args.seed if args.seed is not None else record.get("seed")),
        width=width,
        height=height,
        count=int(count),
        out_dir=output_dir(args.out, args.allow_outside),
        slug=slugify(text),
        refs=refs,
        init=init,
        controls=tuple(controls),
        strength=strength,
        loras=tuple(loras),
        fps=fps,
        frames=frames,
        master=bool(master),
        timeout=args.timeout
        or (DEFAULT_VIDEO_TIMEOUT if media == "video" else DEFAULT_TIMEOUT),
        source=source,
    )
    job.mode = infer_mode(job)
    return job


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def job_report(job: Job) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": True,
        "mode": job.mode,
        "media": job.media,
        "model": job.model,
        "size": f"{job.width}x{job.height}",
        "seed": job.seed,
        "count": job.count,
        "out": str(job.out_dir),
        "inputs": {
            "init": str(job.init) if job.init else None,
            "refs": [str(path) for path in job.refs],
            "controls": [f"{path}:{kind}" for path, kind in job.controls],
        },
        "loras": [f"{name}@{weight:g}" for name, weight in job.loras],
    }
    if job.media == "video":
        payload.update(
            fps=job.fps,
            frames=job.frames,
            duration_s=round((job.frames or 0) / (job.fps or 24), 2),
            video_format="prores4444" if job.master else "h264",
        )
    if job.source:
        payload["from"] = str(job.source)
    return payload


def models_report() -> dict[str, Any]:
    models = []
    for key, entry in MODELS.items():
        settings = load_preset(key)
        models.append(
            {
                "key": key,
                "label": entry["label"],
                "media": entry["media"],
                "aliases": list(entry["aliases"]),
                "preset": entry["preset"],
                "width": settings.get("width"),
                "height": settings.get("height"),
                "steps": settings.get("steps"),
                "refs_max": entry["refs"],
                "init": entry["init"],
                "control": entry["control"],
                "negative": entry["negative"],
                "fps": entry.get("fps"),
                "note": entry["note"],
            }
        )
    return {"ok": True, "default": "klein4b", "models": models}


def sizes_report() -> dict[str, Any]:
    video: dict[str, Any] = {}
    for key, entry in MODELS.items():
        if entry["media"] != "video":
            continue
        settings = load_preset(key)
        video[key] = {
            tier: {
                ratio: list(video_size(f"{ratio}@{tier}", settings))
                for ratio in RATIOS
            }
            for tier in TIERS
        }
    return {
        "ok": True,
        "image": {ratio: list(size) for ratio, size in RATIOS.items()},
        "video": video,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def find_ffmpeg() -> str | None:
    override = os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        "~/.local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/Applications/ffmpeg",
    ):
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def require_ffmpeg() -> str:
    path = find_ffmpeg()
    if not path:
        raise GenError(
            "Video export needs an ffmpeg executable, and none was found.",
            fix="Put ffmpeg on PATH or at ~/.local/bin/ffmpeg. Image jobs still work.",
        )
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", path)
    return path


def video_profile(master: bool) -> dict[str, Any]:
    if master:
        return {
            "suffix": ".mov",
            "codec": "prores_ks",
            "pix_fmt_out": "yuva444p10le",
            "output_params": ("-profile:v", "4444", "-vendor", "apl0"),
            "audio_codec": "pcm_s16le",
            "quality": None,
            "label": "ProRes 4444",
        }
    return {
        "suffix": ".mp4",
        "codec": "libx264",
        "pix_fmt_out": "yuv420p",
        "output_params": (),
        "audio_codec": "aac",
        "quality": 8,
        "label": "H.264",
    }


def save_images(job: Job, frames: Sequence[Any]) -> tuple[list[str], list[int]]:
    files: list[str] = []
    seeds: list[int] = []
    for frame in frames:
        path = allocate(job.out_dir, job.slug)
        try:
            frame.to_file(str(path))
        except Exception as exc:
            with contextlib.suppress(OSError):
                path.unlink()
            raise GenError(
                f"Generated the image but could not save it: {sanitize(exc)}"
            ) from exc
        files.append(str(path))
        metadata = getattr(frame, "metadata", None) or {}
        seed = metadata.get("seed") if isinstance(metadata, dict) else None
        if isinstance(seed, int):
            seeds.append(seed)
    return files, seeds


def contact_sheet(frames: Sequence[Any], path: Path) -> None:
    from PIL import Image

    picks = [0, len(frames) // 2, len(frames) - 1]
    thumbs = []
    for index in picks:
        frame = frames[index]
        mode = {1: "L", 3: "RGB", 4: "RGBA"}.get(int(frame.channels), "RGB")
        image = Image.frombytes(
            mode,
            (int(frame.width), int(frame.height)),
            bytes(frame.data)[: int(frame.width) * int(frame.height) * len(mode)],
        ).convert("RGB")
        scale = 240 / max(1, image.height)
        thumbs.append(image.resize((max(1, int(image.width * scale)), 240)))
    gap = 8
    width = sum(thumb.width for thumb in thumbs) + gap * (len(thumbs) - 1)
    sheet = Image.new("RGB", (width, 240), (24, 24, 24))
    offset = 0
    for thumb in thumbs:
        sheet.paste(thumb, (offset, 0))
        offset += thumb.width + gap
    sheet.save(path, format="PNG")


def save_video(
    job: Job, result: Any
) -> tuple[list[str], list[str], list[int], bool, int]:
    try:
        from imageio_ffmpeg import write_frames
    except ImportError as exc:
        raise GenError(
            "Video export needs the ffmpeg extra in the skill environment.",
            fix="Run install.sh, then retry.",
        ) from exc

    frames = list(result)
    if not frames:
        raise GenError("The video render returned no frames.")
    profile = video_profile(job.master)
    fps = int(job.fps or 24)

    path = allocate(job.out_dir, job.slug, profile["suffix"])
    audio = getattr(result, "audio", None)
    seconds = len(frames) / fps
    if audio is not None:
        with contextlib.suppress(Exception):
            if float(getattr(audio, "duration", 0) or 0) > seconds + 0.05:
                audio = audio.slice(0.0, seconds / float(audio.duration))

    audio_file: str | None = None
    if audio is not None:
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.close()
        audio_file = handle.name
        try:
            audio.to_file(audio_file)
        except Exception as exc:
            raise GenError(f"Could not write the audio track: {sanitize(exc)}") from exc

    first = frames[0]
    try:
        writer = write_frames(
            path=str(path),
            size=(int(first.width), int(first.height)),
            fps=fps,
            codec=profile["codec"],
            pix_fmt_in="rgba" if int(first.channels) == 4 else "rgb24",
            pix_fmt_out=profile["pix_fmt_out"],
            quality=profile["quality"],
            macro_block_size=1,
            output_params=list(profile["output_params"]),
            audio_path=audio_file,
            audio_codec=profile["audio_codec"] if audio_file else None,
        )
        try:
            writer.send(None)
            for frame in frames:
                writer.send(bytes(frame.data))
        finally:
            writer.close()
    except Exception as exc:
        with contextlib.suppress(OSError):
            path.unlink()
        raise GenError(
            f"Could not write the {profile['label']} video: {sanitize(exc)}"
        ) from exc
    finally:
        if audio_file:
            with contextlib.suppress(OSError):
                os.unlink(audio_file)

    previews: list[str] = []
    poster = allocate(job.out_dir, f"{job.slug}_poster")
    try:
        frames[len(frames) // 2].to_file(str(poster))
        previews.append(str(poster))
    except Exception as exc:
        with contextlib.suppress(OSError):
            poster.unlink()
        log(f"Could not write the poster: {sanitize(exc, 160)}")

    sheet = allocate(job.out_dir, f"{job.slug}_sheet")
    try:
        contact_sheet(frames, sheet)
        previews.append(str(sheet))
    except Exception as exc:
        with contextlib.suppress(OSError):
            sheet.unlink()
        log(f"Could not write the contact sheet: {sanitize(exc, 160)}")

    metadata = getattr(first, "metadata", None) or {}
    seed = metadata.get("seed") if isinstance(metadata, dict) else None
    return (
        [str(path)],
        previews,
        ([seed] if isinstance(seed, int) else []),
        audio is not None,
        len(frames),
    )


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "version": VERSION,
        "mode": job.mode,
        "media": job.media,
        "model": job.model,
        "prompt": job.prompt,
        "negative": job.negative,
        "seed": job.seed,
        "width": job.width,
        "height": job.height,
        "count": job.count,
        "strength": job.strength,
        "settings": job.settings,
        "init": str(job.init) if job.init else None,
        "refs": [str(path) for path in job.refs],
        "controls": [[str(path), kind] for path, kind in job.controls],
        "loras": [[name, weight] for name, weight in job.loras],
        "fps": job.fps,
        "frames": job.frames,
        "master": job.master,
    }


def write_sidecar(job: Job, first_file: str) -> str | None:
    needed = job.media == "video" or bool(job.init or job.refs or job.controls)
    if not needed:
        return None
    path = Path(first_file).with_suffix(".json")
    try:
        path.write_text(json.dumps(job_to_dict(job), indent=1), encoding="utf-8")
    except OSError as exc:
        log(f"Could not write the replay record {path.name}: {sanitize(exc, 160)}")
        return None
    return str(path)


async def probe(timeout: float = 4.0) -> dict[str, Any]:
    """Cheap liveness check: connect and count installed models."""
    try:
        from drawthings_py import DrawThings
    except ImportError:
        return {"reachable": False, "error": "The Draw Things SDK is not installed."}
    host, port = connection()
    service = None
    try:
        service = DrawThings.grpc(
            host=host, port=port, progressbar=False, disable_messages=True
        )
        with contextlib.redirect_stdout(sys.stderr):
            await asyncio.wait_for(service.connect(), timeout=timeout)
            info = await service.get_models()
        return {
            "reachable": True,
            "host": host,
            "port": port,
            "models": len(installed_files(info)),
        }
    except Exception as exc:
        return {"reachable": False, "host": host, "port": port, "error": sanitize(exc)}
    finally:
        if service is not None:
            with contextlib.suppress(Exception):
                await service.close()


async def render(job: Job) -> dict[str, Any]:
    try:
        from drawthings_py import Configs, DrawThings, RequestBuilder
    except ImportError as exc:
        raise GenError(
            "The Draw Things Python SDK is missing from the skill environment.",
            fix="Run install.sh, or delete the venv and run run.sh again.",
        ) from exc

    host, port = connection()
    if host not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        log(f"Using remote host {host}; its TLS certificate is not hostname-verified.")

    try:
        connect_timeout = float(os.environ.get("DRAWTHINGS_CONNECT_TIMEOUT", "5") or 5)
    except ValueError:
        connect_timeout = 5.0

    started = time.perf_counter()
    service = None
    try:
        service = DrawThings.grpc(
            host=host, port=port, progressbar=False, disable_messages=True
        )
        try:
            with contextlib.redirect_stdout(sys.stderr):
                await asyncio.wait_for(service.connect(), timeout=connect_timeout)
                info = await service.get_models()
        except Exception as exc:
            raise GenError(
                f"Could not reach Draw Things at {host}:{port}.",
                fix="Open Draw Things and enable Settings > API Server "
                "(gRPC, TLS, port 7859), then retry.",
            ) from exc

        requested = str(job.settings.get("model") or "")
        if not requested:
            raise GenError(f"The preset for {job.model} has no model file.")
        model_file = resolve_installed(requested, installed_files(info))

        config = Configs.from_json(json.dumps(job.settings))
        config["model"] = model_file
        config["width"] = job.width
        config["height"] = job.height
        config["seed"] = job.seed
        config["batch_count"] = 1
        config["batch_size"] = job.count
        config["loras"] = []
        for name, weight in job.loras:
            config.add_lora(name, weight, "All")
        if job.media == "video":
            config["num_frames"] = job.frames
            config["fps"] = job.fps
        elif job.init is not None and job.strength is not None:
            config["strength"] = job.strength

        try:
            request = RequestBuilder(config, job.prompt, job.negative)
            for path in job.refs:
                request.add_moodboard_image(str(path))
            if job.init is not None:
                request.init_image(str(job.init))
            for path, kind in job.controls:
                request.control_image(str(path), kind)
        except Exception as exc:
            raise GenError(
                f"Could not attach the input images: {sanitize(exc)}"
            ) from exc

        log(
            f"{job.mode} · {job.model} · {job.width}x{job.height}"
            + (f" · {job.frames} frames at {job.fps} fps" if job.media == "video" else f" · count {job.count}")
            + f" · seed {job.seed}"
        )
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = await asyncio.wait_for(
                    service.generate(request), timeout=job.timeout
                )
        except TimeoutError as exc:
            raise GenError(
                f"The render did not finish within {job.timeout:.0f}s.",
                fix="Try a smaller --size or a larger --timeout.",
            ) from exc
        except Exception as exc:
            raise GenError(
                f"Draw Things could not finish the render: {sanitize(exc)}"
            ) from exc

        actual_frames = job.frames
        if job.media == "video":
            files, previews, seeds, has_audio, actual_frames = save_video(job, result)
        else:
            frames = list(result)
            if not frames:
                raise GenError("Draw Things returned no images.")
            files, seeds = save_images(job, frames)
            previews, has_audio = [], False

        sidecar = write_sidecar(job, files[0])
        payload: dict[str, Any] = {
            "ok": True,
            "mode": job.mode,
            "model": job.model,
            "size": f"{job.width}x{job.height}",
            "seed": job.seed,
            "files": files,
            "seeds": seeds,
            "display": previews if job.media == "video" else files,
            "elapsed_s": round(time.perf_counter() - started, 2),
        }
        if job.media == "video":
            payload.update(
                kind="video",
                video_format="prores4444" if job.master else "h264",
                fps=job.fps,
                frames=actual_frames,
                duration_s=round((actual_frames or 0) / (job.fps or 24), 2),
                audio=has_audio,
                preview=previews,
            )
        if sidecar:
            payload["record"] = sidecar
        if job.source:
            payload["from"] = str(job.source)
        return payload
    finally:
        if service is not None:
            with contextlib.suppress(Exception):
                await service.close()


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = Parser(
        prog="gen",
        description="Images, edits, and short videos with local Draw Things models.",
    )
    parser.add_argument("prompt", nargs="?", help="prompt text")

    parser.add_argument("--model", help="model key or alias")
    parser.add_argument("--quality", choices=("draft", "standard", "hero"))
    parser.add_argument("--size", help="16:9, 1344x768, 1024, 9:16@draft, 720")
    parser.add_argument("--count", type=int, help="images per request, 1-4")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--negative")

    parser.add_argument("--init", help="init image (img2img / first video frame)")
    parser.add_argument("--strength", type=float)
    parser.add_argument("--ref", action="append", help="reference image")
    parser.add_argument("--control", action="append", help="FILE:TYPE")
    parser.add_argument("--lora", action="append", help="FILE[:WEIGHT]")

    parser.add_argument("--video", action="store_true", help="render a clip")
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--frames", type=int)
    parser.add_argument("--h264", action="store_true", help="small MP4 instead of the ProRes 4444 master")

    parser.add_argument("--from", dest="replay", metavar="FILE", help="replay a PNG or job record")
    parser.add_argument("--out", help="output folder (default ./gen)")
    parser.add_argument("--allow-outside", action="store_true")
    parser.add_argument("--timeout", type=float)

    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)




