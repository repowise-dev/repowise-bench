# Resume after both overnight tasks were killed, 2026-08-05.
#
# WHAT HAPPENED. The Layer A chain and the stability recheck were both stopped
# at the same moment, which points at a session/harness boundary rather than a
# machine or harness-internal fault: exit codes were clean, no traceback, and no
# orphaned build workers were left behind (checked before relaunching).
#
# WHAT SURVIVED.
#   leg 1  dev-fix2   COMPLETE, 70/70, graded. Untouched by this script.
#   leg 2  test-fix2  killed at 30 of 42 builds. All 60 cells are on disk and
#                     the runner resumes on (instance_id, arm), so this picks up
#                     the missing 12 instances and then grades all 42.
#   recheck           never started. It was waiting for a "chain done" line the
#                     killed chain never wrote.
#
# THE FIX TO THE DESIGN. The recheck no longer polls a log file for a sentinel.
# It runs sequentially in this script, after leg 2 returns. A wait loop keyed on
# another process's log line is a second thing that can fail silently, and last
# night it did: the chain died, the sentinel never appeared, and the recheck sat
# waiting until it was killed too. Sequential is strictly more robust here.
#
# Tags: test-fix2, then rc1/rc2/rc3. dev-emb1, test-emb1 and dev-fix2 are NEVER
# written -- dev-fix2 is a finished result now and must not be re-opened.
#
# ASCII only (PowerShell 5.1 reads a BOM-less .ps1 as ANSI).

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DO_NOT_TRACK = "1"
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:REPOWISE_EXE = "C:\Users\ragha\Desktop\repowise-devfix2\.venv\Scripts\repowise.exe"

$py     = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe"
$runner = "C:\Users\ragha\Desktop\repowise\repowise-bench\results\bakeoff_2026_08\rung8\rung8_runner.py"
$logDir = "C:\Users\ragha\Desktop\repowise\repowise-bench\logs\resume_recheck"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "RESUME.log"

function Note($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Write-Output $line
  $line | Out-File -FilePath $log -Encoding utf8 -Append
}

$TARGET  = "SWE-Bench-Verified__python__maintenance__bugfix__81f2c925"
$CONTROL = "SWE-Bench-Verified__python__maintenance__bugfix__1a760e52"

Note "resume start. REPOWISE_EXE=$env:REPOWISE_EXE"

# ---- STEP 1: finish the sealed 42 -------------------------------------------
Note "STEP 1 test-fix2 resume (30 of 42 already ok on disk; 12 to go)"
& $py $runner --split test --workers 3 --tag test-fix2 `
    --arms repowise repowise-search `
    *>&1 | Tee-Object -FilePath (Join-Path $logDir "step1_test.log") -Append
Note "STEP 1 test-fix2 exit $LASTEXITCODE"

# ---- STEP 2: the stability recheck ------------------------------------------
# Three independent rebuilds of the one regressed instance (81f2c925, File
# Coverage 1.000 -> 0.000) plus three of a MATCHED control (1a760e52: python,
# gold_size 1, coverage 1.000 in both builds, pred_size 20 so it sits under the
# same tail pressure). Distinguishes a stable regression from tail-of-pool
# churn, and if the CONTROL also flips that is the bigger finding.
foreach ($tag in @("rc1", "rc2", "rc3")) {
  Note "STEP 2 $tag start"
  & $py $runner --instances $TARGET $CONTROL --workers 2 --tag $tag `
      --arms repowise --rebuild `
      *>&1 | Tee-Object -FilePath (Join-Path $logDir "$tag.log") -Append
  Note "STEP 2 $tag exit $LASTEXITCODE"
}

Note "resume done."
