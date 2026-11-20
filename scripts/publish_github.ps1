# Run inside extracted SunuPay. Makes this project a PUBLIC GitHub repository.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($cmd in @('git', 'gh')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "$cmd not found. Install Git and GitHub CLI first; see docs/GITHUB_PUBLISHING.md"
    }
}
Write-Host 'This will publish SunuPay source code PUBLICLY on GitHub.' -ForegroundColor Yellow
Write-Host 'Your .env, SQLite databases, private keys, and wallet volumes must remain private.'
if (Test-Path '.env') { Write-Host '.env exists locally; it is excluded by .gitignore.' -ForegroundColor Yellow }
if (-not (Test-Path '.git')) { git init -b main; if ($LASTEXITCODE -ne 0) { throw 'git init failed' } }
git add .
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
$staged = @(git diff --cached --name-only)
$unsafe = @($staged | Where-Object { $_ -match '(^|/)(\.env|\.env\.local|\.bitcoin|bitcoin_data|app_data)(/|$)' -or $_ -match '\.(sqlite3?|db|pem|key)$' })
if ($unsafe.Count -gt 0) { throw "Unsafe files staged: $($unsafe -join ', ')" }
Write-Host 'Staged files:'; $staged | ForEach-Object { Write-Host "  $_" }
$confirmation = Read-Host 'Type PUBLISH to confirm public repository creation'
if ($confirmation -ne 'PUBLISH') { Write-Host 'Cancelled without publishing.'; exit 0 }
gh auth status
if ($LASTEXITCODE -ne 0) { gh auth login; if ($LASTEXITCODE -ne 0) { throw 'GitHub login failed' } }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  git commit -m 'SunuPay merchant payment MVP for Dakar Bitcoin Hack'
  if ($LASTEXITCODE -ne 0) { throw 'git commit failed; configure git user.name and user.email' }
}
$existing = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $existing) {
  Write-Host "Existing origin: $existing"
  git push -u origin HEAD
  if ($LASTEXITCODE -ne 0) { throw 'Push failed' }
} else {
  gh repo create sunupay-bitcoin-payments-senegal --public --source . --remote origin --push
  if ($LASTEXITCODE -ne 0) { throw 'Repository create or push failed' }
}
Write-Host 'GitHub public source published. Paste the actual repository URL into Devpost.' -ForegroundColor Green
