"""Oracle for hermes-agent #83131: dashboard health probe sends no Authorization.

`hermes_cli/web_server.py::_probe_gateway_health` builds

    req = urllib.request.Request(path, method="GET")   # no Authorization

for `{base}/health/detailed`, but that route calls `_check_auth()` and requires
Bearer auth against `API_SERVER_KEY`. Every probe therefore 401s and the gateway
logs a WARNING. The probe runs every ~30 s, so it accumulates ~2,100 lines/day;
the feature still WORKS only because the loop falls through to the
unauthenticated `/health`, which is why the failure is invisible in the UI while
it floods the log.

Exit 0 = FIXED. Non-zero = the bug is present.

WHAT IS ASSERTED, AND WHY IT IS A PROPERTY
------------------------------------------
The issue does not dictate a header-construction style, only that the detailed
probe must authenticate. So: **the request to `/health/detailed` must carry an
Authorization header bearing the configured `API_SERVER_KEY`.** Any spelling of
that passes.

WHAT MUST NOT CHANGE (the #83389 lesson applied forward)
--------------------------------------------------------
An oracle asserting only "an Authorization header exists" grades several broken
fixes as passes. So this also holds the surrounding contract:

  * `/health/detailed` is still tried FIRST, and `/health` is still the
    fallback -- a "fix" that just drops the detailed probe removes the 401 and
    the feature with it;
  * a 200 still returns `(True, body)` with the parsed body;
  * with NO key configured the function must still work rather than crash or
    fail closed, because `_check_auth` itself preserves a no-key path.

BOTH DIRECTIONS -- see this file's proof record in the results doc. Direction 1
is the unfixed tree at c0106e50; direction 2 is a correct fix; and two
degenerate fixes are held as negative controls.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Env is set BEFORE the import because `_GATEWAY_HEALTH_URL` is a module-level
# `os.getenv` evaluated at import time (web_server.py:1618). Setting it after
# the import would leave the probe short-circuiting on `if not
# _GATEWAY_HEALTH_URL` and the oracle would pass for the wrong reason -- a
# detector returning a green for a code path it never reached.
PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GATEWAY_HEALTH_URL"] = "http://gateway:8642"
os.environ["API_SERVER_KEY"] = "sentinel-key-83131"

from hermes_cli import web_server as ws

seen = []


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def make_urlopen(detailed_status):
    def _urlopen(req, timeout=None):
        # Header names are normalised by urllib (`Authorization` -> `Authorization`
        # via capitalize()), so read them case-insensitively rather than trusting
        # a spelling.
        hdrs = {k.lower(): v for k, v in getattr(req, "header_items", lambda: [])()}
        seen.append({"url": req.full_url, "headers": hdrs})
        if req.full_url.endswith("/health/detailed"):
            if detailed_status != 200:
                raise RuntimeError(f"HTTP {detailed_status}")
            return _Resp(200, {"detailed": True})
        return _Resp(200, {"detailed": False})
    return _urlopen


out = {}

# 1. the real shape: /health/detailed reachable and authenticated
ws.urllib.request.urlopen = make_urlopen(200)
seen.clear()
out["ok_result"] = list(ws._probe_gateway_health())
out["ok_calls"] = list(seen)

# 2. fallback shape: /health/detailed 401s, /health must still answer
ws.urllib.request.urlopen = make_urlopen(401)
seen.clear()
out["fallback_result"] = list(ws._probe_gateway_health())
out["fallback_calls"] = list(seen)

# 3. no key configured at all -- must not crash
os.environ.pop("API_SERVER_KEY", None)
ws.urllib.request.urlopen = make_urlopen(200)
seen.clear()
try:
    out["nokey_result"] = list(ws._probe_gateway_health())
    out["nokey_error"] = None
except Exception as exc:
    out["nokey_result"] = None
    out["nokey_error"] = f"{type(exc).__name__}: {exc}"

print("@@ORACLE@@" + json.dumps(out))
"""

SENTINEL = "sentinel-key-83131"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--baseline-status", default=None)  # accepted and ignored
    a = ap.parse_args()

    tree = Path(a.tree).resolve()
    r = subprocess.run([sys.executable, "-c", PROBE, str(tree)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(tree))
    line = next((ln for ln in (r.stdout or "").splitlines()
                 if ln.startswith("@@ORACLE@@")), None)
    if line is None:
        print(f"FAIL probe produced no result: rc={r.returncode} "
              f"{(r.stderr or r.stdout or '')[-400:]}")
        return 2
    out = json.loads(line[len("@@ORACLE@@"):])

    ok_calls = out["ok_calls"]
    fb_calls = out["fallback_calls"]

    # --- what must not change ------------------------------------------------
    if not ok_calls or not ok_calls[0]["url"].endswith("/health/detailed"):
        print(f"FAIL /health/detailed is no longer probed first. Dropping the "
              f"detailed probe removes the 401 by removing the feature. "
              f"calls={[c['url'] for c in ok_calls]}")
        return 1
    if out["ok_result"][0] is not True or not isinstance(out["ok_result"][1], dict):
        print(f"FAIL a 200 no longer returns (True, body): {out['ok_result']}")
        return 1
    if not any(c["url"].endswith("/health") and
               not c["url"].endswith("/health/detailed") for c in fb_calls):
        print(f"FAIL the /health fallback is gone: "
              f"calls={[c['url'] for c in fb_calls]}")
        return 1
    if out["fallback_result"][0] is not True:
        print(f"FAIL fallback no longer reports the gateway alive: "
              f"{out['fallback_result']}")
        return 1
    if out["nokey_error"] is not None:
        print(f"FAIL probing with no API_SERVER_KEY now raises: "
              f"{out['nokey_error']}. _check_auth itself preserves a no-key "
              f"path, so the prober must not fail closed.")
        return 1

    # --- the defect itself ---------------------------------------------------
    detailed = ok_calls[0]
    auth = detailed["headers"].get("authorization")
    if not auth:
        print(f"FAIL #83131 present: the /health/detailed request carries no "
              f"Authorization header. headers={sorted(detailed['headers'])}")
        return 1
    if SENTINEL not in auth:
        print(f"FAIL #83131 partially fixed: an Authorization header is sent "
              f"but it does not carry the configured API_SERVER_KEY. "
              f"header={auth!r}")
        return 1

    print(f"PASS #83131 fixed: /health/detailed authenticates with the "
          f"configured key; detailed probe still first, /health fallback and "
          f"no-key path intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
