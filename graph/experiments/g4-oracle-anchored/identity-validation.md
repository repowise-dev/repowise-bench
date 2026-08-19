# G4 protocol step 2: twenty identities, confirmed by hand

The [preregistration](PREREGISTRATION.md) names the identity mapping as the
single largest risk in this experiment and demands that 20 randomly drawn
identities be confirmed by hand before any rate is quoted. This page is that
check. It was outstanding when the results were first written up, and the page
said so.

**Result: 20 of 20 declaration positions are correct.** Every drawn `(file,
line)` is the exact line on which the function the oracle names is declared. No
off-by-one, no doc comment counted in on one side and out on the other, no
receiver line drift. Two of the twenty are positions no arm carries a symbol
for, which is a different finding and is reported as one below.

This draw was taken again after the Go oracle was changed to key a caller at the
outermost enclosing function, so the sample below is the one that belongs to the
rates the results page now quotes. The pool moved from 428 positions to 422 and
the seed is unchanged, so the draw lands on different rows; that shift is itself
the change taking effect and is discussed under [the re-key](#what-the-re-key-did).

## Why the modal offset was not enough on its own

The results page reports that the modal declaration-line offset between each arm
and the oracle is `(0, 0)`. That is a distribution over a join that already
happened. It says the two sides agree with each other; it does not say either
one is right. A systematic off-by-one applied consistently on both sides would
produce the same modal `(0, 0)` and would still be wrong. Only reading the source
line settles it.

There is a sharper version of the same point. A modal offset is computed over
matched edges, and matched edges are by construction the ones that agree, so a
defect touching only unmatched edges cannot appear in it at all. That is exactly
the defect the previous draw surfaced and this one no longer contains.

## What an identity is here

One endpoint of the join key: a `(file, line)` pair the oracle asserts is the
declaration site of a named function. Both endpoint roles are drawn from, since
they fail differently. A caller position is the declaration of the outermost
function a call is written inside, and passes through the reachability set; a
callee position is the callee's own declaration and does not.

The draw is over the 422 declaration positions inside the file set the oracle
type-checked. A position outside that set is one no rate is computed over, so
validating it would prove nothing about the join that produced the rates.

```bash
python validate_identities.py --oracle gitleaks-notests.jsonl     --repo <path-to-gitleaks> --repo-name gitleaks     --out identity-validation-gitleaks.json
python render_identity_validation.py --graded identity-validation-gitleaks.json
```

The first command draws the sample, prints the source window around each
position, and reports what each arm has stored at the same key. The judgement is
a person reading those twenty windows; the verdicts are written back into the
JSON and the table below is rendered from it.

## The draw

Draw: 20 of 422 declaration positions the oracle asserts in `gitleaks`, seed `20260819`.

| # | position | oracle name | arms holding a symbol there | verdict |
|---:|---|---|---|---|
| 1 | `cmd/generate/config/utils/patterns.go:19` | `utils.AlphaNumeric` | all three | confirmed |
| 2 | `cmd/generate/config/rules/squarespace.go:9` | `rules.SquareSpaceAccessToken` | all three | confirmed |
| 3 | `cmd/root.go:517` | `cmd.FormatDuration` | all three | confirmed |
| 4 | `detect/codec/segment.go:64` | `codec.CurrentLine` | all three | confirmed |
| 5 | `cmd/generate/config/rules/easypost.go:10` | `rules.EasyPost` | all three | confirmed |
| 6 | `cmd/generate/config/rules/messagebird.go:30` | `rules.MessageBirdClientID` | all three | confirmed |
| 7 | `detect/detect.go:374` | `(*detect.Detector).detectRule$1` | none | oracle only |
| 8 | `cmd/generate/config/rules/sidekiq.go:37` | `rules.SidekiqSensitiveUrl` | all three | confirmed |
| 9 | `sources/files.go:60` | `(*sources.Files).scanTargets` | all three | confirmed |
| 10 | `detect/utils.go:23` | `detect.createScmLink` | all three | confirmed |
| 11 | `cmd/generate/config/rules/flyio.go:12` | `rules.FlyIOAccessToken` | all three | confirmed |
| 12 | `cmd/generate/config/rules/scalingo.go:9` | `rules.ScalingoAPIToken` | all three | confirmed |
| 13 | `cmd/generate/config/rules/aws.go:85` | `rules.AmazonBedrockAPIKeyShortLived` | all three | confirmed |
| 14 | `cmd/generate/config/base/config.go:11` | `base.CreateGlobalConfig` | all three | confirmed |
| 15 | `cmd/generate/config/rules/curl.go:101` | `rules.CurlHeaderAuth` | all three | confirmed |
| 16 | `main.go:21` | `v8.listenForInterrupt` | all three | confirmed |
| 17 | `sources/files.go:145` | `(*sources.Files).Fragments$1` | none | oracle only |
| 18 | `cmd/generate/config/rules/finicity.go:24` | `rules.FinicityAPIToken` | all three | confirmed |
| 19 | `cmd/stdin.go:23` | `cmd.runStdIn` | all three | confirmed |
| 20 | `cmd/generate/config/rules/mailgun.go:9` | `rules.MailGunPrivateAPIToken` | all three | confirmed |

Notes:

* **7.** The `func` literal bound to `logger` inside `detectRule`. It is drawn in the callee role only, and that is the re-key working: under the outermost-enclosing key a closure is never a caller position. As a callee it is a real target no arm carries a symbol for, which is a measured recall hole and not a mapping defect.
* **9.** The oracle names two functions at this one position, `scanTargets` and `scanTargets$1`. The closure handed to `filepath.WalkDir` on the next line now keys at the method it is written in, which is where all three arms put it. This row is the caller re-key visible in a single draw.
* **14.** Same shape as row 9: `CreateGlobalConfig` and `CreateGlobalConfig$1` share the position, because the literal inside the returned struct is attributed to the function that writes it.
* **17.** The callback passed to `scanTargets` from `Fragments`, in the callee role only. Same category as row 7.

## What the re-key did

The previous draw contained a row that no arm could ever match and that the
aggregate statistics could not see: a `func` literal, drawn in the **caller**
role. Keying a caller at a position no arm symbolises marks the edge wrong for
every arm at once, which is a property of the oracle's key rather than of any
resolver. On TypeScript, where the same defect was found first, correcting it
moved every arm by more than twenty points.

In this draw no caller position is a function literal, because there is no
longer such a thing: a call written inside a closure is attributed to the
outermost function it is written in. Rows 9 and 14 show that directly. Each is
one position carrying two oracle names, the named function and its `$1` literal,
folded together because the arms fold them together too.

What survives is rows 7 and 17, both function literals in the **callee** role.
A closure that is genuinely called is a real target, and no arm carries a symbol
for one, so those edges can never be matched by anyone. They cost all three arms
recall equally and they can never produce a contradicted edge, so they move no
precision number. The size of that hole is measured on the results page rather
than left as an anecdote.

## Three spellings, one function

Row 9 is a method, and the three arms name it three different ways:
`scanTargets`, `Files::scanTargets`, `sources.scanTargets`. The join never sees
any of that, because it compares locations and never names. This row is the
concrete case for that rule: a name-matched join would have scored all three
arms as disagreeing about a function they all located identically.

## A second draw, on whole edges

Endpoint identities can all be correct while the join still points the wrong
way, so five whole edges were drawn under the same seed and the call site the
oracle recorded was read from source.

| caller | callee | site | source at the site |
|---|---|---|---|
| `rules.TwitterAPISecret` | `secrets.NewSecret` | `cmd/generate/config/rules/twitter.go:33` | `tps := utils.GenerateSampleSecrets("twitter", secrets.NewSecret(utils.AlphaNumeric("5...` |
| `rules.SlackUserToken` | `utils.Numeric` | `cmd/generate/config/rules/slack.go:68` | `` `"user_token6": `+fmt.Sprintf(`"xoxp-%s-%s-%s-%s"`, secrets.NewSecret(utils.Numeric("... `` |
| `rules.ArtifactoryReferenceToken` | `utils.AlphaNumeric` | `cmd/generate/config/rules/artifactory.go:47` | `"artifactoryRefToken := \"cmVmd" + secrets.NewSecret(utils.AlphaNumeric("59")) + "\"",` |
| `config.main` | `rules.JWTBase64` | `cmd/generate/config/main.go:144` | `rules.JWTBase64(),` |
| `rules.SendInBlueAPIToken` | `secrets.NewSecret` | `cmd/generate/config/rules/sendinblue.go:22` | `tps := utils.GenerateSampleSecrets("sendinblue", "xkeysib-"+secrets.NewSecret(utils.H...` |

Five of five: the call is visible in the caller's body at the recorded line, and
the callee's declaration file is the package the call qualifies. Raw draw in
[`identity-validation-gitleaks-edges.json`](identity-validation-gitleaks-edges.json).

## Scope

Drawn on gitleaks, the repository whose cell carries the cross-validation
against the hand-graded audit. The oracle binary, the key and the three arm
extractors are the same code in every cell, so a mapping that holds here holds
in the others.
