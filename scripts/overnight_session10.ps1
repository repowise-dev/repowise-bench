# Overnight queue for the night of 2026-08-03, after Layer B session 10.
#
# Runs A -> B -> C -> D strictly sequentially. Nothing here makes a decision a
# human would have to make; every step either passes its own gate or is skipped,
# and a skipped step never silently becomes a result.
#
#   A  flask48 under the pinned environment            settles D16
#   B  repowise surface ablation (11 vs 8 vs 4)        explains the rung 6 lean row
#   C  Codex on the stratified draw                    second harness, adoption only
#   D  timed index builds across arms                  $0 API, MUST run alone (E1)
#
# D is last on purpose. Finding E1 measured a 65% inflation when a timed build
# runs under a live process pool, so it may not overlap A/B/C — sequential
# execution is what guarantees that, and it is why D is not run in parallel to
# save wall clock.
#
# D is pinned to `--repos django,cli`. Its DEFAULT repo list includes **mui**,
# which is a held-out validation set whose index must not be rebuilt, and
# `run_cell` wipes every arm's artifact dir in the tree it builds in.
#
# Each step writes its own fresh log via `*>` so the file is created rather than
# appended (appending from PowerShell writes UTF-16LE, which is finding-worthy
# on its own: session 9 lost a hook counter to exactly that).

$ErrorActionPreference = "Continue"
$bench = "C:\Users\ragha\Desktop\repowise\repowise-bench"
$py    = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe"
$logs  = "$bench\logs\overnight_session10"
$chain = "$logs\CHAIN.log"

Set-Location $bench
New-Item -ItemType Directory -Force -Path $logs | Out-Null

$env:REPOWISE_ROOT           = "C:/Users/ragha/Desktop/repowise-layerb"
$env:REPOWISE_EXE            = "C:/Users/ragha/Desktop/repowise-layerb/.venv/Scripts/repowise.exe"
$env:FORCE_PROMPT_CACHING_5M = "1"
$env:DO_NOT_TRACK            = "1"
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:PYTHONPATH              = $bench

function Say($msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Write-Output $line
  Add-Content -Path $chain -Value $line -Encoding utf8
}

Say "=== overnight queue starting ==="
Say "REPOWISE_ROOT=$env:REPOWISE_ROOT"

# ---------------------------------------------------------------- A: flask48
Say "A1: flask48 canary (2 tasks, 4 cells) — proves hooks are off before 96 cells spend"
& $py -u harness/run_experiment.py --config configs/swe_qa_flask48_d16_canary.yaml *> "$logs\A1_canary.log"
Say "A1: exit=$LASTEXITCODE"

& $py -m harness.canary_gate `
    --results results/bakeoff_2026_08/rung6/swe_qa_flask48_d16_canary `
    --expect-cells 4 --require-exercised C2_full *> "$logs\A2_gate.log"
$gate = $LASTEXITCODE
Say "A2: canary gate exit=$gate (0 = passed)"

if ($gate -eq 0) {
  Say "A3: flask48 full, 48 tasks x 2 conditions = 96 cells"
  & $py -u harness/run_experiment.py --config configs/swe_qa_flask48_d16.yaml *> "$logs\A3_flask48.log"
  Say "A3: exit=$LASTEXITCODE"
} else {
  Say "A3: SKIPPED — the canary gate failed, so the environment is not proven pinned."
  Say "A3: a 96-cell run under unproven isolation measures nothing. See A2_gate.log."
}

# -------------------------------------------------- B: repowise surface ablation
Say "B: repowise surface ablation, 4 arms x 15 questions = 60 cells"
& $py -u harness/run_experiment.py --config configs/layerb_surface_ablation_django.yaml *> "$logs\B_ablation.log"
Say "B: exit=$LASTEXITCODE"

& $py -m harness.report_by_shape `
    --results results/bakeoff_2026_08/rung6/layerb_surface_ablation_django `
    --out results/bakeoff_2026_08/rung6/report__ablation.json *> "$logs\B_report.log"
Say "B: report exit=$LASTEXITCODE"

# ------------------------------------------------------------------ C: Codex
Say "C1: Codex isolation probe — REQUIRED before any Codex cell"
& $py -u harness/codex_isolation_probe.py *> "$logs\C1_probe.log"
$probe = $LASTEXITCODE
Say "C1: probe exit=$probe (0 = isolation holds)"

if ($probe -eq 0) {
  Say "C2: Codex on the stratified draw, 2 arms x 15 = 30 cells"
  & $py -u harness/run_experiment.py --config configs/layerb_codex_stratified_django.yaml *> "$logs\C2_codex.log"
  Say "C2: exit=$LASTEXITCODE"
} else {
  Say "C2: SKIPPED — the Codex isolation probe did not pass."
  Say "C2: Codex emits no hook events, so a cell cannot report 'no hooks fired'"
  Say "C2: about itself. Without the probe there is no evidence of isolation."
}

# ------------------------------------------------- D: timed index builds, ALONE
Say "D: timed index builds. Everything above has exited, so nothing contends (E1)."
Say "D: repos pinned to django,cli — the DEFAULT list includes mui, which is held out."
& $py -u results/bakeoff_2026_08/rung4/smoke_matrix.py `
    --repos django,cli `
    --out results/bakeoff_2026_08/rung6/index_timing_session10.json *> "$logs\D_index_timing.log"
Say "D: exit=$LASTEXITCODE"

Say "=== overnight queue finished ==="
