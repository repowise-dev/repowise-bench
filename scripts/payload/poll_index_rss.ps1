# Peak RSS of the indexing job, matched by PROCESS TREE rather than by name or
# by a substring of the command line.
#
# Two dead detectors in this workstream came from getting this wrong, and this
# session very nearly produced a third:
#
#   * last session, `Get-Process repowise` matched the session's own idle MCP
#     server and reported a rock-steady 5 MB peak for a job that had never
#     launched;
#   * the fix was "match on Win32_Process.CommandLine LIKE '%<tree>%'", and THAT
#     fails here for the opposite reason: the actual worker is spawned as
#     `python.exe <path>\repowise.exe ...` and its command line does NOT contain
#     the tree name at all. Only the launcher and the console-script shim carry
#     it, and both sit at ~0-5 MB. A CommandLine-on-tree match would have
#     reported single-digit megabytes while the real worker was at 7 GB.
#
# So: walk DOWN from the launcher PID. Descendants are the job by definition,
# whatever they are named and whatever their argv looks like.
#
# Proved in both directions by construction: it prints `no descendants` before
# the job starts and a non-zero peak while it runs.

param([Parameter(Mandatory = $true)][int]$RootPid,
      [int]$IntervalSec = 15)

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

$peak = 0
while ($true) {
  $d = @(Get-Descendants $RootPid)
  if (-not (Get-Process -Id $RootPid -ErrorAction SilentlyContinue)) {
    Write-Host "ROOT $RootPid EXITED. PEAK_TREE_RSS_MB=$peak"
    break
  }
  if ($d.Count -eq 0) {
    Write-Host "$(Get-Date -Format HH:mm:ss)  no descendants yet"
  } else {
    $sum = [math]::Round((($d | Measure-Object WorkingSetSize -Sum).Sum) / 1MB, 0)
    $max = [math]::Round((($d | Measure-Object WorkingSetSize -Maximum).Maximum) / 1MB, 0)
    if ($sum -gt $peak) { $peak = $sum }
    Write-Host "$(Get-Date -Format HH:mm:ss)  procs=$($d.Count) tree_rss=${sum}MB largest=${max}MB peak=${peak}MB"
  }
  Start-Sleep -Seconds $IntervalSec
}
