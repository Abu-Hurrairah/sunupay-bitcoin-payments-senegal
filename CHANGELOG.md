# SunuPay — Senegal merchant update

## Added

- XOF currency support with real Kraken BTC/EUR quotes and the official EUR/XOF peg (no hardcoded pretend BTC rates)
- Rate snapshot stored with each local-currency payment request
- French and English merchant checkout, auth and main navigation
- French and English public checkout with local XOF amount and Bitcoin equivalent
- Optional **real LNbits** Lightning invoice issuance and paid-status verification (off until invoice-only credentials are supplied)
- Printable payment receipts **only for verified paid requests**
- Business name, city and preferred language, with migrations for existing merchants
- 14-day payment analytics and method breakdown; no fictional sample sales
- Additional XOF, receipt, profile and provider status tests
- New security and setup documentation, Devpost demo script and Docker `.env.example`

## Kept unchanged

- Existing Bitcoin Core regtest wallet/payment/mining workflow and isolation
- Existing SQLite account and wallet volumes after normal Docker rebuild
- Docker Compose local port 127.0.0.1:8000; no public server exposed by default

## Not connected / not claimed

- No mainnet on-chain BTC wallet; only valueless Bitcoin Core regtest
- No CFA fiat custody, mobile-money account, cashout, settlement or guaranteed exchange rate
- No automatically provisioned Lightning wallet; LNbits credentials must be supplied
- No full Wolof translation (French and English are provided)
- No production compliance, multi-merchant Lightning custody, or public deployment validation

## Run after replacing the old code

```powershell
docker compose up -d --build
docker compose ps
```

Hard-refresh the browser with `Ctrl+F5`. **Do not run `docker compose down -v`.**

## FR/EN language consistency update — 20 November 2026
- Fixed mixed-language merchant UI: sidebar sections, breadcrumb, country headings, metric captions, invoice form helper labels, analytics captions, and account/footer labels now translate with the selected locale.
- Localized sign-in marketing text, form placeholders, safety notice, browser title, and accessible control labels.
- Fixed the public payment page to translate its country label, loading message, unavailable-status warning, page title, and image accessibility label.
- Added real FR/EN language switching on confirmed-payment receipts, including printable text and regulatory/test-network warnings.
- Added localization regression tests to ensure FR and EN dictionaries have the same keys and all referenced labels are defined.
- Payment processing, persisted merchant accounts, wallet volumes, and Lightning provider configuration are unchanged.
