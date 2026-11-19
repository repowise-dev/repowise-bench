"""Keyless repowise indexes for the per-model savings bench (Linux, Modal content profile).

  $env:MODAL_PROFILE="content"; $env:MODAL_IMAGE_BUILDER_VERSION="2025.06"
  modal run index_app.py
Writes /results/<name>.tar.gz (the repo's .repowise dir) to volume mk-bench-index.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import modal

HERE = Path(__file__).parent
REPOS = {
    "foreman": ("https://github.com/thruwire/foreman.git", "c3142ef636617f32f772140125aa310d81ceece9"),
    "okf-agent-memory": ("https://github.com/okf-memory/okf-agent-memory.git", "4cbbf831224ec76bc450522faac9249905fdc51a"),
    "leviathan": ("https://github.com/elstongun/leviathan.git", "21563697e1a9c99f31b28108ed332649044d066a"),
}

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ca-certificates", "build-essential")
    .env({"REPOWISE_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1"})
    .add_local_dir(str(HERE / "src"), "/src/repowise", copy=True)
    .run_commands("python -m venv /opt/rw && /opt/rw/bin/pip install -q /src/repowise && /opt/rw/bin/repowise --version")
)
app = modal.App("mk-bench-index", image=image)
vol = modal.Volume.from_name("mk-bench-index", create_if_missing=True)


@app.function(cpu=4.0, memory=8192, timeout=3600, volumes={"/results": vol})
def index(name: str, url: str, sha: str) -> dict:
    t0 = time.time()
    work = Path("/work") / name
    log = []

    def sh(cmd: str, cwd: str = "/work") -> None:
        p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True)
        log.append({"cmd": cmd, "rc": p.returncode, "out": (p.stdout + p.stderr)[-4000:]})
        if p.returncode:
            raise RuntimeError(json.dumps(log[-1]))

    Path("/work").mkdir(exist_ok=True)
    sh(f"git clone -q {url} {name} && git -C {name} checkout -q {sha}")
    sh("/opt/rw/bin/repowise init --yes --no-prose --commit-limit 1000 .", cwd=str(work))
    sh(f"tar -czf /results/{name}.tar.gz -C {work} .repowise")
    vol.commit()
    res = {"name": name, "sha": sha, "secs": round(time.time() - t0, 1), "log": log}
    Path(f"/results/{name}.json").write_text(json.dumps(res, indent=1))
    vol.commit()
    return res


@app.local_entrypoint()
def main():
    for r in index.starmap([(n, u, s) for n, (u, s) in REPOS.items()], return_exceptions=True):
        print(json.dumps(r if isinstance(r, dict) else {"error": str(r)[:3000]}, indent=1)[:3000])
