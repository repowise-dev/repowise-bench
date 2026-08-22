# G5: adversarial invariance

**Status: scored, four arms, Go.** The generator and the scorer both exist and
gitleaks is graded at `3594ba75`. Java is predicted and not run.

Read [PREREGISTRATION.md](PREREGISTRATION.md) first. It has the predictions,
including the one that says we fail badly on Java.

## Result

| arm | M1 decoy twin | M2 consistent rename | M3 shadowing |
|---|---|---|---|
| repowise | pass | pass | **fail** |
| codegraph | pass | pass | **fail** |
| graphify | untestable | untestable | untestable |
| code-review-graph | untestable | untestable | untestable |

Neither we nor CodeGraph put an edge on M1's decoy, and both come through M2's
284 affected edges with none lost and none gained, so neither tool is a
name-matcher. **Nobody passes M3**: ours resolves `secrets.NewSecret(...)` to the
package function after `secrets` has become an `int`, and CodeGraph does the
same.

`untestable` is not a pass and is never reported as one. Graphify and
code-review-graph resolved no edge to the mutated symbol at the baseline, so a
mutation cannot change their answer. An arm that resolves nothing cannot be
tricked, and scoring that as a pass would rank it above one that resolves almost
everything.

## What exists

`mutate.py` generates semantics-known mutations of a real repository into a
scratch copy, deterministically from a seed, and writes a manifest. It is
tool-agnostic: it edits source files and nothing else, imports no `repowise`
code, and reads no index. Both arms then index the mutated tree without being
told a mutation happened.

| | mutation | status on Go |
|---|---|---|
| **M1** | decoy twin: a same-named function in a directory nothing imports | built |
| **M2** | consistent rename of a symbol and every reference | built |
| **M3** | shadowing: a local whose name equals a called import | built |
| M4 | overload split | **not applicable to Go**, which has no overloading |
| M5 | vendored twin | stubbed, see below |

M5 is stubbed rather than faked. In Go the import path *is* the identity, so
copying a package to a second path with no import rewrite is invisible to any
correct resolver and the mutation would measure nothing. Doing it properly needs
an import-graph rewrite, which is a design question and not a missing function.

## Verified on gitleaks

* **M1** picked `NewSecret`, which has **319 call sites**, and wrote a decoy into
  a fresh unreferenced package. A large denominator is what makes this mutation
  able to discriminate at all; a decoy on a symbol called twice proves nothing.
* **M2** picked `GenerateSampleSecrets`, 223 call sites across 117 files, renamed
  to a generated name confirmed absent from the tree beforehand by an independent
  grep returning 0.
* **M3** shadowed the `secrets` import inside `JFrogIdentityToken`.
* **Determinism:** two runs at seed 2026 into different destinations are
  byte-identical, tree and manifest. The manifest deliberately carries no
  timestamps and no absolute paths, since either would break that.
* **Validity:** `gofmt -e` clean across all 117 touched files.
* **Non-destructive:** the source repository has no modified tracked files after
  either run.

## The scorer, which is the missing half

For each mutation, rebuild both arms' graphs on the mutated tree and compare
against each arm's own unmutated baseline:

* **M1** counts edges retargeted or added to the decoy, over the 319 call sites
  naming that symbol. Lower is better and zero is correct.
* **M2** counts edges lost or gained under the renaming. **Zero is the only
  passing answer.** Anything else is a resolver keyed on the old spelling.
* **M3** counts shadowed sites still bound to the import.

The baseline has to rebuild byte-identically inside the same session or the run
is void, because a scorer that cannot tell a mutation from ordinary
non-determinism is not measuring the mutation.

## Run

```bash
python graph/experiments/g5-invariance/mutate.py \
    --src ../test-repos/gitleaks --dst /tmp/g5/gitleaks-m1 \
    --mutations m1,m2 --seed 2026
```

`--src` is never written to. `--dst` is refused if non-empty without `--force`.
