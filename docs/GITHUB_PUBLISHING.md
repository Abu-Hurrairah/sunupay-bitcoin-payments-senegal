# Publish SunuPay safely

1. Keep `.env`, local SQLite files, wallet backups, and Bitcoin Core data OUT of this repository. `.gitignore` and `.dockerignore` are already provided.
2. Install Git and GitHub CLI (`winget install --id Git.Git -e`; `winget install --id GitHub.cli -e`) if missing.
3. In PowerShell inside the extracted `SunuPay` folder: `powershell -ExecutionPolicy Bypass -File .\scripts\publish_github.ps1`.
4. Confirm the repository is public and copy its actual URL into Devpost **Try it out**.

The script checks the staged files, asks for explicit public publication approval, and pushes through GitHub CLI (which requires your GitHub account login). It cannot publish without your login.

For manual publishing: `git init -b main; git add .; git commit -m 'SunuPay initial release'; gh auth login; gh repo create sunupay-bitcoin-payments-senegal --public --source . --remote origin --push`.
