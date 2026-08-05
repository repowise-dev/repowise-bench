# Layer A chain, 2026-08-04 evening.
#
#   LEG 1  dev-fix2   finish the dev half. The runner resumes on
#                     (instance_id, arm): a cell whose record exists with
#                     status == "ok" is skipped whole, so this runs the 25
#                     missing python instances and then re-grades all 70.
#   LEG 2  test-fix2  the SEALED 42, on the post-gate-fix build. Second touch,
#                     agreed with Raghav, and both conditions are on record in
#                     NEXT_SESSION.md: whatever comes back gets published, and
#                     docs/BENCHMARKS.md must stop saying "evaluated once".
#
# ASCII only. Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so a UTF-8
# em dash decodes to cp1252 bytes and the parser dies forty lines later with a
# missing-string-terminator (SESSION10 section 9).
#
# Tags: dev-fix2 and test-fix2. NEITHER dev-emb1 NOR test-emb1 is written.
# Those two are the "before" the whole comparison rests on.
#
# REPOWISE_ROOT is deliberately NOT set. The key resolver reads
# REPOWISE_ROOT/provider_config.json and that file lives only in the main
# checkout. Pointing ROOT at the worktree would silently build 8-dim mock
# indexes, which is finding D13.

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DO_NOT_TRACK = "1"
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:REPOWISE_EXE = "C:\Users\ragha\Desktop\repowise-devfix2\.venv\Scripts\repowise.exe"

$py     = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe"
$runner = "C:\Users\ragha\Desktop\repowise\repowise-bench\results\bakeoff_2026_08\rung8\rung8_runner.py"
$logDir = "C:\Users\ragha\Desktop\repowise\repowise-bench\logs\layerA_chain"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$chain = Join-Path $logDir "CHAIN.log"

function Note($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Write-Output $line
  $line | Out-File -FilePath $chain -Encoding utf8 -Append
}

Note "chain start. REPOWISE_EXE=$env:REPOWISE_EXE"

# ---- LEG 1: finish the dev half -------------------------------------------
Note "LEG 1 dev-fix2 start (resumes; 45 of 70 already ok on disk)"
& $py $runner --split dev --workers 3 --tag dev-fix2 `
    --arms repowise repowise-search `
    *>&1 | Tee-Object -FilePath (Join-Path $logDir "leg1_dev.log") -Append
$leg1 = $LASTEXITCODE
Note "LEG 1 dev-fix2 exit $leg1"

# ---- LEG 2: the sealed test half ------------------------------------------
# Runs regardless of leg 1's exit code. Leg 1 partially failing does not make
# the sealed run less valid, and a chain that silently skips its second half on
# a nonzero exit is how a night gets wasted. Leg 2's own health is asserted
# from its cells before anything is read.
Note "LEG 2 test-fix2 start (SEALED 42, second touch, publish-whatever-returns)"
& $py $runner --split test --workers 3 --tag test-fix2 `
    --arms repowise repowise-search `
    *>&1 | Tee-Object -FilePath (Join-Path $logDir "leg2_test.log") -Append
$leg2 = $LASTEXITCODE
Note "LEG 2 test-fix2 exit $leg2"

Note "chain done. leg1=$leg1 leg2=$leg2"
