# Track A: re-score the Layer A dev half on current main (081a59fa), which
# carries both gate fixes -- #1284 (gates 4/5/8) and #1289 (gate 2).
#
# ASCII only. Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so a UTF-8
# em dash decodes to cp1252 bytes and the parser dies forty lines later with a
# missing-string-terminator (SESSION10 section 9).
#
# REPOWISE_EXE points at the devfix2 worktree. REPOWISE_ROOT is deliberately
# NOT set: the key resolver reads REPOWISE_ROOT/provider_config.json and that
# file lives only in the main checkout (it is untracked, so the worktree has no
# copy). Pointing ROOT at the worktree would silently build 8-dim mock indexes,
# which is finding D13.

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:DO_NOT_TRACK = "1"
$env:REPOWISE_SKIP_EDITOR_SETUP = "1"
$env:REPOWISE_EXE = "C:\Users\ragha\Desktop\repowise-devfix2\.venv\Scripts\repowise.exe"

$logDir = "C:\Users\ragha\Desktop\repowise\repowise-bench\logs\devfix2"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "run.log"

"=== track A dev-fix2 start ===" | Out-File -FilePath $log -Encoding utf8
"REPOWISE_EXE = $env:REPOWISE_EXE" | Out-File -FilePath $log -Encoding utf8 -Append

& "C:\Users\ragha\Desktop\repowise\.venv\Scripts\python.exe" `
  "C:\Users\ragha\Desktop\repowise\repowise-bench\results\bakeoff_2026_08\rung8\rung8_runner.py" `
  --split dev --workers 3 --tag dev-fix2 --arms repowise repowise-search `
  *>&1 | Tee-Object -FilePath $log -Append

"=== track A dev-fix2 exit $LASTEXITCODE ===" | Out-File -FilePath $log -Encoding utf8 -Append
