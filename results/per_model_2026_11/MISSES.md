# Misses: Repowise arm vs bare arm, haiku-4-5 and sonnet-4-6

Graded with harness/analyze.py (regex on text after `ANSWER:`). Failing runs found: Repowise arm 10 of 144 runs (haiku 5, sonnet 5), bare arm 4 of 144 (haiku 2, sonnet 2). Counts are by run; the "67/72 vs 70/72" framing may aggregate differently, the failing set below is the full list from the run dirs.

| model | arm | question | run | missed gold | class | evidence |
|---|---|---|---|---|---|---|
| haiku | rw | LEV-10 | r1 | source.rs | (a) | search_codebase("open_ro") and get_context references returned only index.rs + query.rs, `complete:false`, reason "rust: same-package use is not scanned"; source.rs calls are path-qualified `crate::index::open_ro(...)` (lines 200, 607). Agent answered "high" ignoring complete:false |
| haiku | rw | LEV-10 | r2 | source.rs | (a) | same as r1 |
| sonnet | rw | LEV-10 | r1 | source.rs | (a) | search_codebase + get_context(callers) listed 4 callers, none in source.rs; agent said "I have all the call sites", confidence high, despite `complete:false` |
| sonnet | rw | LEV-10 | r2 | source.rs | (a) | same |
| sonnet | rw | LEV-10 | r3 | source.rs | (a) | same |
| sonnet | rw | FOR-10 | r2 | _route_work, _route_prompt | (a) minor, (b) | get_context include=references (complete:true) returned file + line numbers only (runtime.py:143, hooks.py:475), no enclosing method. Agent wrote "function name resolvable via context" and did not Read. Files matched, names missed |
| sonnet | rw | FOR-10 | r3 | _route_work, _route_prompt | (a) minor, (b) | same; agent even said "a quick read would confirm" and stopped |
| haiku | rw | OKF-11 | r1 | ResolveVendorDir, RemoveVendorDir | (b) | search_codebase listed all call lines in vendor.go; agent then fetched vendor.go:51-70 and 68-80 (ResolveVendorDir, RemoveVendorDir bodies) and still answered with only Client.Resolve and resolveGitURL. Keyless get_answer returned low confidence, no-llm-provider, irrelevant citations, but was not the cause |
| haiku | rw | LEV-02 | r2 | `half_life(` | (c) borderline, (b) | Final ANSWER named Settings::from_config as the resolver; Settings::half_life() appeared only in prose before `ANSWER:`. Gold wants Settings::half_life. Tools served it (search_codebase on "decay period" found config.rs; get_symbol returned half_life) |
| haiku | rw | LEV-02 | r3 | `half_life(` | (c) | Answer correctly says `Settings::half_life` (mod.rs:221) but without parentheses; regex `\bhalf_life\s*\(` needs a "(" . Correct answer, gold too strict |
| haiku | bare | OKF-07 | r2 | saveConceptLocked | (d)/(c) | Traced mcp.go -> SaveConcept -> WithBundleLock -> atomicWriteFile correctly, skipped the private inner helper saveConceptLocked |
| haiku | bare | OKF-07 | r3 | saveConceptLocked | (d)/(c) | same, ends with other layers and omits the private helper |
| sonnet | bare | FOR-11 | r1 | _assess_event | (d)/(c) | Named runtime.py and hooks.py catch sites by line (410, 604) but called the hooks method "function around line 602"; never named `_assess_event` |
| sonnet | bare | FOR-11 | r2 | _assess_event | (d)/(c) | same |

## Do the same questions fail in both arms?
No overlap. Bare misses are FOR-11 and OKF-07 (naming a private function in a long trace); Repowise misses are LEV-10, FOR-10, OKF-11, LEV-02. Bare passed LEV-10 (3/3 on both models, by reading/grepping source.rs) and FOR-10 and OKF-11 and LEV-02 in every run. Repowise passed FOR-11 and OKF-07 in every run.

## Conclusion
Not noise and not (mostly) gold. Of 10 Repowise failing runs: 5 (LEV-10, both models, 5 of 6 runs) are one real product gap; 2 (FOR-10) are a smaller real gap compounded by the agent not reading; 1 (OKF-11) is the agent dropping data it had; 2 (LEV-02) are gold strictness/ambiguity. The bare misses are gold-granularity (private helper names), unrelated to the Repowise misses.

1. Real product gap (LEV-10, the only consistent one): Rust path-qualified calls (`crate::index::open_ro(...)`) are not resolved. search_codebase `lines`, get_context references and get_context callers all omit source.rs. The tools did flag `complete:false` ("same-package use is not scanned") but agents treated the result as complete. Fix: in the Rust resolver, index `crate::`/`super::`/`self::` qualified call paths as call sites; and as a cheap general backstop, when complete:false, have search_codebase add a literal text-grep fallback (the identifier occurs in other files) or an explicit directive "grep the repo for `open_ro` before answering". Tools: search_codebase, get_context (references/callers), Rust call extraction.
2. Smaller gap (FOR-10): get_context include=references returns line numbers only; callers returns enclosing symbols. Add enclosing symbol name per call site to references (or point to include=callers when the question is "which functions call").
3. Not fixable in product: OKF-11 (agent had ResolveVendorDir/RemoveVendorDir in view and dropped them; confidence "high" regardless).
4. Gold fixes: LEV-02 regex `\bhalf_life\s*\(` should be `\bhalf_life\b` (or accept `Settings::half_life`); LEV-02 r2 naming from_config is arguably acceptable but grades fail. For the bare side, `saveConceptLocked` and `_assess_event` are private helpers; consider any_of with the public/enclosing names, but keep the gold as-is for pre-registration integrity and report the sensitivity (LEV-02 regex fix alone moves Repowise 10 -> 8 failing runs).
5. get_answer keyless low confidence (no-llm-provider, OKF-11 r1): returned irrelevant citations, but no failure traces to it; agents fell back to search/get_symbol.
