# Finding Performance Bugs That Other Tools Can't See

**Repowise detects a class of performance bug — wasted work hidden across function and file boundaries — that no standard code linter can find. We measured this against the real tools developers use today, on 12+ well-known open-source projects. The headline: the standard tools find _zero_ of these bugs, and Repowise finds them at 90–100% precision.**

*Technical report · June 2026 · all numbers below are reproducible (see [How we measured](#how-we-measured)).*

---

## The problem, in one minute

Most performance bugs in real software are **wasted work in a loop**: code that opens a database connection, makes a network call, or reads a file *once per item* when it could do it once for all items. A page that should run one database query runs five hundred. This is the single most common cause of slow software, and it has a name: the **N+1 problem**.

These bugs are expensive twice over: they make products slow for users, and they burn server/cloud spend doing work that didn't need doing.

The catch: the worst ones are **split across functions**. The loop is in one file; the database call it triggers is three function calls away in another file. The tools developers rely on — ruff, ESLint, golangci-lint, clippy — read **one file at a time**. They physically cannot follow the trail. So these bugs ship.

---

## The headline result

We ran the actual, industry-standard linters and Repowise side by side on the same well-known codebases, and compared what each found.

### Standard tools find *none* of this bug class

| Language | Standard tool | Codebases | Files | Tool found (this bug class) | **Repowise found** |
|----------|---------------|-----------|------:|----------------------------:|-------------------:|
| Python | ruff | Django, FastAPI, Pydantic, Scrapy, Celery, Microdot | 7,529 | **0** | **253** |
| TypeScript | ESLint | dub, Hono, Zod, Taxonomy | 4,920 | **0** | **262** |
| Go | golangci-lint | gitleaks | 214 | **0** | **42** |

> Across **12,000+ files of famous open-source code, the standard linters found 0 of these performance bugs. Repowise found 557** — and **~90 of them span multiple functions**, which is the part no single-file tool can ever reach.

This isn't because Repowise is "tuned more aggressively." The standard tools **have no rule for this** — they were never built to follow a call from one file into another. It's a category they don't compete in.

*(Where the tools **do** overlap — a narrow "await inside a loop" check — they agree with us 89% of the time, and the standard tool is often the broader one. We're not claiming to beat them at their job. We're claiming a whole category they don't touch.)*

---

## We're accurate, not noisy

A detector that cries wolf is worse than useless. We hand-verified findings against the real source code, on two kinds of corpus:

| Corpus type | Example repos | Precision (findings that are real) |
|-------------|---------------|-----------------------------------:|
| Mature, well-maintained | Django, FastAPI, Go web tools | **96–100%** |
| Large, fast-moving ("vibe-coded") | **openclaw** (popular open-source AI agent, ~20,000 files) | **90%** |

For comparison, the academic bar for "trustworthy" static performance detection is **70%**. We clear it comfortably, even on messy real-world code.

---

## We find real bugs in code written by experts

On a read-through of the findings in **FastAPI** and **Django** — two of the most-used, most-reviewed Python projects on earth — Repowise surfaced genuine issues, including a verified **O(n²) slowdown in FastAPI's own request-handling core** (a list being searched repeatedly where a set should be used). These are projects with thousands of contributors and years of review. The bug was real.

---

## We confirmed the slowdowns are real

Flagging a pattern is one thing. We took 7 already-verified findings, wrote a tiny before/after benchmark for each, fixed it the obvious way, and **measured the speedup** on the same machine with the same inputs. Every one was faster.

| # | The waste | The fix | Speedup |
|---|-----------|---------|--------:|
| 1 | Searching a growing list inside a loop | Use a set | up to **2,500x** |
| 2 | **FastAPI's own request core** searching a list repeatedly | Use a set | up to **186x** |
| 3 | Building a string with `+=` in a loop | Join a list | **2.5x** (honest small one) |
| 4 | Prepending to a list in a loop | Append then reverse | up to **350x** |
| 5 | Rebuilding an array each step of a reduce | Push into one array | up to **805x** |
| 6 | One database query **per item** (N+1) | One batched query | **~30x** |
| 7 | Opening a database connection every iteration | Open it once | **~10x** |

> **7 of 7 confirmed faster. Median ~50x at realistic sizes; up to 800x+ on the quadratic blow-ups and ~30x on N+1 database batching.** The N+1 number is the conservative floor: it was measured against a local database with zero network latency, so against a real networked database the win is far larger.

We're deliberately honest about the spread. The string-concat finding is only ~2.5x because the Python runtime already optimizes that exact case, and the connection-in-loop finding stays ~10x no matter the size because it's a fixed per-connection cost, not a runaway loop. That last point is *why* the ranking below exists: a 10x win on cold setup code matters less than a 2x win on your hottest request path.

*(Benchmarks and raw numbers: [`benchmarks/`](./benchmarks/).)*

---

## We tell you which ones actually matter

Finding bugs is only half the job. A migration script that runs once a year and a function on your hottest request path both "have" the same pattern — but only one is worth fixing.

Repowise ranks findings by **how central the code is** (how much of the system flows through it) and **how often it changes**. We validated this against history: *do the findings we rank highest line up with where developers actually shipped performance fixes?*

Validated on Repowise's own codebase (we run it on ourselves), measured against where real performance fixes actually landed in the project's history:

| Ranking method | Quality score (NDCG) |
|----------------|---------------------:|
| Raw detection order | 0.29 |
| By severity alone | 0.29 |
| **Repowise (centrality + activity)** | **0.76** |

> Repowise's ranking is **2.6× better** at floating the bugs that mattered to the top. The **top 5 findings were all real, impactful issues.** Confirmed again on **openclaw** (2,634 findings, 12× larger) with a 2.4× improvement.

This is the difference between handing a developer a 500-item wall of noise and handing them the 10 things to fix this week.

---

## It works at real scale

On **openclaw** — a popular open-source AI-agent project, ~20,000 files — Repowise built a map of **176,769 connections between functions** and traced performance bugs across it, including hundreds that span multiple files. This is not a toy that works on small examples; it runs on the size of codebase a real company has.

---

## Why this is a moat, not a feature

Any team can write a linter rule. What they can't easily replicate is the **three things Repowise already maintains** about a codebase, which this detection is built on:

1. **A whole-program call graph** — who calls whom, across every file. This is what lets us follow a loop in one file to a database call in another.
2. **A classified dependency map** — knowing that `prisma` is a *database*, `axios` is a *network call*, `fs` is the *filesystem*. This is what turns "a function call in a loop" (millions, useless) into "a **database** call in a loop" (a real bug, rare).
3. **Centrality and change-history per function** — what's on the hot path, what's actively worked on. This is what powers the ranking.

A single-file linter has none of these and, by design, never will. The same three assets also power Repowise's architecture diagrams, security reachability, and dead-code analysis — so this isn't a one-off; it's one product built on a foundation that compounds.

The academic literature names the exact blocker that stops standard tools from doing this — "you'd need to manually annotate what every library does" — and notes it's impractical at scale. **Repowise already has that annotation, maintained automatically, as a byproduct of indexing the code.** That's the moat.

---

## What we honestly *don't* claim

Trust comes from being clear about the edges:

- We detect **patterns that waste work** statically; we're an analyzer, not a profiler. (We *did* confirm the waste is real by fixing 7 findings and measuring the speedup above, but in normal use we flag the pattern, we don't time your code.)
- We deliberately **don't** chase database "lazy-loading" N+1s (where the query is hidden behind an innocent-looking attribute access). Those are invisible to *any* static tool — claiming them would be dishonest. We catch the *visible* call-in-loop family, with high precision.
- On very mature frameworks, many true findings sit in cold paths (one-time setup, migrations). That's why the **ranking** above matters — and why we built it.

---

## Summary

| | |
|---|---|
| Bug class | Wasted work in loops / N+1, including **cross-function** (the hard, high-value kind) |
| Standard tools' coverage | **0** — they read one file at a time and have no rule for this |
| Repowise findings (12k+ files of famous OSS) | **557**, ~90 spanning multiple functions |
| Precision | **96–100%** on mature code, **90%** on messy real-world code |
| Confirmed runtime impact | **7/7** findings measurably faster after the fix; median ~50x, up to 800x+ |
| Ranking quality vs. raw | **2.6×** better at surfacing what matters |
| Proven scale | **20,000 files**, a **176,769-edge** call graph |
| Languages | Python, TypeScript/JavaScript, Java, Go, C# (Rust in progress) |

---

## How we measured

Everything above is reproducible:

- **The moat comparison** ran the real linters (`ruff --select PERF,ASYNC`, ESLint `no-await-in-loop`, `golangci-lint` perf linters) and Repowise on the same files, then compared finding sets. Standard tools have no io-in-loop / N+1 / cross-function rule — verifiable from each tool's published rule list.
- **Precision** was hand-labeled against real source: a stratified sample per language, two independent passes, scored with confidence intervals.
- **Confirmed runtime impact** took 7 already-verified findings, wrote a before/after microbenchmark for each, and timed the pattern vs the obvious fix on the same inputs back to back (Python / Node / SQLite). Scripts and raw output are in [`benchmarks/`](./benchmarks/).
- **Ranking quality** mined each project's git history for commits that shipped a performance fix (`perf:`, `N+1`, `optimize`, …), then measured whether our top-ranked findings landed where those fixes did, using standard ranking metrics (Precision@k, NDCG).
- **Corpora** are all public, well-known projects: Django, FastAPI, Pydantic, Scrapy, Celery, dub, Hono, Zod, gitleaks, and openclaw (the large fast-moving TypeScript monorepo). Repowise's own codebase is used as the dogfood example for ranking.

Full methodology, per-repo numbers, and the detector internals are documented in the engineering appendix (`METHODOLOGY.md`).

---

*Repowise — code intelligence that your team and your AI agents can both trust.*
