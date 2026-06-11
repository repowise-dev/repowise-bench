#!/usr/bin/env python3
"""naturalness.py — a repo-local n-gram *cache* language model, anchored at T0.

RESEARCH ARTIFACT (bench-only). The Phase-2 candidate: **code naturalness /
entropy**. Ray–Hellendoorn–Devanbu (ICSE'16) showed buggy code is measurably
more *entropic* — a per-token language model assigns it higher cross-entropy
(surprisal) — and that ordering files/lines by surprisal rivals static finders.
Because surprisal is **per-token**, it normalizes against file size for free,
which is exactly the within-NLOC-band wall the shipped health score hits.

What this builds, per repo, deterministically (zero LLM — a *counting* n-gram):

  1. **Tokenize at T0.** ``git show T0:path`` for every T0 source file (same
     universe rule as the file-level join: under ``source_root``, matching
     ``extensions``, test files + index-excludes dropped). Tokens come from the
     product tree-sitter parser's *leaf* stream (language-agnostic). Comments are
     dropped; string/number literals are folded to ``<STR>`` / ``<NUM>`` to keep
     the vocabulary from exploding on literal noise.
  2. **Train an order-n count model** (default n=4) on that snapshot, with
     **interpolated Witten–Bell back-off** (a parameter-free, deterministic
     smoothing that mixes each order with the lower one by context richness).
  3. **Add the cache** (Tu et al. "On the Localness of Software"; Hellendoorn &
     Devanbu FSE'17): while scoring a file, a second n-gram model is built
     *incrementally over the tokens already seen in that same file* and mixed in
     with weight ``cache_weight``. This rewards local *idiom/repetition*, so a
     file that re-uses its own established patterns reads as natural — the
     mitigation that keeps this from collapsing into a churn proxy.

The catch this design handles (plan §5, the churn-proxy trap): brand-new code
also reads as "unnatural". Two mitigations are baked in — (a) the model is
**anchored at T0** (trained on, and scoring, exactly the T0 snapshot the health
score was computed on, before the (T0,T1] fixes exist), and (b) the **cache**
rewards within-file idiom rather than global novelty. Redundancy-vs-churn is then
*measured* (not assumed) by the Phase-1 gate in ``naturalness_experiment.py``.

Outputs, cached under ``results/health_defect_<repo>/``:
  * ``naturalness.json`` — model meta + per-file aggregates (**mean** line
    surprisal and **top-decile** line surprisal — the literature's cost-effective
    orderings — plus a token-weighted file cross-entropy) + per-function
    aggregates (mean / max line surprisal over the walker's spans).
  * ``naturalness_lines.json`` — **per-line surprisal** for every file. This is
    the artifact Phase 4 consumes (per-line / per-hunk localization).

The trained count model is *not* pickled: it is large and the per-line surprisal
(its only downstream consumer) is already cached, and the model rebuilds
deterministically from the T0 snapshot. ``naturalness.json``'s ``meta`` records
the exact params (order, cache weight, vocab size, token count) so a rebuild is
reproducible.

Run (venv python — has tree-sitter via the editable install; NOT ``uv run``)::

    cd health-defect
    ../../.venv/Scripts/python.exe naturalness.py \
        --results-dir <bench>/results --repos-dir <bench>/repos \
        [--repo rich] [--order 4] [--cache-weight 0.4] [--rebuild]

``--results-dir``/``--repos-dir`` default to this checkout's siblings; point them
at the fully-populated bench checkout when running from an R&D worktree.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

# --- product imports (live editable install of the walker + parser) ----------
_BENCH_DIR = Path(__file__).resolve().parent
_REPOWISE_ROOT = _BENCH_DIR.parents[1]
for _src in ("core", "cli", "server"):
    _p = _REPOWISE_ROOT / "packages" / _src / "src"
    if _p.exists():
        sys.path.insert(0, str(_p))

from repowise.core.analysis.health.complexity.walker import walk_file  # noqa: E402
from repowise.core.ingestion.languages import REGISTRY  # noqa: E402

from lib.defect_counter import _git, resolve_t0_sha  # noqa: E402
from lib.filters import is_test_file, normalize_path  # noqa: E402

# Defaults (overridable on the CLI). Order 4 + cache 0.4 follow the Tu/
# Hellendoorn cache-model regime; the smoothing is parameter-free.
DEFAULT_ORDER = 4
DEFAULT_CACHE_WEIGHT = 0.4
# Pad token marking sentence (file) start so the first real tokens still have a
# defined (lower-order) context.
_BOS = "<s>"
_STR = "<STR>"
_NUM = "<NUM>"
_TINY = 1e-12  # surprisal floor so -log2 never sees a literal 0


# --------------------------------------------------------------------------
# Tokenization — language-agnostic leaf-token stream from the product parser
# --------------------------------------------------------------------------
_NUM_HINT = ("number", "integer", "float", "int_literal", "float_literal",
             "numeric")
_STR_HINT = ("string", "char", "rune", "escape_sequence", "heredoc")


def _norm_token(node_type: str, text: str) -> str | None:
    """Fold a leaf node into a vocabulary token, or ``None`` to drop it.

    Comments are dropped (naturalness is about code, not prose). String- and
    number-literal *content* is folded to a class token so literal churn does
    not blow up the vocabulary; identifiers, keywords, operators and
    punctuation keep their literal text.
    """
    t = node_type
    if "comment" in t:
        return None
    if any(h in t for h in _NUM_HINT):
        return _NUM
    if any(h in t for h in _STR_HINT):
        return _STR
    s = text.strip()
    if not s:
        return None
    return s


def tokenize(source: bytes, language: str, parser_cache: dict) -> list[tuple[str, int]]:
    """Return ``[(token, line_no), ...]`` for *source* in *language*.

    Leaf (terminal) nodes of the tree-sitter parse, in source order, folded by
    ``_norm_token``. ``line_no`` is the 1-indexed start line. Consecutive folded
    ``<STR>`` tokens (tree-sitter splits a string into start/content/end leaves)
    collapse to one. Returns ``[]`` for an unsupported/unparseable language
    (absent — never a zero-filled column downstream).
    """
    from tree_sitter import Parser

    parser = parser_cache.get(language)
    if parser is None:
        if language in parser_cache:  # cached negative result
            return []
        try:
            from repowise.core.ingestion.parser import _get_language

            grammar = _get_language(language)
            parser = Parser(grammar) if grammar is not None else None
        except Exception:
            parser = None
        parser_cache[language] = parser
        if parser is None:
            return []

    try:
        tree = parser.parse(source)
    except Exception:
        return []

    out: list[tuple[str, int]] = []
    # Iterative pre-order; emit only leaves.
    stack = [tree.root_node]
    # Pre-order requires visiting children left-to-right, so push reversed.
    # We instead collect via recursion-free ordered walk using a cursor.
    cursor = tree.walk()

    def _visit() -> None:
        node = cursor.node
        if node.child_count == 0:
            tok = _norm_token(node.type, (node.text or b"").decode("utf-8", "replace"))
            if tok is not None:
                line = node.start_point[0] + 1
                if tok == _STR and out and out[-1][0] == _STR:
                    return  # collapse adjacent string leaves
                out.append((tok, line))
            return
        if cursor.goto_first_child():
            while True:
                _visit()
                if not cursor.goto_next_sibling():
                    break
            cursor.goto_parent()

    _visit()
    return out


# --------------------------------------------------------------------------
# Order-n count model with interpolated Witten–Bell back-off
# --------------------------------------------------------------------------
class NgramModel:
    """Counting n-gram model. ``order`` = max context length + 1.

    Stores, per order k in 1..order, ``cont[k][ctx] -> Counter(next_token)`` and
    ``total[k][ctx] -> int``. Probability uses interpolated Witten–Bell:

        p_k(w|ctx) = λ·p_ML(w|ctx) + (1-λ)·p_{k-1}(w|ctx[1:]),
        λ = c(ctx) / (c(ctx) + N1+(ctx)),   N1+(ctx)=#distinct continuations,

    bottoming out at the unigram interpolated with a uniform 1/V floor — so an
    unseen context contributes λ=0 and falls straight through to lower orders.
    Deterministic and parameter-free.
    """

    def __init__(self, order: int, vocab_size: int) -> None:
        self.order = order
        self.vocab_size = max(vocab_size, 1)
        self.cont: list[dict[tuple[str, ...], Counter]] = [
            defaultdict(Counter) for _ in range(order + 1)
        ]
        self.total: list[dict[tuple[str, ...], int]] = [
            defaultdict(int) for _ in range(order + 1)
        ]
        self._uniform = 1.0 / self.vocab_size

    def add(self, ctx: tuple[str, ...], w: str) -> None:
        """Add one observation for every order 1..order ending at ``w``.

        ``ctx`` is the up-to-(order-1) preceding tokens (oldest first)."""
        for k in range(1, self.order + 1):
            sub = ctx[-(k - 1):] if k > 1 else ()
            self.cont[k][sub][w] += 1
            self.total[k][sub] += 1

    def add_tokens(self, tokens: list[str]) -> None:
        pad = [_BOS] * (self.order - 1)
        seq = pad + tokens
        for i in range(self.order - 1, len(seq)):
            ctx = tuple(seq[i - (self.order - 1): i])
            self.add(ctx, seq[i])

    def prob(self, ctx: tuple[str, ...], w: str) -> float:
        """Interpolated Witten–Bell P(w | ctx) over orders order..1."""
        return self._prob_k(self.order, ctx, w)

    def _prob_k(self, k: int, ctx: tuple[str, ...], w: str) -> float:
        if k == 0:
            return self._uniform
        sub = ctx[-(k - 1):] if k > 1 else ()
        c = self.total[k].get(sub, 0)
        lower = self._prob_k(k - 1, ctx, w)
        if c == 0:
            return lower
        cont = self.cont[k][sub]
        n1plus = len(cont)
        lam = c / (c + n1plus) if (c + n1plus) > 0 else 0.0
        p_ml = cont.get(w, 0) / c
        return lam * p_ml + (1.0 - lam) * lower


class CacheModel(NgramModel):
    """An ``NgramModel`` grown incrementally over the tokens seen so far in the
    current file (the localness cache). Same back-off; empties to uniform."""

    def __init__(self, order: int, vocab_size: int) -> None:
        super().__init__(order, vocab_size)


# --------------------------------------------------------------------------
# Scoring — per-line surprisal under global ⊕ cache mixture
# --------------------------------------------------------------------------
def score_file_lines(
    tokens: list[tuple[str, int]],
    global_model: NgramModel,
    *,
    cache_weight: float,
    vocab_size: int,
) -> dict[int, list[float]]:
    """Surprisal (bits) for each token, grouped by line.

    For token i: p = (1-λc)·p_global(w_i|h) + λc·p_cache(w_i|h), where the cache
    is an n-gram model built from tokens 0..i-1 of this same file (idiom boost).
    surprisal = -log2 p. Returns ``{line_no: [token surprisals]}``.
    """
    order = global_model.order
    cache = CacheModel(order, vocab_size)
    by_line: dict[int, list[float]] = defaultdict(list)
    pad = [_BOS] * (order - 1)
    seq = pad + [t for t, _ in tokens]
    for idx, (tok, line) in enumerate(tokens):
        pos = idx + (order - 1)
        ctx = tuple(seq[pos - (order - 1): pos])
        pg = global_model.prob(ctx, tok)
        if cache_weight > 0.0:
            pc = cache.prob(ctx, tok)
            p = (1.0 - cache_weight) * pg + cache_weight * pc
        else:
            p = pg
        by_line[line].append(-math.log2(max(p, _TINY)))
        # grow the cache with this token's n-grams (past-only context)
        cache.add(ctx, tok)
    return by_line


def _aggregate_file(by_line: dict[int, list[float]]) -> dict | None:
    """File aggregates from per-token line surprisals.

    ``line_surprisal`` = mean token surprisal on a line. Returns mean and
    top-decile of the line surprisals, plus a token-weighted cross-entropy."""
    if not by_line:
        return None
    line_means = {ln: (sum(v) / len(v)) for ln, v in by_line.items() if v}
    if not line_means:
        return None
    vals = sorted(line_means.values())
    n = len(vals)
    k = max(1, n // 10)  # top decile (at least one line)
    top_decile = sum(vals[-k:]) / k
    all_tokens = [s for v in by_line.values() for s in v]
    return {
        "mean_line_surprisal": round(sum(vals) / n, 5),
        "top_decile_line_surprisal": round(top_decile, 5),
        "token_cross_entropy": round(sum(all_tokens) / len(all_tokens), 5),
        "n_lines": n,
        "n_tokens": len(all_tokens),
    }


# --------------------------------------------------------------------------
# Per-repo driver
# --------------------------------------------------------------------------
def _list_t0_source_files(
    repo_dir: str, t0_sha: str, *, source_root: str,
    extensions: tuple[str, ...], is_excluded,
) -> list[str]:
    out = _git(["ls-tree", "-r", "--name-only", t0_sha], cwd=repo_dir)
    files: list[str] = []
    for raw in out.split("\n"):
        f = normalize_path(raw)
        if not f or not f.startswith(source_root):
            continue
        if not any(f.endswith(e) for e in extensions):
            continue
        if is_test_file(f) or is_excluded(f):
            continue
        files.append(f)
    return files


def _make_exclude_matcher(patterns: list[str]):
    if not patterns:
        return lambda _p: False
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    return lambda p: spec.match_file(p)


def _show_bytes(repo_dir: str, t0_sha: str, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{t0_sha}:{path}"], cwd=repo_dir, capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def build_repo(
    cfg: dict, repos_dir: Path, results_dir: Path, *,
    order: int, cache_weight: float, rebuild: bool,
) -> dict | None:
    """Build + cache the naturalness model outputs for one repo. Returns a
    summary dict (or None if the clone is missing)."""
    name = cfg["name"]
    out_dir = results_dir / f"health_defect_{name}"
    nat_path = out_dir / "naturalness.json"
    lines_path = out_dir / "naturalness_lines.json"
    if nat_path.exists() and lines_path.exists() and not rebuild:
        meta = json.loads(nat_path.read_text()).get("meta", {})
        if meta.get("order") == order and abs(meta.get("cache_weight", -1) - cache_weight) < 1e-9:
            return {**meta, "cached": True}

    repo_dir = (repos_dir / name).resolve()
    nested = repo_dir / name
    if nested.exists() and (nested / ".git").exists():
        repo_dir = nested
    if not repo_dir.exists():
        print(f"  SKIP {name}: {repo_dir} missing")
        return None
    repo_dir = str(repo_dir)

    source_root = cfg["source_root"]
    extensions = tuple(cfg.get("extensions", [".py"]))
    is_excluded = _make_exclude_matcher(list(cfg.get("exclude") or []))
    t0_sha = resolve_t0_sha(repo_dir, cfg["t0_date"])

    t_start = time.time()
    files = _list_t0_source_files(
        repo_dir, t0_sha, source_root=source_root,
        extensions=extensions, is_excluded=is_excluded,
    )

    parser_cache: dict = {}
    # --- Pass 1: tokenize every file, build the global vocabulary -----------
    file_tokens: dict[str, list[tuple[str, int]]] = {}
    vocab: set[str] = {_BOS, _STR, _NUM}
    for path in files:
        ext = "." + path.rsplit(".", 1)[-1]
        lang = REGISTRY.from_extension(ext)
        if lang == "unknown":
            continue
        content = _show_bytes(repo_dir, t0_sha, path)
        if content is None:
            continue
        toks = tokenize(content, lang, parser_cache)
        if not toks:
            continue  # unparseable / empty → absent, never zero
        file_tokens[path] = toks
        vocab.update(t for t, _ in toks)

    if not file_tokens:
        print(f"  {name}: 0 parseable files — skipped")
        return None

    vocab_size = len(vocab)

    # --- Pass 2: train the global order-n model on the whole T0 snapshot ----
    gmodel = NgramModel(order, vocab_size)
    total_tokens = 0
    for toks in file_tokens.values():
        seq = [t for t, _ in toks]
        gmodel.add_tokens(seq)
        total_tokens += len(seq)

    # --- Pass 3: score each file with global ⊕ within-file cache ------------
    file_aggs: dict[str, dict] = {}
    per_line: dict[str, dict[str, float]] = {}
    fn_rows: list[dict] = []
    for path, toks in file_tokens.items():
        by_line = score_file_lines(
            toks, gmodel, cache_weight=cache_weight, vocab_size=vocab_size,
        )
        agg = _aggregate_file(by_line)
        if agg is None:
            continue
        file_aggs[path] = agg
        line_means = {ln: round(sum(v) / len(v), 5) for ln, v in by_line.items() if v}
        per_line[path] = {str(ln): val for ln, val in sorted(line_means.items())}

        # Per-function aggregates via the walker's spans (Phase-4 feeder too).
        ext = "." + path.rsplit(".", 1)[-1]
        lang = REGISTRY.from_extension(ext)
        content = _show_bytes(repo_dir, t0_sha, path)
        if content is None:
            continue
        try:
            fc = walk_file(path, lang, content)
        except Exception:
            fc = None
        if fc and fc.functions:
            for fn in fc.functions:
                span = [line_means[ln] for ln in range(fn.start_line, fn.end_line + 1)
                        if ln in line_means]
                if not span:
                    continue
                fn_rows.append({
                    "function_id": f"{path}::{fn.name}:{fn.start_line}",
                    "file_path": path, "name": fn.name,
                    "start_line": fn.start_line, "end_line": fn.end_line,
                    "nloc": fn.nloc,
                    "mean_line_surprisal": round(sum(span) / len(span), 5),
                    "max_line_surprisal": round(max(span), 5),
                })

    meta = {
        "repo": name, "t0_sha": t0_sha, "order": order,
        "cache_weight": cache_weight, "vocab_size": vocab_size,
        "n_files": len(file_aggs), "n_tokens": total_tokens,
        "n_functions": len(fn_rows),
        "build_seconds": round(time.time() - t_start, 1),
        "smoothing": "interpolated_witten_bell",
        "token_normalization": "comments dropped; <STR>/<NUM> literal folding",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    nat_path.write_text(json.dumps(
        {"meta": meta, "files": file_aggs, "functions": fn_rows}, indent=2))
    lines_path.write_text(json.dumps(per_line, indent=2))
    print(f"  {name:12s} files={len(file_aggs):4d} tokens={total_tokens:>8d} "
          f"vocab={vocab_size:>6d} fns={len(fn_rows):>5d} "
          f"{meta['build_seconds']:.1f}s")
    return {**meta, "cached": False}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, default=_BENCH_DIR.parent / "results")
    ap.add_argument("--repos-dir", type=Path, default=_BENCH_DIR.parent / "repos")
    ap.add_argument("--config", type=Path, default=_BENCH_DIR / "config.yaml")
    ap.add_argument("--repo", default="", help="comma list; default = all config repos")
    ap.add_argument("--order", type=int, default=DEFAULT_ORDER)
    ap.add_argument("--cache-weight", type=float, default=DEFAULT_CACHE_WEIGHT)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    import yaml
    cfg_all = yaml.safe_load(args.config.read_text())
    repo_cfgs = {r["name"]: r for r in cfg_all["repos"]}
    repos = args.repo.split(",") if args.repo else list(repo_cfgs)

    print(f"=== Naturalness (order={args.order} cache_weight={args.cache_weight}) "
          f"over {len(repos)} repos ===")
    summaries = []
    for repo in repos:
        cfg = repo_cfgs.get(repo)
        if cfg is None:
            print(f"  (skip {repo}: not in config)")
            continue
        try:
            s = build_repo(cfg, args.repos_dir, args.results_dir,
                           order=args.order, cache_weight=args.cache_weight,
                           rebuild=args.rebuild)
            if s:
                summaries.append(s)
        except Exception as exc:  # noqa: BLE001 — one bad repo must not abort
            import traceback
            print(f"  !! {repo} FAILED: {exc}")
            traceback.print_exc()

    print("\n=== Naturalness build summary ===")
    tot_files = tot_tokens = 0
    for s in summaries:
        tot_files += s.get("n_files", 0)
        tot_tokens += s.get("n_tokens", 0)
    print(f"  repos={len(summaries)} files={tot_files} tokens={tot_tokens}")


if __name__ == "__main__":
    main()
