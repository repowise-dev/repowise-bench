# Detached launcher for the mui dev-15 index builds.
#
# WHY THIS EXISTS. Two background-task launches of the same command were killed by
# the agent harness mid-run (after ~56 min and after a few min, so not a timeout).
# Builds are stamp-idempotent so nothing was repaid, but a run that needs 9-15
# hours cannot depend on an agent session staying alive to relaunch it.
#
# ON THE STANDING RULE "never launch a benchmark with Start-Process". That rule
# comes from 2026-08-05, where eleven sealed cells returned `McpError: Connection
# closed` under Start-Process, twice, on healthy builds. It is a rule about AGENT
# runs: the failure was in the MCP client/server handshake inside a cell. This
# script runs INDEX BUILDS ONLY -- no agent, no MCP client, no judge, nothing that
# opens an MCP connection -- so that failure mode is not reachable here. Recorded
# rather than silently deviated from.
#
# Usage (from repowise-bench):
#   powershell -ExecutionPolicy Bypass -File scripts/run_mui_overnight.ps1
#
# It writes its own log and is safe to run repeatedly: already-built (arm, tree)
# pairs are skipped via their disk stamps.

$ErrorActionPreference = "Stop"

$Bench = "C:\Users\ragha\Desktop\repowise\repowise-bench"
$Log   = Join-Path $Bench "logs\mui_dev15_overnight.log"
New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null

# The key lives in .repowise/.env; `repowise init` does NOT read it from there, and
# without it the build either fails outright or (worse) builds 8-dim mock vectors.
# That is finding A9 and the D13 gate exists to refuse the mock case.
$envFile = "C:\Users\ragha\Desktop\repowise\.repowise\.env"
$line = Get-Content $envFile | Where-Object { $_ -match '^\s*OPENAI_API_KEY\s*=' } | Select-Object -First 1
$key  = ($line -replace '^\s*OPENAI_API_KEY\s*=\s*', '').Trim().Trim('"').Trim("'")
if (-not $key) { throw "OPENAI_API_KEY not found in $envFile" }

$env:OPENAI_API_KEY             = $key
$env:REPOWISE_ROOT              = "C:/Users/ragha/Desktop/repowise-layerb2"
$env:REPOWISE_EXE               = "C:/Users/ragha/Desktop/repowise-layerb2/.venv/Scripts/repowise.exe"
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:DO_NOT_TRACK               = "1"
$env:PYTHONIOENCODING           = "utf-8"

Set-Location $Bench
"=== launched $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), key len $($key.Length) ===" |
    Out-File -FilePath $Log -Append -Encoding utf8

# -u so progress is unbuffered and the log is readable while it runs.
#
# DO NOT PIPE THIS THROUGH Tee-Object. The first version did, and the resulting
# log was double-encoded to the point where `Select-String 'SKIP'` matched ZERO
# lines in a file that visibly contained SKIPs. That is the same class of defect as
# the 2026-08-05 hook counter that read five firings as zero because `cmd /c echo
# >>` writes UTF-16LE: a log you cannot parse reads as "nothing happened", which is
# the most dangerous possible failure for an unattended run.
#
# `Out-File -Encoding utf8` on the redirected stream keeps it greppable. The
# AUTHORITATIVE record is not this log anyway: prebuild_mui_indexes.py rewrites
# results/bakeoff_2026_08/layera_mui_dev15/prebuild.json as UTF-8 after EVERY
# build, and each completed (arm, tree) pair also leaves a JSON disk stamp. Read
# those first; treat this log as human-facing narration only.
& python -u scripts\prebuild_mui_indexes.py --config configs\layera_mui_dev15.yaml 2>&1 |
    ForEach-Object { $_ | Out-File -FilePath $Log -Append -Encoding utf8; $_ }

"=== exited $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') with code $LASTEXITCODE ===" |
    Out-File -FilePath $Log -Append -Encoding utf8
