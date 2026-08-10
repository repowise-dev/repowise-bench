# Detached launcher for the mui SEALED-30 index builds.
#
# Copied from `run_mui_overnight.ps1` 2026-08-09, changing only the config, the
# log path and the preflight. The dev-15 launcher is left alone as the record of
# what built the dev half.
#
# WHY THIS EXISTS. Two background-task launches of the dev-15 command were killed
# by the agent harness mid-run (after ~56 min and after a few min, so not a
# timeout). Builds are stamp-idempotent so nothing was repaid, but a run that
# needs ~16 hours cannot depend on an agent session staying alive to relaunch it.
#
# ON THE STANDING RULE "never launch a benchmark with Start-Process". That rule
# comes from 2026-08-05, where eleven sealed cells returned `McpError: Connection
# closed` under Start-Process, twice, on healthy builds. It is a rule about AGENT
# runs: the failure was in the MCP client/server handshake inside a cell. This
# script runs INDEX BUILDS ONLY -- no agent, no MCP client, no judge, nothing
# that opens an MCP connection -- so that failure mode is not reachable here.
# Recorded rather than silently deviated from. The QUERY half of this experiment
# (`grade_mui_layera.py`) does open MCP connections and must NOT be launched this
# way.
#
# THIS RUN SPENDS THE SEAL. The 30 held-out instances are evaluated once and
# whatever comes back is published. Nothing measured here may be tuned against.
#
# Usage (from repowise-bench):
#   powershell -ExecutionPolicy Bypass -File scripts/run_mui_sealed30.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/run_mui_sealed30.ps1 -Instances "cbmui_c2e9e5ff,cbmui_6cb88b18"
#
# `-Instances` IS FOR THE GATE, AND THE GATE NEEDS DETACHING AS MUCH AS THE RUN
# DOES. 2026-08-09: the two-instance gate was launched as an agent background
# task instead of through this script, on the reasoning that 80 minutes is short
# enough to babysit. The harness killed it at ~38 minutes, mid-build, after the
# repowise index had reached 416 MB. That is the third harness kill in this
# workstream and the first one where the launcher already existed and was not
# used. The gate is part of the same 30, so nothing was repaid beyond the
# machine time -- but the rule is now: anything that builds an index goes
# through this script, however short it looks.
#
# It writes its own log and is safe to run repeatedly: already-built (arm, tree)
# pairs are skipped via their disk stamps, so a kill repays nothing.

# ONE COMMA-SEPARATED STRING, NOT A [string[]], AND THAT IS DELIBERATE.
# `powershell -File script.ps1 -Instances a b` binds ONLY `a` to a [string[]]
# parameter and drops `b` on the floor without a word. Measured 2026-08-10: the
# gate was launched for two instances and the log read "1 instances x 5 arms = 5
# builds". A silent truncation in a launcher is the same failure class as the
# mis-encoded log that matched zero SKIPs -- the run looks like it did what you
# asked. Taking one string and splitting it here makes the count explicit, and
# the launcher echoes what it parsed.
param(
    [string] $Instances = ""
)
$InstanceList = @($Instances -split '[,\s]+' | Where-Object { $_ })

$ErrorActionPreference = "Stop"

$Bench = "C:\Users\ragha\Desktop\repowise\repowise-bench"
$Log   = if ($InstanceList.Count) { Join-Path $Bench "logs\mui_sealed30_gate.log" }
         else                     { Join-Path $Bench "logs\mui_sealed30.log" }
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

# PREFLIGHT, RECORDED RATHER THAN ASSUMED. The dev-15 overnight run logged 9.2 GB
# free RAM and 246 GB free disk at launch, and an earlier attempt died at 5.7 GB
# free disk. Finding E1 says a live process pool inflates timings by up to 65%
# and Layer A section B measured the inflation as ARM-SPECIFIC (1.03x to 3.31x),
# which cannot be corrected after the fact. These lines are written to the log so
# that if the numbers come out strange there is a machine state to read them
# against, instead of a guess made the next morning.
$os   = Get-CimInstance Win32_OperatingSystem
$free = (Get-PSDrive C).Free / 1GB
$ram  = $os.FreePhysicalMemory / 1MB
$busy = Get-Process |
    Where-Object { $_.WorkingSet64 -gt 300MB -and $_.Name -notmatch '^(claude|powershell|pwsh)$' } |
    ForEach-Object { "{0}({1}) {2:N0}MB" -f $_.Name, $_.Id, ($_.WorkingSet64/1MB) }

@(
  "=== launched $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), key len $($key.Length), " +
  "scope $(if ($InstanceList.Count) { "GATE ($($InstanceList.Count)): " + ($InstanceList -join ' ') } else { 'ALL 30' }) ==="
  "preflight: disk free {0:N1} GB, RAM free {1:N1} GB of {2:N1} GB" -f $free, $ram, ($os.TotalVisibleMemorySize/1MB)
  "preflight: processes over 300MB: $(if ($busy) { $busy -join ', ' } else { 'none' })"
) | Out-File -FilePath $Log -Append -Encoding utf8

# -u so progress is unbuffered and the log is readable while it runs.
#
# DO NOT PIPE THIS THROUGH Tee-Object. The first dev-15 version did, and the
# resulting log was double-encoded to the point where `Select-String 'SKIP'`
# matched ZERO lines in a file that visibly contained SKIPs. For an unattended run
# that reads as "nothing happened", which is the most dangerous possible failure
# mode. `Out-File -Encoding utf8` on the redirected stream keeps it greppable.
#
# THE AUTHORITATIVE RECORD IS NOT THIS LOG. `prebuild_mui_indexes.py` rewrites
# results/bakeoff_2026_08/layera_mui_sealed30/prebuild.json as UTF-8 after EVERY
# build, and each completed (arm, tree) pair also leaves a JSON disk stamp in its
# own tree. Read those first; this log is human-facing narration only.
$argv = @("-u", "scripts\prebuild_mui_indexes.py", "--config", "configs\layera_mui_sealed30.yaml")
if ($InstanceList.Count) { $argv += @("--instances") + $InstanceList }

& python @argv 2>&1 |
    ForEach-Object { $_ | Out-File -FilePath $Log -Append -Encoding utf8; $_ }

"=== exited $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') with code $LASTEXITCODE ===" |
    Out-File -FilePath $Log -Append -Encoding utf8
