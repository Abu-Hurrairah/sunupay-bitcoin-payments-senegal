# SunuPay Security and Threat Model

## Boundary / risk statement

This application is **regtest-only** and **local development only**. Its coins have no financial value, and Docker Compose binds HTTP to `127.0.0.1`. It is **not** a safe Bitcoin mainnet custodian. User names and email addresses are stored locally; passwords are not stored in plaintext.

## Assets

1. Merchant invoices (descriptions, addresses, amounts and status)
2. Account password hashes and session token hashes
3. Bitcoin Core wallet private keys, including merchant/customer/treasury descriptor wallets
4. Docker volumes containing node and SQLite state

## Threats and defenses

| Threat | Implemented defense | Remaining risk |
| --- | --- | --- |
| Internet attacker probing Bitcoin RPC | Bitcoin only on internal Docker network; no host RPC port; auth uses a local cookie | Shared RPC cookie permits high privilege from the app container; no least-privilege RPC proxy |
| Mainnet funds used accidentally | Every workflow first verifies Bitcoin Core `chain=regtest`; addresses use `bcrt` prefix | Operators must not modify Core config to expose or use real funds |
| Other user reads a merchant invoice | Private API scopes invoice queries by `user_id` | Public checkout token deliberately exposes limited description/amount/address/status |
| Password theft | Salted 390,000-iteration PBKDF2-HMAC-SHA256, no plaintext storage | No account recovery, MFA or login throttling in this local MVP |
| Session theft / cross-site requests | 48-byte random session tokens, server stores SHA-256 only, HttpOnly, SameSite=Strict, Origin check, 7-day expiry | Local HTTP has no TLS; bind only to loopback. `COOKIE_SECURE` must be set behind HTTPS |
| XSS in invoice titles | Escapes user content before writing dynamic HTML; restrictive CSP | Continue security testing before any public service |
| CSV formula injection | Leading spreadsheet formula characters receive quote prefix | All exports need continued spreadsheet-specific review |
| Fee ambiguity / integer rounding | Convert RPC Decimal values to integer satoshis | Network fees affect spendable balance; invoice payment amount is validated separately |
| Blockchain payment false positive | Read received amounts from Bitcoin Core merchant wallet; require ≥1 confirmation for Paid | One confirmation can be reorganized; production needs confirmation policy and reorg handling |
| Data loss / volume deletion | Named Docker volumes survive normal restarts | No backup, migration, disaster recovery, or encrypted storage |

## Operational restrictions

- The application uses the Bitcoin Core RPC **cookie** from a read-only volume mount; it does **not** store RPC passwords in source code.
- The Python service runs as a non-root UID matching the Bitcoin Core image's default `bitcoin` UID so it can read the cookie.
- Bitcoin RPC authentication prevents unauthenticated clients; the private network does not by itself limit authorization once the app is compromised.
- `POST /api/lab/fund` and `/api/lab/mine` are **deliberately privileged regtest-only learning actions**. They must never be enabled on public networks.
- Payment amounts have validation limits; the application does not perform exchange-rate conversion.
- Local SQLite, account metadata, wallet files, and chain data persist until Docker volumes are deleted.
- The public checkout link is a capability URL. Anyone given a valid token can read the invoice details exposed on its payment page.

## Before a public real-value release

Build a noncustodial wallet or a specialized signing service with least privilege, rigorously audited RPC boundaries, secret management, TLS + secure cookies, authentication rate limits and MFA, key backup/recovery, reorg-aware accounting, idempotent payment acceptance, unique payment expiration, address gap / rescan handling, chain monitoring, alerts and a realistic threat-model review. Seek relevant local financial and consumer-protection advice.

## Dakar merchant features and optional Lightning (v2)

- Live XOF conversions use a server-side Kraken public ticker quote, BCEAO's fixed parity, Decimal arithmetic and upward rounding to full satoshis. There is **no fabricated/stale fallback**; conversion failures refuse XOF invoice creation.
- Separate Lightning LNbits integration: HTTPS URL and invoice-only key via environment variables. **No spending/admin key** is needed or accepted by the UI. Secrets are kept out of QR/checkout responses and normal logs. One provider wallet per single-merchant deployment only, since multiple accounts sharing the same LNbits key would commingle funds.
- Payment status uses Bitcoin Core with at least one confirmation or LNbits `paid: true`. Provider errors return unavailable rather than trusted paid. Public printable HTML receipts exist only after payment is verified.
- The verified date is first-seen by the application (UTC), not guaranteed actual settlement time. Quote rates are indicative and do not promise FX settlement or local payouts.
- Public payment links contain unguessable tokens; holders can see item, amount, Bitcoin address or BOLT11 invoice. Do not put names or sensitive details in payment titles. Links must be treated as access-bearing information.
- The app is bound to localhost by default, and **not intended for public Internet or real-custody operation** without deployment hardening, merchant-specific provider isolation, secrets management, TLS, rate limiting, fraud/abuse controls, backups, reconciliation and independent security audit.
