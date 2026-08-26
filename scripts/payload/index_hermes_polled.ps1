# Index ONE hermes tree with the RSS poller armed BEFORE init starts.
#
# This closes owed item #4 from RESULT_S3C_SMOKE.md §2.4. There, the poller was
# armed at 19:33, after a single direct observation of 7,033 MB at 19:32, so it
# could not bracket the true peak and its 1,712 MB was a peak-over-its-own-window
# only. The heavy phase (duplication analysis) was already over before sampling
# began. A number that cannot bracket the peak is not a peak.
#
# The fix is structural rather than a matter of being quicker: `init` is launched
# with -PassThru so its PID exists before any child does, and sampling starts
# from that PID at t=0. Descendants are the job by definition -- see
# poll_index_rss.ps1 for why neither an image-name match nor a CommandLine match
# selects the right process here (both have already published a false all-clear).
#
# Usage: powershell -File index_hermes_polled.ps1 -Tree C:\...\se-hermes-pf-golden

param([Parameter(Mandatory = $true)][string]$Tree,
      [int]$IntervalSec = 15)

# CLAUDE.md: without these the rich checkmark glyph crashes the CLI with a
# charmap codec error on Windows.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Standing rule 5: there is ONE global repowise MCP key and an unguarded init
# repoints the operator's editor.
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:DO_NOT_TRACK = "1"

# The key is the whole reason `index_vector_dim` is a hard gate: without it,
# `--embedder openai` silently falls back to MockEmbedder, writes 8-dimensional
# vectors, exits 0, and init's summary does not say so (finding D13).
Get-Content C:\Users\ragha\Desktop\repowise\.env | ForEach-Object {
  if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$') {
    $v = $matches[2].Trim('"').Trim("'")
    if ($matches[1] -in @('OPENAI_API_KEY','OPENAI_BASE_URL')) {
      [Environment]::SetEnvironmentVariable($matches[1], $v)
    }
  }
}
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY not loaded from .env" }
Write-Host "OPENAI_API_KEY loaded, $($env:OPENAI_API_KEY.Length) chars"

function Get-Descendants([int]$root) {
  $all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, WorkingSetSize
  $keep = @{}; $frontier = @($root)
  while ($frontier.Count -gt 0) {
    $next = @()
    foreach ($p in $all) {
      if ($frontier -contains $p.ParentProcessId -and -not $keep.ContainsKey($p.ProcessId)) {
        $keep[$p.ProcessId] = $p; $next += $p.ProcessId
      }
    }
    $frontier = $next
  }
  $keep.Values
}

$exe = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\repowise.exe"
$out = Join-Path $env:TEMP "hermes_pf_init_out.txt"
$err = Join-Path $env:TEMP "hermes_pf_init_err.txt"

$t0 = Get-Date
$proc = Start-Process -FilePath $exe `
  -ArgumentList @("init","--embedder","openai","--max-file-pages","0",
                  "--no-workspace","--no-editor-setup","--yes") `
  -WorkingDirectory $Tree -PassThru -NoNewWindow `
  -RedirectStandardOutput $out -RedirectStandardError $err

Write-Host "LAUNCHER_PID=$($proc.Id)  poller armed at t=0 (before any child exists)"

# Sample the whole process tree, including the launcher itself: on this repo the
# real worker is a descendant, but a peak that excluded the root would be a
# different measurement than the one claimed.
$peak = 0; $peakAt = ""; $samples = 0
while ($true) {
  $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
  $d = @(Get-Descendants $proc.Id)
  if ($alive) { $d += (Get-Process -Id $proc.Id) }
  if ($d.Count -gt 0) {
    $sum = [math]::Round((($d | Measure-Object WorkingSetSize -Sum).Sum) / 1MB, 0)
    $max = [math]::Round((($d | Measure-Object WorkingSetSize -Maximum).Maximum) / 1MB, 0)
    $samples++
    if ($sum -gt $peak) { $peak = $sum; $peakAt = (Get-Date -Format HH:mm:ss) }
    $free = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1KB, 0)
    Write-Host "$(Get-Date -Format HH:mm:ss)  procs=$($d.Count) tree_rss=${sum}MB largest=${max}MB peak=${peak}MB free=${free}MB"
  } else {
    Write-Host "$(Get-Date -Format HH:mm:ss)  no descendants yet"
  }
  if (-not $alive) { break }
  Start-Sleep -Seconds $IntervalSec
}

$rc = $proc.ExitCode
$mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
Write-Host ""
Write-Host "INIT_RC=$rc ELAPSED_MIN=$mins PEAK_TREE_RSS_MB=$peak PEAK_AT=$peakAt SAMPLES=$samples"
Write-Host "stdout -> $out"
Write-Host "stderr -> $err"
exit $rc
