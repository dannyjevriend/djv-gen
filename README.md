# Draw Things MCP

*DJV.TOOLS · Gen — the local rendering stage.*

Lets Bionic create images and short videos on your Mac by driving the **Draw
Things** app for you. No cloud, no API keys, nothing leaves the machine.

> **Status: early development.** Part of
> [DJV.TOOLS](https://dannyjevriend.com/tools) — Story → Prep → DoP → **Gen**.
> This is the beginning of `djv.tools.gen`. Design notes:
> [`docs/DJV.TOOLS.architecture.md`](docs/DJV.TOOLS.architecture.md).

## Why this one

Most Draw Things MCP servers are thin bridges to the app's compatibility API:
they generate and transform, but they use whatever model happens to be open, and
they can't switch models, take reference or control images, render video, or
reproduce a result once the session ends. This one drives Draw Things' native
gRPC engine instead, so every request picks its model, carries references,
control images and LoRAs, or renders a clip — and the full recipe travels inside
the PNG it produces, which is why `replay` still works months later, on another
machine, with no database, session registry or asset ID. Around that engine sits
a small, opinionated layer: seven curated models with plain-language notes, three
quality tiers and an exact size table, so the assistant makes sensible choices
instead of guessing at legacy 512px defaults — behind six focused tools that
validate their input, offer a dry run, and keep writes inside the folder you
named. And it needs nothing else: no headless server, no workflow graphs, no
cloud account, no Node, no keys. It uses the Draw Things app you already have,
speaks to it only over loopback, and installs itself on first run.

## How it fits together

```
You → Bionic → this server (stdio pipe) → Draw Things (local gRPC, :7859) → image
```

Draw Things draws; this package only translates. Bionic starts `run.sh` and talks
to it over a pipe, so there is no port to open and nothing to log in to.

## Security & privacy

Everything stays on loopback: Bionic talks to this server over a pipe, and the
server talks to Draw Things on `127.0.0.1:7859`. There is no authentication and
no traffic leaves the machine — which also means **do not expose port 7859** to
your network. Renders go to `~/Pictures/drawthings`, and `out` is kept inside
the project unless `allow_outside` is set.

## Setup

1. **Draw Things** → Settings → API Server: on, **gRPC + TLS, port 7859**.
   Check with `lsof -nP -iTCP:7859 -sTCP:LISTEN`. *(Port 7860 is a different,
   older interface — this package doesn't use it.)*
2. **Bionic** → quit it, run `./make-entry.sh` and paste the object it prints
   into the `servers` list in `~/.lmstudio/apps/bionic/.internal/ng-mcp.json`,
   then relaunch. *(The committed `drawthings-mcp-entry.example.json` has a
   placeholder path — your local entry is generated, never committed.)*
3. First start builds a private Python environment (~1 minute, once).

You need Apple Silicon, Draw Things, and `uv` (or Python 3.11+). No Node, no
Homebrew, no accounts. **There is no `.skill` to install — this folder is the
whole thing.**

## How to use it

| You say | What happens |
| --- | --- |
| "A lighthouse at sunset, wide" | one image, settings chosen for you |
| "Same but at night" | edit of the last image |
| "Give me three options" | three images |
| "Turn this photo into a watercolour" | attach it, it restyles |
| "A 5-second clip of waves" | video |
| "Do that again, different seed" | replay with one change |

## The six tools

| Tool | Does | Options |
| --- | --- | --- |
| `generate` | text → image | `quality`, `size`, `count`, `seed`, `steps`, `lora` |
| `transform` | image → image | `init`, `strength` (0.7), `ref`, `control` |
| `video` | text/image → clip | `seconds`/`frames`, `h264`, `init`, `ref` |
| `replay` | reproduce a render | `source` (PNG or `.json`) + overrides |
| `status` | is it up? | `probe: true` |
| `guide` | which model is good at what | — |

## Models and sizes

Images: `klein4b` (default, fastest) · `klein9b` (more detail) · `dev` (best,
slower) · `ernie` (photographic; the only one that takes negative prompts).
Video: `h3` (with audio) · `h3ref` (up to 9 reference images) · `ltx` (fast).
Quality tiers pick for you: image `draft/standard/hero` → klein4b/klein9b/dev;
video → ltx/h3/h3.

`size` takes a ratio (`16:9`), exact pixels (`1344x768`), a square (`1024`), or
nothing.

## Where your files go

Renders land in `~/Pictures/drawthings` (change with `DRAWTHINGS_OUTPUT_DIR`).
Every PNG embeds its recipe, so replay works later. Video is a ProRes `.mov` by
default; add `h264` for a small `.mp4`, plus a poster and contact sheet.

## If something breaks

| Symptom | Fix |
| --- | --- |
| "Could not reach Draw Things" | App open? API Server on with gRPC + TLS on **7859**? |
| "The model … is not installed" | Download it in Draw Things, or ask for another model. |
| No tools in Bionic | Check the `ng-mcp.json` entry, then fully quit and relaunch. |
| First request is slow | The `dev` model loads once; later requests are fast. |

## Inside

`server.py` is the six tools. `gen.py` is the engine: models, sizes, validation,
rendering. It uses Draw Things' native gRPC API rather than the older HTTP one,
which is why model switching, references, control images, video and replay
work at all.

`server.py` · `gen.py` · `presets/` · `run.sh` · `install.sh` · `make-entry.sh` ·
`audit.sh` · `docs/` · `upstream/`

## Credits

Built by **[Danny Hollander](https://dannyjevriend.com/tools)**.
[Draw Things](https://drawthings.ai/) · [drawthings-py](https://github.com/kcjerrell/drawthings-py)
(GPL-3.0, used as a separate package) · [LM Studio / Bionic](https://lmstudio.ai/) ·
presets from [community-models](https://github.com/drawthingsai/community-models).
MIT — see [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
