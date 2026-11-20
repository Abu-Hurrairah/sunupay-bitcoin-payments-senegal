"""Indicative BTC/XOF quote, using Kraken BTC/EUR and BCEAO's EUR/XOF fixed parity.

Never silently fabricate or reuse stale market rates. This is a quoting service,
not a foreign-exchange settlement facility.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal, ROUND_CEILING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EUR_XOF = Decimal('655.957')
SAT_PER_BTC = Decimal('100000000')
KRAKEN_TICKER = 'https://api.kraken.com/0/public/Ticker?pair=XBTEUR'


class RateError(Exception):
    pass


class PriceService:
    def __init__(self):
        self._lock = threading.RLock()
        self._price: Decimal | None = None
        self._at: float = 0.0

    def current(self):
        with self._lock:
            now = time.time()
            if self._price is not None and now - self._at < 60:
                return self._describe(self._price, self._at)
            req = Request(KRAKEN_TICKER, headers={'User-Agent': 'SunuPay/2.0 merchant quotation', 'Accept': 'application/json'})
            try:
                with urlopen(req, timeout=9) as reply:
                    data = json.load(reply)
                if data.get('error'):
                    raise RateError('Kraken could not provide a live exchange rate')
                entries = list(data.get('result', {}).values())
                if len(entries) != 1:
                    raise RateError('Unexpected exchange-rate response')
                price = Decimal(str(entries[0]['c'][0]))
                if not price.is_finite() or price <= 0:
                    raise RateError('Invalid exchange rate')
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                raise RateError('Live BTC/XOF quote unavailable. Check Internet or use satoshis.') from exc
            self._price, self._at = price, time.time()
            return self._describe(self._price, self._at)

    @staticmethod
    def _describe(price, timestamp):
        return {'btc_eur': str(price), 'eur_xof': str(EUR_XOF),
                'btc_xof': str(price * EUR_XOF), 'source': 'Kraken BTC/EUR + BCEAO fixed EUR/XOF parity',
                'quoted_at': int(timestamp), 'indicative': True}

    def quote_xof(self, amount_xof: int):
        if not (100 <= amount_xof <= 100_000_000):
            raise RateError('XOF amount must be between 100 and 100,000,000')
        rate = self.current()
        sats = int((Decimal(amount_xof) * SAT_PER_BTC / Decimal(rate['btc_xof'])).to_integral_value(rounding=ROUND_CEILING))
        if not (1000 <= sats <= 100_000_000):
            raise RateError('This amount converts outside supported payment limits (1,000–100,000,000 sats)')
        return {'amount_xof': amount_xof, 'amount_sats': sats, 'rate': rate}


prices = PriceService()
