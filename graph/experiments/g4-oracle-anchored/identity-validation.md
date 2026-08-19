# G4 protocol step 2: twenty identities, confirmed by hand

The [preregistration](PREREGISTRATION.md) names the identity mapping as the
single largest risk in this experiment and demands that 20 randomly drawn
identities be confirmed by hand before any rate is quoted. This page is that
check. It was outstanding when the results were first written up, and the page
said so.

**Result: 20 of 20 declaration positions are correct.** Every drawn `(file,
line)` is the exact line on which the function the oracle names is declared. No
off-by-one, no doc comment counted in on one side and out on the other, no
receiver line drift.

## Why the modal offset was not enough on its own

The results page reports that the modal declaration-line offset between each arm
and the oracle is `(0, 0)`. That is a distribution over a join that already
happened. It says the two sides agree with each other; it does not say either
one is right. A systematic off-by-one applied consistently on both sides would
produce the same modal `(0, 0)` and would still be wrong. Only reading the source
line settles it.

## What an identity is here

One endpoint of the join key: a `(file, line)` pair the oracle asserts is the
declaration site of a named function. Both endpoint roles are drawn from, since
they fail differently. A caller position comes from the SSA function's own
declaration and passes through the reachability set; a callee position does not.

The draw is over the 428 declaration positions inside the file set the oracle
type-checked. A position outside that set is one no rate is computed over, so
validating it would prove nothing about the join that produced the rates.

```bash
python validate_identities.py --oracle gitleaks-notests.jsonl \
    --repo <path-to-gitleaks> --repo-name gitleaks \
    --out identity-validation-gitleaks.json
python render_identity_validation.py --graded identity-validation-gitleaks.json
```

The first command draws the sample, prints the source window around each
position, and reports what each arm has stored at the same key. The judgement is
a person reading those twenty windows; the verdicts are written back into the
JSON and the table below is rendered from it.

## The draw

Draw: 20 of 428 declaration positions the oracle asserts in `gitleaks`, seed `20260819`.

| # | position | oracle name | arms holding a symbol there | verdict |
|---:|---|---|---|---|
| 1 | `cmd/generate/config/utils/patterns.go:19` | `utils.AlphaNumeric` | all three | confirmed |
| 2 | `cmd/generate/config/rules/squarespace.go:9` | `rules.SquareSpaceAccessToken` | all three | confirmed |
| 3 | `cmd/root.go:502` | `cmd.fileExists` | all three | confirmed |
| 4 | `detect/codec/segment.go:35` | `codec.Tags` | all three | confirmed |
| 5 | `cmd/generate/config/rules/easypost.go:10` | `rules.EasyPost` | all three | confirmed |
| 6 | `cmd/generate/config/rules/messagebird.go:30` | `rules.MessageBirdClientID` | all three | confirmed |
| 7 | `detect/detect.go:284` | `(*detect.Detector).DetectContext$1` | none | oracle only |
| 8 | `cmd/generate/config/rules/sidekiq.go:37` | `rules.SidekiqSensitiveUrl` | all three | confirmed |
| 9 | `sources/file.go:150` | `(*sources.File).decompressorFragments` | all three | confirmed |
| 10 | `detect/git.go:19` | `(*detect.Detector).DetectGit` | all three | confirmed |
| 11 | `cmd/generate/config/rules/flyio.go:12` | `rules.FlyIOAccessToken` | all three | confirmed |
| 12 | `cmd/generate/config/rules/scalingo.go:9` | `rules.ScalingoAPIToken` | all three | confirmed |
| 13 | `cmd/generate/config/rules/aws.go:85` | `rules.AmazonBedrockAPIKeyShortLived` | all three | confirmed |
| 14 | `cmd/generate/config/base/config.go:11` | `base.CreateGlobalConfig` | all three | confirmed |
| 15 | `cmd/generate/config/rules/curl.go:101` | `rules.CurlHeaderAuth` | all three | confirmed |
| 16 | `logging/log.go:44` | `logging.Fatal` | all three | confirmed |
| 17 | `sources/file.go:254` | `(*sources.File).FullPath` | all three | confirmed |
| 18 | `cmd/generate/config/rules/finicity.go:24` | `rules.FinicityAPIToken` | all three | confirmed |
| 19 | `cmd/stdin.go:13` | `cmd.init#6` | repowise, codegraph | confirmed |
| 20 | `cmd/generate/config/rules/mailgun.go:9` | `rules.MailGunPrivateAPIToken` | all three | confirmed |

Notes:

* **7.** an immediately invoked func literal inside DetectContext; no arm models a closure as a symbol, so this identity can never match for anyone
* **9.** three spellings of one method: decompressorFragments, File::decompressorFragments, sources.decompressorFragments
* **10.** three spellings again: DetectGit, Detector::DetectGit, detect.DetectGit
* **19.** the sixth package-level init in package cmd; codebase-memory-mcp stores no symbol at this position


## What the two non-trivial rows say

Neither is a mapping defect, and both are worth reporting rather than smoothing
over.

**Row 7 is an oracle-only identity.** `DetectContext$1` is an immediately
invoked function literal, which the SSA form names and treats as a function. No
arm models a closure as a symbol, so no arm can ever match that identity. It
lowers recall for all three equally and it can never produce a contradicted edge
for anyone, so it moves no precision number.

**Row 19 is present for two arms and absent for the third.** The position is a
package-level `func init()`, the sixth in package `cmd`.
codebase-memory-mcp stores no symbol there. That is a property of what that tool
records, not of the join, and it is the kind of thing this check exists to tell
apart.

## Three spellings, one function

Rows 9, 10 and 17 are methods, and the three arms name them three different
ways: `decompressorFragments`, `File::decompressorFragments`,
`sources.decompressorFragments`. The join never sees any of that, because it
compares locations and never names. These rows are the concrete case for that
rule: a name-matched join would have scored all three arms as disagreeing about
a function they all located identically.

## A second draw, on whole edges

Endpoint identities can all be correct while the join still points the wrong
way, so five whole edges were drawn under the same seed and the call site the
oracle recorded was read from source.

| caller | callee | site | source at the site |
|---|---|---|---|
| `config.main` | `rules.LinkedinClientSecret` | `cmd/generate/config/main.go:153` | `rules.LinkedinClientSecret(),` |
| `rules.PulumiAPIToken` | `utils.GenerateUniqueTokenRegex` | `rules/pulumi.go:14` | `Regex: utils.GenerateUniqueTokenRegex(...)` |
| `rules.NPM` | `secrets.NewSecret` | `rules/npm.go:22` | `... + secrets.NewSecret(utils.AlphaNumeric("36"))` |
| `rules.SettlemintApplicationAccessToken` | `secrets.NewSecret` | `rules/settlemint.go:46` | `... + secrets.NewSecret(utils.AlphaNumeric("10")) + ...` |
| `rules.Typeform` | `utils.GenerateSampleSecrets` | `rules/typeform.go:22` | `tps := utils.GenerateSampleSecrets(...)` |

Five of five: the call is visible in the caller's body at the recorded line, and
the callee's declaration file is the package the call qualifies. Raw draw in
[`identity-validation-gitleaks-edges.json`](identity-validation-gitleaks-edges.json).

## Scope

Drawn on gitleaks, the repository whose cell carries the cross-validation
against the hand-graded audit. The oracle binary, the key and the three arm
extractors are the same code in every cell, so a mapping that holds here holds
in the others; what does not carry across is the arm coverage observation in row
19, which is a fact about one tool on one repository.
