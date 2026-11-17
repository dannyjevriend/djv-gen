# Third-party notices

This package's own code and documentation are MIT-licensed (see `LICENSE`).
It installs and calls third-party software at runtime.

## drawthings-py

- Package: `drawthings-py[ffmpeg]==0.4.0`
- Project: https://github.com/kcjerrell/drawthings-py
- License: GPL-3.0-only

The server invokes `drawthings_py` as a separate Python package in an isolated
virtual environment; it does not vendor or modify the library's source. Review
the GPL-3.0 terms before redistributing a bundle that includes the installed
package.

The installer pins `drawthings-py==0.4.0` and lets `uv` or `pip` resolve its
dependencies (betterproto, flatbuffers, fpzip, grpclib, numpy, pillow,
strictyaml, tqdm, imageio-ffmpeg). Each carries its own license.

## Model presets

The files under `presets/` are copies of community configurations from
[drawthingsai/community-models](https://github.com/drawthingsai/community-models)
at commit `e9851164709187e7249471701708301842f9f222`, with their LoRA lists
cleared. Each preset records its `source` and `commit` fields.

## Model names

FLUX.2 is a model family from Black Forest Labs. ERNIE Image is from Baidu.
MiniMax H3 and LTX-2.3 are named by their respective publishers. Draw Things is
an application by Draw Things, Inc. This skill is not affiliated with or
endorsed by any of them. Model weights are not included; they are downloaded
and stored by the Draw Things app.
