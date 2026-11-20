"""Contract tests of app flows with an in-memory stand-in for the Bitcoin RPC boundary.
The shipped runtime ALWAYS talks to genuine Bitcoin Core; no fake node is included.
"""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import db
from app import main
from app.bitcoin import btc_to_sats, sats_to_btc


class CoreStub:
    def __init__(self):
        self.balances={}
        self.addresses={}
        self.incoming={}
        self.confirmed={}
        self.height=102
        self.sequence=0
        self.wallet_txs={}
    def verify_regtest(self):return self.height
    def ensure_wallet(self,wallet):
        self.balances.setdefault(wallet,0)
        self.wallet_txs.setdefault(wallet,[])
    def new_address(self,wallet,label=''):
        self.ensure_wallet(wallet)
        self.sequence+=1
        address='bcrt1q'+str(self.sequence).zfill(38)
        self.addresses[address]=wallet
        return address
    def get_balance_sats(self,wallet):return self.balances.get(wallet,0)
    def received_sats(self,wallet,address,confs):
        return (self.confirmed if confs>=1 else self.incoming).get(address,0)
    def history(self,wallet,count=30):return self.wallet_txs.get(wallet,[])[-count:]
    def send_sats(self,wallet,address,amount):
        assert address in self.addresses
        if self.balances[wallet]<amount:raise main.BitcoinError('Insufficient funds')
        self.balances[wallet]-=amount
        self.incoming[address]=self.incoming.get(address,0)+amount
        dest=self.addresses[address]
        self.balances[dest]+=amount
        self.sequence+=1
        txid=str(self.sequence).zfill(64)
        self.wallet_txs[dest].append({'txid':txid,'category':'receive','amount':str(amount/1e8),'confirmations':0})
        return txid
    def mine(self,n=1):
        self.confirmed.update(self.incoming)
        self.height+=n
        return self.height
    def fund_customer(self,wallet,amount_sats=5_000_000):
        self.ensure_wallet(wallet)
        self.balances[wallet]+=amount_sats
        self.sequence+=1
        return str(self.sequence).zfill(64)


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setattr(db,'DB_FILE',tmp_path/'integration.sqlite')
    monkeypatch.setattr(main,'rpc',CoreStub())
    with TestClient(main.app) as c:
        yield c


def register(client,email='awa@example.com'):
    r=client.post('/api/auth/register',json={'name':'Awa Diop','email':email,'password':'strongpassword123'})
    assert r.status_code==201,r.text
    assert r.json()['user']['email']==email
    return r


def test_btc_amount_conversion():
    assert btc_to_sats('0.00001001')==1001
    assert sats_to_btc(1001)==0.00001001


def test_register_login_and_logout(client):
    register(client)
    assert client.get('/api/me').status_code==200
    assert client.get('/api/overview').json()['invoice_count']==0
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/me').status_code==401
    wrong=client.post('/api/auth/login',json={'email':'awa@example.com','password':'badpassword'})
    assert wrong.status_code==401
    assert client.post('/api/auth/login',json={'email':'awa@example.com','password':'strongpassword123'}).status_code==200


def test_invoice_payment_confirmation_and_export(client):
    register(client)
    r=client.post('/api/invoices',json={'title':'Coffee order 12','amount_sats':15000})
    assert r.status_code==201,r.text
    inv=r.json(); assert inv['status']=='unpaid'
    token=inv['public_token']
    public=client.get('/api/public/invoices/'+token)
    assert public.status_code==200
    assert 'merchant_wallet' not in public.json()
    assert public.json()['bitcoin_uri'].startswith('bitcoin:bcrt1q')
    qr=client.get('/api/public/invoices/'+token+'/qr')
    assert qr.status_code==200
    assert qr.content.startswith(b'\x89PNG')
    assert client.post('/api/lab/fund').status_code==200
    payment=client.post('/api/lab/pay/'+str(inv['id']))
    assert payment.status_code==200,payment.text
    assert payment.json()['invoice']['status']=='confirming'
    assert client.post('/api/lab/pay/'+str(inv['id'])).status_code==409
    assert client.post('/api/lab/mine').status_code==200
    final=client.get('/api/invoices/'+str(inv['id'])).json()
    assert final['status']=='paid'
    assert final['confirmed_sats']==15000
    assert client.get('/api/overview').json()['paid_count']==1
    csvr=client.get('/api/invoices/export.csv')
    rows=list(csv.DictReader(io.StringIO(csvr.text)))
    assert len(rows)==1 and rows[0]['status']=='paid'


def test_separate_users_and_protected_exports(client):
    register(client,'user1@example.com')
    id1=client.post('/api/invoices',json={'title':'User one order','amount_sats':30000}).json()['id']
    assert client.post('/api/auth/logout').status_code==200
    register(client,'user2@example.com')
    assert client.get('/api/invoices').json()['invoices']==[]
    assert client.get('/api/invoices/'+str(id1)).status_code==404
    assert client.post('/api/lab/pay/'+str(id1)).status_code==404


def test_security_validation(client):
    register(client)
    assert client.post('/api/invoices',json={'title':'x','amount_sats':1000}).status_code==422
    assert client.post('/api/invoices',json={'title':'long enough','amount_sats':-10}).status_code==422
    forbidden=client.post('/api/invoices',headers={'Origin':'https://evil.example'},json={'title':'Wrong origin','amount_sats':1000})
    assert forbidden.status_code==403
    assert client.get('/api/invoices/export.csv').headers['x-content-type-options']=='nosniff'


def test_page_assets(client):
    assert client.get('/').status_code==200
    assert 'data-i18n="authHeroTitle"' in client.get('/').text
    assert client.get('/static/app.js').status_code==200
    assert client.get('/pay/any-valid-token').status_code==200
    assert client.get('/api/public/invoices/notfound').status_code==404
