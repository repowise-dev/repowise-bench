"""Oracle for hermes-agent issue #83389: TodoStore.write drops id-less items.

`_dedupe_by_id` keys every item lacking an `id` to the literal string `"?"`, so
each id-less item overwrites the previous one's index and only the LAST survives.

Exit 0 = the tree is FIXED. Non-zero = the bug is present.

Both directions are proved before this file is used, per the standing rule that
an oracle which has never failed has not been tested:

  * on the unfixed tree at c0106e50 it exits 1, printing 1 item retained;
  * with `_dedupe_by_id` giving id-less items a synthetic per-index key -- the
    same treatment the function ALREADY gives non-dict items on the line above --
    it exits 0.

The check is deliberately NOT "the result equals this exact list". The issue's
expected behaviour is that both items survive and receive fallback ids; it does
not specify WHAT those ids are, and pinning them would fail a correct fix that
chose different synthetic ids. The oracle asserts the property the issue states
(both contents retained, distinct ids), never an implementation's exact output.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from tools.todo_tool import TodoStore
store = TodoStore()
out = store.write([
    {"content": "Task 1", "status": "pending"},
    {"content": "Task 2", "status": "pending"},
])
print(json.dumps(out))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    # Accepted and ignored: the runner passes it to every oracle. The suite half
    # of this task's oracle is a separate check and uses ">= baseline with zero
    # failures", never an exact test count.
    ap.add_argument("--baseline-status", default=None)
    a = ap.parse_args()

    tree = Path(a.tree).resolve()
    r = subprocess.run([sys.executable, "-c", PROBE, str(tree)],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(tree))
    if r.returncode != 0:
        print(f"FAIL probe crashed: {(r.stderr or '')[-300:]}")
        return 2

    import json
    try:
        items = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL probe output unreadable: {exc}: {r.stdout[-200:]!r}")
        return 2

    contents = [str(i.get("content", "")) for i in items]
    ids = [str(i.get("id", "")) for i in items]

    if len(items) != 2:
        print(f"FAIL #83389 present: {len(items)} item(s) retained, expected 2. "
              f"got={items}")
        return 1
    if not ({"Task 1", "Task 2"} <= set(contents)):
        print(f"FAIL both contents not retained: {contents}")
        return 1
    if len(set(ids)) != 2:
        print(f"FAIL id-less items did not receive distinct ids: {ids}")
        return 1

    print(f"PASS #83389 fixed: 2 items retained with distinct ids {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
