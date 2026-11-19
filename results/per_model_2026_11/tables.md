## Per model

| Model | Runs per arm | Pairs | Repowise used | Output tokens bare / repowise | Change [95% CI] | Leaner on | Tool calls | Files opened | Wall clock (s) | Correct bare / repowise |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Fable 5.1 | 0 | 0 | | not run: subscription capacity (A4) | | | | | | |
| Opus 5.5 | 2 (A4) | 48 | 48/48 | 1,617 / 1,388 | -14.1% [-22.7, -7.0] | 20 of 24 | 5.1 / 4.2 (-19%) | 2.2 / 0.4 (-83%) | 18 / 17 (-5%) | 48 / 48 of 48 |
| Sonnet 5.5 | 2 (A4) | 48 | 48/48 | 1,388 / 1,254 | -9.6% [-15.5, -3.5] | 19 of 24 | 4.2 / 4.0 (-5%) | 1.5 / 0.9 (-40%) | 12 / 11 (-6%) | 46 / 48 of 48 |
| Opus 4.8 | 2 (A4) | 48 | 48/48 | 1,676 / 1,432 | -14.5% [-42.3, +5.7] | 12 of 24 | 5.2 / 4.8 (-9%) | 2.1 / 0.4 (-83%) | 22 / 22 (+3%) | 47 / 46 of 48 |
| Opus 4.6 | 0 | 0 | | not run: subscription capacity (A4) | | | | | | |
| Sonnet 4.6 | 3 (A1) | 72 | 72/72 | 2,348 / 1,146 | -51.2% [-73.8, -29.0] | 18 of 24 | 7.1 / 5.3 (-26%) | 3.5 / 0.0 (-99%) | 45 / 24 (-47%) | 70 / 67 of 72 |
| Haiku 4.5 | 3 (A1) | 72 | 72/72 | 2,907 / 2,754 | -5.3% [-15.9, +4.9] | 14 of 24 | 9.0 / 9.7 (+8%) | 3.7 / 0.3 (-91%) | 30 / 29 (-3%) | 70 / 69 of 72 |
| Codex gpt-5.6-terra | 0 | 0 | | not run: Codex CLI 0.154 `exec` exposed no tools from the stdio Repowise server to the model (isolated home and operator home alike), so there is no Repowise arm to compare (A3) | | | | | | |

Not run:
- Fable 5.1: not run: subscription capacity (A4).
- Opus 4.6: not run: subscription capacity (A4).
- Codex gpt-5.6-terra: not run: Codex CLI 0.154 `exec` exposed no tools from the stdio Repowise server to the model (isolated home and operator home alike), so there is no Repowise arm to compare (A3).

## Correctness before and after A5

| Model | Bare correct, pre-A5 / A5 | Repowise correct, pre-A5 / A5 |
|---|---:|---:|
| Opus 5.5 | 48 / 48 of 48 | 48 / 48 of 48 |
| Sonnet 5.5 | 46 / 46 of 48 | 48 / 48 of 48 |
| Opus 4.8 | 46 / 47 of 48 | 46 / 46 of 48 |
| Sonnet 4.6 | 70 / 70 of 72 | 67 / 67 of 72 |
| Haiku 4.5 | 70 / 70 of 72 | 67 / 69 of 72 |

## Total tokens (recorded, not a headline metric)

| Model | Total tokens bare / repowise | Change [95% CI] |
|---|---:|---:|
| Opus 5.5 | 121k / 128k | +5.2% [-13.5, +20.8] |
| Sonnet 5.5 | 101k / 104k | +3.4% [-6.8, +13.4] |
| Opus 4.8 | 121k / 128k | +6.3% [-11.6, +24.3] |
| Sonnet 4.6 | 131k / 139k | +6.6% [-8.4, +22.2] |
| Haiku 4.5 | 288k / 306k | +6.5% [-14.1, +27.4] |

## By repository

| Model | Repo | Output tokens bare / repowise | Change [95% CI] | Leaner on | Files opened bare / repowise | Correct bare / repowise |
|---|---|---:|---:|---:|---:|---:|
| Opus 5.5 | foreman (Python) | 2,037 / 1,608 | -21.1% [-35.2, -8.8] | 7 of 8 | 3.3 / 0.5 | 16 / 16 of 16 |
| Opus 5.5 | okf-agent-memory (Go) | 1,452 / 1,243 | -14.4% [-25.4, -4.7] | 7 of 8 | 2.1 / 0.1 | 16 / 16 of 16 |
| Opus 5.5 | leviathan (Rust) | 1,361 / 1,314 | -3.5% [-11.1, +4.6] | 6 of 8 | 1.4 / 0.5 | 16 / 16 of 16 |
| Sonnet 5.5 | foreman (Python) | 1,649 / 1,443 | -12.5% [-20.0, -5.0] | 7 of 8 | 1.8 / 1.1 | 14 / 16 of 16 |
| Sonnet 5.5 | okf-agent-memory (Go) | 1,221 / 1,026 | -15.9% [-24.4, -7.3] | 7 of 8 | 1.5 / 0.7 | 16 / 16 of 16 |
| Sonnet 5.5 | leviathan (Rust) | 1,293 / 1,294 | +0.1% [-10.4, +12.5] | 5 of 8 | 1.3 / 0.9 | 16 / 16 of 16 |
| Opus 4.8 | foreman (Python) | 2,300 / 1,627 | -29.3% [-80.5, +4.3] | 3 of 8 | 2.4 / 0.2 | 15 / 15 of 16 |
| Opus 4.8 | okf-agent-memory (Go) | 1,459 / 1,258 | -13.8% [-39.2, +9.9] | 6 of 8 | 2.2 / 0.3 | 16 / 16 of 16 |
| Opus 4.8 | leviathan (Rust) | 1,268 / 1,413 | +11.4% [-11.9, +36.6] | 3 of 8 | 1.8 / 0.6 | 16 / 15 of 16 |
| Sonnet 4.6 | foreman (Python) | 2,711 / 1,253 | -53.8% [-83.9, -22.9] | 7 of 8 | 4.9 / 0.0 | 22 / 22 of 24 |
| Sonnet 4.6 | okf-agent-memory (Go) | 3,021 / 1,112 | -63.2% [-90.5, -32.0] | 8 of 8 | 3.9 / 0.0 | 24 / 24 of 24 |
| Sonnet 4.6 | leviathan (Rust) | 1,310 / 1,073 | -18.1% [-63.9, +20.1] | 3 of 8 | 1.8 / 0.0 | 24 / 21 of 24 |
| Haiku 4.5 | foreman (Python) | 3,150 / 2,694 | -14.5% [-24.6, -4.5] | 6 of 8 | 4.3 / 0.8 | 24 / 24 of 24 |
| Haiku 4.5 | okf-agent-memory (Go) | 3,025 / 2,757 | -8.9% [-32.6, +11.2] | 5 of 8 | 4.1 / 0.0 | 22 / 23 of 24 |
| Haiku 4.5 | leviathan (Rust) | 2,547 / 2,811 | +10.4% [-3.1, +25.5] | 3 of 8 | 2.7 / 0.2 | 24 / 22 of 24 |

## By question kind

| Model | Kind | Output tokens bare / repowise | Change [95% CI] | Leaner on | Files opened bare / repowise | Correct bare / repowise |
|---|---|---:|---:|---:|---:|---:|
| Opus 5.5 | locate | 993 / 832 | -16.2% [-31.0, -3.1] | 7 of 9 | 1.3 / 0.3 | 18 / 18 of 18 |
| Opus 5.5 | flow | 2,344 / 2,095 | -10.6% [-23.7, +0.2] | 7 of 9 | 3.3 / 0.6 | 18 / 18 of 18 |
| Opus 5.5 | impact | 1,462 / 1,162 | -20.5% [-29.6, -12.4] | 6 of 6 | 2.1 / 0.1 | 12 / 12 of 12 |
| Sonnet 5.5 | locate | 854 / 727 | -14.8% [-28.9, +0.0] | 7 of 9 | 0.7 / 0.6 | 18 / 18 of 18 |
| Sonnet 5.5 | flow | 2,044 / 1,921 | -6.0% [-13.7, +2.5] | 7 of 9 | 2.6 / 1.4 | 18 / 18 of 18 |
| Sonnet 5.5 | impact | 1,203 / 1,045 | -13.1% [-20.6, -3.3] | 5 of 6 | 1.2 / 0.6 | 10 / 12 of 12 |
| Opus 4.8 | locate | 959 / 904 | -5.7% [-26.4, +15.1] | 5 of 9 | 1.4 / 0.3 | 18 / 18 of 18 |
| Opus 4.8 | flow | 2,472 / 1,934 | -21.8% [-67.5, +10.6] | 5 of 9 | 2.8 / 0.3 | 17 / 18 of 18 |
| Opus 4.8 | impact | 1,557 / 1,472 | -5.5% [-37.6, +18.8] | 2 of 6 | 2.2 / 0.4 | 12 / 10 of 12 |
| Sonnet 4.6 | locate | 1,444 / 706 | -51.1% [-105.0, -4.5] | 6 of 9 | 2.3 / 0.0 | 27 / 27 of 27 |
| Sonnet 4.6 | flow | 3,871 / 1,766 | -54.4% [-71.9, -32.4] | 8 of 9 | 5.7 / 0.0 | 27 / 27 of 27 |
| Sonnet 4.6 | impact | 1,417 / 877 | -38.2% [-102.3, +8.7] | 4 of 6 | 2.2 / 0.0 | 16 / 13 of 18 |
| Haiku 4.5 | locate | 1,832 / 1,727 | -5.7% [-24.0, +11.1] | 5 of 9 | 2.3 / 0.1 | 27 / 27 of 27 |
| Haiku 4.5 | flow | 3,904 / 3,805 | -2.5% [-10.8, +7.5] | 6 of 9 | 4.7 / 0.2 | 25 / 27 of 27 |
| Haiku 4.5 | impact | 3,026 / 2,718 | -10.2% [-42.4, +19.5] | 3 of 6 | 4.3 / 0.9 | 18 / 15 of 18 |
