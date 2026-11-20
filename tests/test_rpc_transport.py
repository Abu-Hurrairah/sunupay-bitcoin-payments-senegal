"""RPC HTTP/wallet URL, cookie auth, and Decimal decoding tests.
This local fake HTTP endpoint is used only by pytest, never at runtime.
"""
import base64
import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.bitcoin import BitcoinError, CoreRPC, btc_to_sats


class RPCHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw=self.rfile.read(int(self.headers['Content-Length']))
        request=json.loads(raw)
        self.server.received=(self.path,self.headers.get('Authorization'),request)
        result={'chain':'regtest','blocks':101} if request['method']=='getblockchaininfo' else 0.00012345
        if request['method']=='badmethod':
            self.send_response(500)
            obj={'result':None,'error':{'code':-32601,'message':'Method not found'},'id':'sunupay'}
        else:
            self.send_response(200)
            obj={'result':result,'error':None,'id':'sunupay'}
        data=json.dumps(obj).encode()
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self,*args):pass


@pytest.fixture
def rpc_server(tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),RPCHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    cookie=tmp_path/'.cookie'
    cookie.write_text('__cookie__:localauthtoken')
    try:
        yield CoreRPC(host=f'http://127.0.0.1:{server.server_port}',cookie=str(cookie)),server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rpc_cookie_and_regtest_network(rpc_server):
    rpc,server=rpc_server
    assert rpc.verify_regtest()==101
    path,authorization,request=server.received
    assert path=='/'
    assert authorization=='Basic '+base64.b64encode(b'__cookie__:localauthtoken').decode()
    assert request['method']=='getblockchaininfo'


def test_rpc_wallet_routing_and_precision(rpc_server):
    rpc,server=rpc_server
    result=rpc.call('getbalance',wallet='sunupay_customer_123')
    assert isinstance(result,Decimal)
    assert btc_to_sats(result)==12345
    assert server.received[0]=='/wallet/sunupay_customer_123'


def test_rpc_reports_node_errors(rpc_server):
    rpc,_=rpc_server
    with pytest.raises(BitcoinError,match='Method not found'):
        rpc.call('badmethod')
