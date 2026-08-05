# Overnight stability recheck, queued 2026-08-04 after leg 1 landed.
#
# THE QUESTION. dev-fix2 produced exactly one regression: 81f2c925, File
# Coverage 1.000 -> 0.000. Its autopsy says both builds served 20 files, the
# lists differ by 3 paths, and gold_size is 1 -- so one file falling off the tail
# of a 20-path cut flips the score all the way. That READS like marginal churn
# rather than a systematic regression, but reading is not measuring, and this is
# the single regression in a headline number.
#
# THE DESIGN. Three independent rebuilds of the regressed instance, plus three
# of a MATCHED CONTROL, so the answer distinguishes two very different worlds:
#
#   81f2c925 misses 3/3 and the control holds 3/3
#       -> the regression is real and stable. Publish it as a regression.
#   81f2c925 is mixed, or the control also flips
#       -> the ranked pool churns at the tail generally, several of the 70 are
#          coin flips, and the headline needs a stability caveat. That would be
#          the more important finding of the two.
#
# CONTROL CHOICE, fixed before the run and on the record: 1a760e52. Python (same
# as the target), gold_size 1 (equally all-or-nothing), coverage 1.000 in BOTH
# builds (so a flip is visible), and pred_size 20 (the SAME full cut as
# 81f2c925, so it is under the same tail pressure). Picked deterministically by
# scripts/pick_stability_control.py. 02b5862c sorted first but served only 18
# paths, never reached the cut, and would have been a weaker control.
#
# Waits for the Layer A chain to finish first. Finding E1: a concurrent process
# pool inflates builds 65%, and more to the point two runs competing for three
# workers each is how a night produces neither result.
#
# Tags rc1 / rc2 / rc3. dev-emb1, dev-fix2 and test-fix2 are never written.
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
$chainLog = "C:\Users\ragha\Desktop\repowise\repowise-bench\logs\layerA_chain\CHAIN.log"
$logDir = "C:\Users\ragha\Desktop\repowise\repowise-bench\logs\stability_recheck"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "RECHECK.log"

function Note($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Write-Output $line
  $line | Out-File -FilePath $log -Encoding utf8 -Append
}

$TARGET  = "SWE-Bench-Verified__python__maintenance__bugfix__81f2c925"
$CONTROL = "SWE-Bench-Verified__python__maintenance__bugfix__1a760e52"

Note "recheck queued. waiting for the Layer A chain to finish."

# ---- wait for the chain, with a ceiling so this cannot hang all night --------
$waited = 0
while ($waited -lt 21600) {           # 6h ceiling
  if (Test-Path $chainLog) {
    if (Select-String -Path $chainLog -Pattern 'chain done' -Quiet) { break }
  }
  Start-Sleep -Seconds 60
  $waited += 60
}
if ($waited -ge 21600) {
  Note "GAVE UP waiting for the chain after 6h. Recheck NOT run, so that a"
  Note "stalled chain does not silently turn into a contended recheck."
  exit 2
}
Note "chain finished after ${waited}s of waiting. starting recheck."

# ---- three independent rebuilds of both instances ---------------------------
foreach ($tag in @("rc1", "rc2", "rc3")) {
  Note "$tag start"
  & $py $runner --instances $TARGET $CONTROL --workers 2 --tag $tag `
      --arms repowise --rebuild `
      *>&1 | Tee-Object -FilePath (Join-Path $logDir "$tag.log") -Append
  Note "$tag exit $LASTEXITCODE"
}

Note "recheck done. summarise with 50-results/dev-fix2/scripts/stability_summary.py"
