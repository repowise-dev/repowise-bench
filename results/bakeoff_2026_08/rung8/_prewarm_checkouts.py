"""Serially materialise every instance checkout the grader will need.

Written by rung8_runner.py; runs inside ContextBench's own venv.
"""
import json, os, sys
# Run by absolute path, so the script's own directory heads sys.path and the
# grader package next to the cwd is not importable without this.
sys.path.insert(0, os.getcwd())
from contextbench.core import checkout

rows = json.load(open(sys.argv[1], encoding="utf-8"))
cache = sys.argv[2]
for r in rows:
    d = checkout(r["repo_url"], r["base_commit"], cache, verbose=False)
    print(("ok   " if d else "FAIL ") + r["instance_id"], flush=True)
