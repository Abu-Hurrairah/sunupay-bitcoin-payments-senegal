# Devpost Submission — SunuPay: Payments for Dakar Merchants

## One-liner

SunuPay gives small merchants in Senegal a simple way to **price in CFA francs, request Bitcoin and issue verified digital receipts**, using Bitcoin Core and optional Lightning.

## Problem

Market stall owners, small shops, taxi drivers and independent workers can benefit from simple digital bookkeeping and shareable payment requests. Many Bitcoin apps force customers to think in satoshis rather than local prices and do not include a merchant receipt or clear confirmation status. We target a focused workflow: merchant creates a sale in XOF, sends a payment link, verifies settlement and records the transaction.

## Solution and build

A bilingual **French/English** responsive merchant platform built with FastAPI, vanilla JS, Bitcoin Core v28 and SQLite. We do not use UI mockups in the running product: creating an on-chain payment request generates a genuine descriptor-wallet address, paying sends a signed Bitcoin Core transaction on a private regtest blockchain and confirming mines a real regtest block. Regtest funds have **no monetary value**.

A merchant can choose XOF pricing: SunuPay gets a public Kraken BTC/EUR quote and applies BCEAO's EUR/XOF peg to calculate sats server-side, storing the exact rate at creation. No silent fallback rate. Customers receive a QR and a public status page. Verified payments unlock a printable HTML receipt and a merchant analytics report.

Lightning invoice reception is implemented against real LNbits HTTPS APIs but **disabled without a separate LNbits wallet and invoice-only API key**. We do not pretend an external Lightning wallet exists until configured. When connected, it can handle real funds, unlike regtest. No Lightning payments are simulated or routed through the on-chain regtest wallet.

## Architecture

Merchant browser (FR/EN) → FastAPI → SQLite (accounts, invoices, profile, receipts)

FastAPI → Bitcoin Core private regtest RPC (wallets, new addresses, signed payments, block mining)

FastAPI → Kraken public BTC/EUR ticker (XOF quote) and optionally → external HTTPS LNbits invoice API

## Why it matters locally

- Local price display in XOF instead of requiring merchant mental conversion to sats
- French user interface with low-bandwidth, no-framework assets
- Merchant proof of payment through verified receipts, not screenshots or unverifiable claims
- Sales history and CSV recordkeeping for small businesses
- A clear path to Lightning invoices for small payments, subject to provider configuration

This is a **technical MVP**, not a deployed financial service or established claim of socioeconomic impact. We have not yet conducted a Dakar merchant user trial.

## 3-minute demo script

**0:00–0:25 · The problem**: Explain a typical Dakar shopkeeper selling food for 2,500 XOF who wants to record a sale and share a payment link without knowing BTC denominations.

**0:25–0:50 · Localized merchant dashboard**: Sign in, show French/English toggle, business profile, XOF reporting and sales records.

**0:50–1:25 · Quote + invoice**: Enter `Pain et café`, `2,500 XOF`; show live Kraken-derived sats conversion, create on-chain regtest request, open QR checkout. If Internet unavailable, explicitly switch to sats and explain no fabricated rate.

**1:25–2:15 · Proof of real Bitcoin**: Fund the customer wallet in the transaction lab, pay the request using Bitcoin Core, show pending confirmation, mine one regtest block, then show Paid. Run `docker compose exec -u bitcoin bitcoin bitcoin-cli -regtest getblockchaininfo`.

**2:15–2:40 · Receipt + analytics**: Open shareable customer page, show the paid receipt, export CSV and explain 14-day chart counts only verified payments.

**2:40–3:00 · Lightning and next steps**: Show the LNbits integration option (disabled until configured). State that testing and mainnet custody are separate from a production rollout. Propose field studies, Wolof localization, merchant settlement and regulatory review.

## Verification & limitations

- Automated tests cover: login, on-chain invoice lifecycle, regtest payment verification, CSV, XOF calculations, rate failure, profile migration, receipt access and LNbits API boundary/paid checks.
- The previous SunuPay release was started successfully on Windows Docker Desktop with Bitcoin healthy; the **new version needs an updated end-to-end run after rebuild**.
- The project itself does **not** process real XOF payments or cash outs, provide a Bitcoin mainnet wallet, or automatically supply a Lightning provider.
- For detailed build steps, keys, constraints and threat model, see README and `docs/SECURITY.md`.

## License

MIT. Repo can be open-sourced; ensure you don't upload `.env` or merchant database/Bitcoin wallet volumes.
