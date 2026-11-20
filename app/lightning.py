"""Real, receive-only LNbits payment API integration.

This optional provider is independent from Bitcoin Core regtest; it is NOT a
regtest Lightning node and can involve real funds on the provider's wallet.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class LightningError(Exception):
    pass


class LNbits:
    def __init__(self):
        self.url = os.environ.get('LNBITS_URL', '').strip().rstrip('/')
        self.key = os.environ.get('LNBITS_INVOICE_KEY', '').strip()

    @property
    def enabled(self):
        return bool(self.url and self.key)

    def request(self, path, payload=None):
        if not self.enabled:
            raise LightningError('Lightning is not configured: set LNBITS_URL and LNBITS_INVOICE_KEY')
        p = urlparse(self.url)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise LightningError('LNbits must use an HTTPS base URL without embedded credentials')
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(self.url + path, data=body, headers={
            'X-Api-Key': self.key, 'Content-Type': 'application/json', 'Accept': 'application/json'
        }, method='POST' if payload is not None else 'GET')
        try:
            with urlopen(request, timeout=12) as reply:
                data = json.load(reply)
        except HTTPError as exc:
            # No response-body text: third-party errors sometimes echo account details.
            raise LightningError(f'LNbits rejected request (HTTP {exc.code}); check invoice-only key and wallet') from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise LightningError('Could not reach LNbits. Check the HTTPS URL, service and network.') from exc
        if not isinstance(data, dict):
            raise LightningError('Unexpected LNbits response')
        return data

    def create_invoice(self, sats, memo):
        data = self.request('/api/v1/payments', {'out': False, 'amount': sats, 'memo': memo, 'expiry': 3600})
        checking_id = data.get('checking_id') or data.get('payment_hash')
        payreq = data.get('payment_request')
        if not isinstance(checking_id, str) or not isinstance(payreq, str) or not payreq.lower().startswith(('lnbc', 'lntb', 'lnbcrt')):
            raise LightningError('LNbits did not return a valid BOLT11 invoice')
        return {'checking_id': checking_id, 'payment_request': payreq}

    def is_paid(self, checking_id):
        if not checking_id or len(checking_id) > 256:
            raise LightningError('Invalid Lightning payment reference')
        data = self.request('/api/v1/payments/' + quote(checking_id, safe=''))
        if not isinstance(data.get('paid'), bool):
            raise LightningError('LNbits did not return a verified payment status')
        return data['paid']


lnbits = LNbits()
