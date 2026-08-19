"""G4 protocol step 2: confirm 20 randomly drawn identities by hand.

The preregistration names the identity mapping -- an oracle function versus one
of an arm's symbol nodes -- as the single largest risk in this experiment, and
demands 20 identities be confirmed by hand before any rate is quoted. The modal
`(0, 0)` declaration-line offset reported in the README is strong aggregate
evidence, but it is a distribution over a join, not a check that any particular
`(file, line)` denotes the function the oracle says it does. A systematic
off-by-one that happened to be consistent on both sides would produce exactly
that modal offset and would still be wrong.

This script does the mechanical half: it draws the sample, pulls the source
lines the oracle's declaration positions point at, and reports what each arm
has stored at the same key. The judgement half is a person reading the output.

An *identity* here is one endpoint of the join key: a `(file, line)` pair that
the oracle asserts is the declaration site of a named function. Both endpoint
roles are drawn from, because they fail differently -- a caller position comes
from the SSA function's own declaration, a callee position from the callee's,
and only one of the two passes through the reachability set.

Names are printed for the human to read. They are never compared automatically:
that is the failure mode the whole experiment exists to remove, and it would
sneak back in here of all places.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BENCH / "graph" / "lib"))
sys.path.insert(0, str(BENCH / "graph" / "arms"))

import arms as arms_lib  # noqa: E402


def load_edges(path: Path) -> list[dict]:
    return [
        o
        for o in (json.loads(l) for l in path.open(encoding="utf-8"))
        if not o.get("_header") and not o.get("_reachable")
    ]


def load_identities(path: Path) -> tuple[dict, dict[tuple[str, int], dict]]:
    """Every distinct `(file, line)` the oracle asserts is a declaration site."""
    header: dict = {}
    idents: dict[tuple[str, int], dict] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            o = json.loads(line)
            if o.get("_header"):
                header = o
                continue
            if o.get("_reachable"):
                continue
            for file_k, line_k, name_k, role in (
                ("caller_decl_file", "caller_decl_line", "caller_func", "caller"),
                ("callee_file", "callee_line", "callee_func", "callee"),
            ):
                f, ln = o[file_k], o[line_k]
                if not f or not ln:
                    continue
                rec = idents.setdefault((f, ln), {"names": set(), "roles": set()})
                rec["names"].add(o[name_k])
                rec["roles"].add(role)
    return header, idents


def ours_symbols(art) -> dict[tuple[str, int], set[str]]:
    g = art.handle.graph
    out: dict[tuple[str, int], set[str]] = {}
    for _, d in g.nodes(data=True):
        if d.get("node_type") != "symbol" or d.get("file_path") is None:
            continue
        key = (arms_lib.norm_path(d["file_path"]), int(d.get("start_line") or 0))
        out.setdefault(key, set()).add(str(d.get("name") or d.get("id")))
    return out


def _sql_symbols(
    conn: sqlite3.Connection, root: str | None, prefix: str | None = None
) -> dict[tuple[str, int], set[str]]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
    name_col = "qualified_name" if "qualified_name" in cols else "name"
    out: dict[tuple[str, int], set[str]] = {}
    sql = f"SELECT file_path, start_line, {name_col} FROM nodes WHERE file_path <> ''"
    for f, ln, nm in conn.execute(sql):
        if not f:
            continue
        nm = str(nm)
        # The same strip the arm applies to callee identities. Left in, a name
        # here would be prefixed with the scratch directory this build happened
        # to land in, which is a fact about the run and not about the tool.
        if prefix and nm.startswith(prefix):
            nm = nm[len(prefix):]
        out.setdefault((arms_lib.norm_path(f, root), int(ln or 0)), set()).add(nm)
    return out


SYMBOLS = {
    "repowise": ours_symbols,
    "codegraph": lambda art: _sql_symbols(art.handle, None),
    "codebase-memory-mcp": lambda art: _sql_symbols(
        art.handle["conn"], art.handle["root"], art.handle.get("project_prefix")
    ),
}


def source_window(repo: Path, rel: str, line: int, before: int = 1, after: int = 1) -> list[str]:
    p = repo / rel
    if not p.exists():
        return ["<file missing>"]
    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lo, hi = max(0, line - 1 - before), min(len(text), line + after)
    return [f"{i + 1:>6}{'>' if i + 1 == line else ' '} {text[i]}" for i in range(lo, hi)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-name", default=None)
    ap.add_argument("--arms", default="repowise,codegraph,codebase-memory-mcp")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument(
        "--edge-sample",
        type=int,
        default=0,
        help="also draw this many whole edges and print the call site line, so the "
             "direction of the join is checked and not only its endpoints",
    )
    ap.add_argument("--seed", type=int, default=20260819)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    header, idents = load_identities(Path(args.oracle))
    analysed = set(header["analysed_files"])
    # An identity outside the analysed set is one no rate is computed over, so
    # validating it would prove nothing about the join that produced the rates.
    pool = sorted(k for k in idents if k[0] in analysed)
    rng = random.Random(args.seed)
    sample = rng.sample(pool, min(args.n, len(pool)))

    repo = Path(args.repo).resolve()
    name = args.repo_name or repo.name

    arm_syms: dict[str, dict[tuple[str, int], set[str]]] = {}
    for nm in [n for n in args.arms.split(",") if n]:
        arm = arms_lib.get_arm(nm)
        art = arm.build(repo, repo_name=name, fresh=True)
        try:
            arm_syms[nm] = SYMBOLS[nm](art)
            print(f"built {nm} v{art.version}: {len(arm_syms[nm])} symbol positions",
                  file=sys.stderr)
        finally:
            arm.close(art)

    rows = []
    for i, (f, ln) in enumerate(sample, 1):
        rec = idents[(f, ln)]
        row = {
            "n": i,
            "file": f,
            "line": ln,
            "oracle_names": sorted(rec["names"]),
            "roles": sorted(rec["roles"]),
            "source": source_window(repo, f, ln),
            "arms": {nm: sorted(arm_syms[nm].get((f, ln), [])) for nm in arm_syms},
        }
        rows.append(row)
        print(f"\n--- {i:>2}. {f}:{ln}  [{'/'.join(row['roles'])}]")
        print(f"    oracle: {', '.join(row['oracle_names'])}")
        for sl in row["source"]:
            print(f"    {sl}")
        for nm in arm_syms:
            got = row["arms"][nm]
            print(f"    {nm:<22} {', '.join(got) if got else '-- nothing at this position --'}")

    # Endpoint identities can all be right while the join still points the wrong
    # way, so a second, smaller draw takes whole edges and shows the call site
    # line the oracle recorded. Reading it is the check; nothing here asserts.
    edge_rows = []
    if args.edge_sample:
        pairs = [
            o for o in load_edges(Path(args.oracle))
            if o["callee_file"] in analysed and o["caller_decl_file"] in analysed
        ]
        for o in random.Random(args.seed).sample(pairs, min(args.edge_sample, len(pairs))):
            site = source_window(repo, o["caller_file"], o["caller_line"], 0, 0)
            edge_rows.append({
                "caller": o["caller_func"], "callee": o["callee_func"],
                "caller_decl": [o["caller_decl_file"], o["caller_decl_line"]],
                "callee_decl": [o["callee_file"], o["callee_line"]],
                "call_site": [o["caller_file"], o["caller_line"]],
                "site_source": site[0].split("> ", 1)[-1].strip() if site else "",
                "dynamic": o["dynamic"],
            })
        print("\n=== whole-edge draw ===")
        for i, r in enumerate(edge_rows, 1):
            print(f"\n--- {i}. {r['caller']}\n     -> {r['callee']}")
            print(f"    caller decl {r['caller_decl'][0]}:{r['caller_decl'][1]}"
                  f"   callee decl {r['callee_decl'][0]}:{r['callee_decl'][1]}")
            print(f"    site {r['call_site'][0]}:{r['call_site'][1]}   {r['site_source']}")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"repo": name, "seed": args.seed, "oracle": args.oracle,
                        "pool_size": len(pool), "rows": rows,
                        "edge_rows": edge_rows}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
