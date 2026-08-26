# Index ONE cell-B arm tree, with the same flags cell A's rw-full tree carries
# (`.repowise/config.yaml` there reads `max_file_pages: 0`, `embedder: openai`),
# so the treatment shape is comparable across the two cells rather than
# comparable-looking.
#
# Every arm indexes from a CLEAN checkout. The gate-(a) dry run left a 242 MB
# `.repowise/` parse cache on `test-repos/hermes-agent-full`; an arm inheriting
# another arm's cache is unstated shared state, and this workstream has already
# paid for one round of re-measurement caused by exactly that. The worktrees are
# fresh `git worktree add` checkouts and were verified to carry no `.repowise/`
# before this ran.
#
# Usage: powershell -File index_hermes_arm.ps1 -Tree C:\...\se-rw-full-hermes

param([Parameter(Mandatory=$true)][string]$Tree)

$ErrorActionPreference = "Stop"

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

$exe = "C:\Users\ragha\Desktop\repowise\.venv\Scripts\repowise.exe"
$t0 = Get-Date
Push-Location $Tree
& $exe init --embedder openai --max-file-pages 0 --no-workspace --no-editor-setup --yes
$rc = $LASTEXITCODE
Pop-Location
Write-Host "INIT_RC=$rc ELAPSED_MIN=$([math]::Round(((Get-Date)-$t0).TotalMinutes,1))"
exit $rc
