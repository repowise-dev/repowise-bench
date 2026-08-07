"""Serially materialise every checkout the grader needs. See rung8_runner."""
import json, os, sys
sys.path.insert(0, os.getcwd())
from contextbench.core import checkout

rows = json.load(open(sys.argv[1], encoding="utf-8"))
cache = sys.argv[2]
for r in rows:
    d = checkout(r["repo_url"], r["base_commit"], cache, verbose=False)
    print(("ok   " if d else "FAIL ") + r["instance_id"], flush=True)
