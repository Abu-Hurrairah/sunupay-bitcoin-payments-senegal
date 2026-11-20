"""Contract tests for XOF invoice quoting, Lightning adapter, receipts and DB migration.

Third-party HTTP services are patched at the boundary. Live credentials are NOT
shipped, and no external Lightning payment is presented as verified in tests.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app import db, main
from app.rates import PriceService, RateError
from app.lightning import LNbits, LightningError
from tests.test_app import CoreStub


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_FILE', tmp_path / 'merchant.sqlite3')
    monkeypatch.setattr(main, 'rpc', CoreStub())
    monkeypatch.setattr(main.lnbits, 'url', '')
    monkeypatch.setattr(main.lnbits, 'key', '')
    with TestClient(main.app) as c:
        assert c.post('/api/auth/register', json={
            'name': 'Awa Diop', 'email': 'awa@example.sn', 'password': 'verylongpassword#1'
        }).status_code == 201
        yield c


def test_profile_migration_and_receipt_security(client):
    profile = client.put('/api/me/profile', json={
        'business_name': 'Marché des Almadies', 'city': 'Dakar', 'language': 'fr'
    })
    assert profile.status_code == 200, profile.text
    assert profile.json()['user']['business_name'] == 'Marché des Almadies'
    r = client.post('/api/invoices', json={'title': 'Pain et lait', 'currency': 'SATS', 'amount_sats': 18000})
    inv = r.json()
    assert r.status_code == 201
    assert client.get('/receipt/'+inv['public_token']).status_code == 409
    assert client.post('/api/lab/fund').status_code == 200
    assert client.post('/api/lab/pay/'+str(inv['id'])).status_code == 200
    assert client.post('/api/lab/mine').status_code == 200
    receipt = client.get('/receipt/'+inv['public_token'])
    assert receipt.status_code == 200
    assert 'Marché des Almadies' in receipt.text
    assert '18,000 sats' in receipt.text
    assert 'Bitcoin Core · regtest' in receipt.text
    assert "data-i18n='receiptTitle'" in receipt.text
    assert 'receipt-lang' in receipt.text
    assert client.get('/api/analytics').json()['paid_count'] == 1


def test_xof_quote_locked_by_server(client, monkeypatch):
    from app import rates
    def quote(value):
        assert value == 2500
        return {'amount_xof':value, 'amount_sats':7111, 'rate':{'btc_eur':'54000','quoted_at':1750000000,'btc_xof':'35421678'}}
    monkeypatch.setattr(main.prices, 'quote_xof', quote)
    r=client.post('/api/invoices', json={
        'title':'Fruits locaux', 'method':'onchain', 'currency':'XOF', 'amount_xof':2500
    })
    assert r.status_code==201,r.text
    inv=r.json()
    assert inv['amount_xof']==2500
    assert inv['amount_sats']==7111
    assert inv['rate_btc_eur']=='54000'
    assert client.get('/api/public/invoices/'+inv['public_token']).json()['amount_xof']==2500
    assert client.post('/api/invoices',json={
        'title':'Mauvaise conversion','currency':'XOF','amount_xof':2500,'amount_sats':1
    }).status_code==422
    assert client.get('/api/analytics').json()['total_paid_xof']==0


def test_rate_error_has_no_fabricated_fallback(client, monkeypatch):
    def offline(_):raise RateError('No current quote available')
    monkeypatch.setattr(main.prices, 'quote_xof', offline)
    r=client.post('/api/invoices',json={'title':'Local order', 'currency':'XOF','amount_xof':2500})
    assert r.status_code==503
    assert client.get('/api/invoices').json()['invoices']==[]


def test_real_lightning_provider_contract_mocked(client, monkeypatch):
    # HTTP boundary stub only for tests; production uses LNbits HTTPS API.
    monkeypatch.setattr(main.lnbits,'url','https://example.lnbits.com')
    monkeypatch.setattr(main.lnbits,'key','invoice-only-test-secret')
    is_paid={'value':False}
    observed=[]
    def request(path,payload=None):
        observed.append((path,payload))
        if payload is not None:
            assert payload['out'] is False
            return {'checking_id':'ab'*32,'payment_request':'lnbc10u1pinternaltest'}
        return {'paid':is_paid['value']}
    monkeypatch.setattr(main.lnbits,'request',request)
    r=client.post('/api/invoices',json={'title':'Taxi fare','currency':'SATS','amount_sats':10000,'method':'lightning'})
    assert r.status_code==201,r.text
    inv=r.json()
    assert inv['method']=='lightning'
    assert inv['address'] is None
    assert inv['status']=='unpaid'
    assert client.get('/api/public/invoices/'+inv['public_token']).json()['lightning_invoice'].startswith('lnbc')
    assert client.post('/api/lab/pay/'+str(inv['id'])).status_code==409
    assert client.get('/receipt/'+inv['public_token']).status_code==409
    is_paid['value']=True
    detail=client.get('/api/invoices/'+str(inv['id'])).json()
    assert detail['status']=='paid'
    assert client.get('/receipt/'+inv['public_token']).status_code==200
    assert client.get('/api/analytics').json()['by_method']['lightning']==1
    assert observed[-1][0].endswith('ab'*32)


def test_lightning_unconfigured_or_disconnected_fails_closed(client, monkeypatch):
    r=client.post('/api/invoices',json={'title':'Not allowed','method':'lightning','currency':'SATS','amount_sats':10000})
    assert r.status_code==409
    monkeypatch.setattr(main.lnbits,'url','https://example.lnbits.com')
    monkeypatch.setattr(main.lnbits,'key','invoice-only-secret')
    monkeypatch.setattr(main.lnbits,'create_invoice',lambda amount,memo:{'checking_id':'11'*32,'payment_request':'lnbc1...test'})
    monkeypatch.setattr(main.lnbits,'is_paid',lambda reference:False)
    inv=client.post('/api/invoices',json={'title':'Maybe disconnected','method':'lightning','currency':'SATS','amount_sats':5000}).json()
    def fail(_):raise LightningError('Provider offline')
    monkeypatch.setattr(main.lnbits,'is_paid',fail)
    assert client.get('/api/invoices/'+str(inv['id'])).status_code==503
    assert client.get('/api/invoices').json()['invoices'][0]['status']=='unavailable'
    assert client.get('/receipt/'+inv['public_token']).status_code==503


def test_rate_service_decimal_ceil_and_cache(monkeypatch):
    service=PriceService()
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b''
    class Live:
        def __enter__(self):return self
        def __exit__(self,*args):return False
    calls=[]
    def stub(request,timeout=0):
        calls.append(1)
        class Value:
            def __enter__(self):return self
            def __exit__(self,*args):return False
        return Value()
    # json.load() normally reads file. Patch it at HTTP boundary.
    from app import rates
    monkeypatch.setattr(rates,'urlopen',stub)
    monkeypatch.setattr(rates.json,'load',lambda reply:{'error':[],'result':{'XXBTZEUR':{'c':['60000.00000','1']}}})
    quoted=service.quote_xof(2500)
    assert quoted['amount_sats']>0
    assert quoted['rate']['eur_xof']=='655.957'
    assert len(calls)==1
    service.quote_xof(5000)
    assert len(calls)==1
