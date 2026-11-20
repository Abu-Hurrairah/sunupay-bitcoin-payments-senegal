"""SunuPay: working Bitcoin Core regtest merchant payments and isolated payer wallets."""
from __future__ import annotations

import hashlib
import hmac
import io
import html
import datetime
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import qrcode
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from . import db
from .bitcoin import BitcoinError, CoreRPC, btc_to_sats, sats_to_btc
from .rates import RateError, prices, EUR_XOF
from .lightning import LightningError, lnbits

ROOT = Path(__file__).parent
rpc = CoreRPC()
payment_lock = threading.RLock()
COOKIE_NAME = "sunupay_session"
SEVEN_DAYS = 7 * 24 * 3600


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    yield


app = FastAPI(title="SunuPay API", version="2.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            # Browser Origin protection in addition to SameSite cookies.
            expected = str(request.base_url).rstrip("/")
            if origin != expected:
                return JSONResponse(status_code=403, content={"detail": "Cross-origin request denied"})
        elif request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse(status_code=403, content={"detail": "Cross-site request denied"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return resp


@app.exception_handler(BitcoinError)
async def bitcoin_error_handler(request: Request, exc: BitcoinError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(RateError)
async def rates_error_handler(request: Request, exc: RateError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(LightningError)
async def lightning_error_handler(request: Request, exc: LightningError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


class Register(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class Login(BaseModel):
    email: EmailStr
    password: str


class CreateInvoice(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    # For XOF invoices the server retrieves a fresh quote itself. Clients can
    # never choose the exchange rate or amount of satoshis billed.
    currency: str = Field(default='SATS', pattern='^(XOF|SATS)$')
    amount_xof: int | None = Field(default=None, ge=100, le=100_000_000)
    amount_sats: int | None = Field(default=None, ge=1000, le=100_000_000)
    method: str = Field(default='onchain', pattern='^(onchain|lightning)$')


class MerchantProfile(BaseModel):
    business_name: str = Field(min_length=2, max_length=100)
    city: str = Field(default='Dakar', min_length=2, max_length=80)
    language: str = Field(default='fr', pattern='^(fr|en)$')


class SendBitcoin(BaseModel):
    address: str = Field(min_length=14, max_length=100)
    amount_sats: int = Field(ge=1000, le=100_000_000)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 390_000)
    return salt.hex() + ":" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hashed = stored.split(":")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 390_000)
        return hmac.compare_digest(digest, bytes.fromhex(hashed))
    except (ValueError, TypeError):
        return False


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def require_user(token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    if not token or len(token) != 64 or not re.fullmatch("[a-zA-Z0-9_-]+", token):
        raise HTTPException(401, "Please sign in")
    with db.connection() as sql:
        row = sql.execute("""
            SELECT users.* FROM sessions JOIN users ON sessions.user_id=users.id
            WHERE sessions.hash=? AND sessions.expires_at>?
        """, (session_hash(token), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "Session expired; please sign in")
    return dict(row)


def set_login_cookie(response: Response, user_id: int):
    token = secrets.token_urlsafe(48)
    expires = int(time.time()) + SEVEN_DAYS
    with db.connection() as sql:
        sql.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        sql.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES(?,?,?)",
                    (session_hash(token), user_id, expires))
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                        max_age=SEVEN_DAYS, path="/")


def public_user(row):
    return {"id": row["id"], "name": row["name"], "email": row["email"],
            "business_name": row["business_name"], "city": row["city"],
            "language": row["language"]}


def ensure_wallets(user):
    rpc.verify_regtest()
    rpc.ensure_wallet(user["merchant_wallet"])
    rpc.ensure_wallet(user["customer_wallet"])


def invoice_status(invoice, user_wallet: str, tolerate_outage=False):
    method = invoice['method']
    if method == 'lightning':
        if not lnbits.enabled:
            paid = None
        else:
            try:
                paid = lnbits.is_paid(invoice['lightning_checking_id'])
            except LightningError:
                if not tolerate_outage:
                    raise
                paid = None
        state = ('paid' if paid is True else
                 'unavailable' if paid is None else
                 'expired' if invoice['expires_at'] and int(time.time()) > invoice['expires_at'] else
                 'unpaid')
        received = confirmed = invoice['amount_sats'] if paid else 0
    else:
        received = rpc.received_sats(user_wallet, invoice['address'], 0)
        confirmed = rpc.received_sats(user_wallet, invoice['address'], 1)
        target = invoice['amount_sats']
        state = ('paid' if confirmed >= target else 'confirming' if received >= target
                 else 'partial' if received > 0 else 'unpaid')
    paid_at = invoice['paid_at']
    if state == 'paid' and not paid_at:
        # Store first *observed* payment date. Don't invent a settlement time.
        observed = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with db.connection() as sql:
            sql.execute('UPDATE invoices SET paid_at=? WHERE id=? AND paid_at IS NULL', (observed, invoice['id']))
        paid_at = observed
    return {
        'id': invoice['id'], 'public_token': invoice['public_token'],
        'title': invoice['title'], 'amount_sats': invoice['amount_sats'],
        'received_sats': received, 'confirmed_sats': confirmed,
        'status': state, 'address': invoice['address'] if method == 'onchain' else None, 'method': method,
        'lightning_invoice': invoice['lightning_invoice'],
        'amount_xof': invoice['amount_xof'],
        'rate_btc_eur': invoice['rate_btc_eur'], 'rate_at': invoice['rate_at'],
        'expires_at': invoice['expires_at'], 'paid_at': paid_at,
        'lab_txid': invoice['lab_txid'], 'created_at': invoice['created_at'],
    }


def fetch_invoice(invoice_id: int, user):
    with db.connection() as sql:
        row = sql.execute("SELECT * FROM invoices WHERE id=? AND user_id=?", (invoice_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Invoice not found")
    return row


@app.get("/api/health")
def health():
    try:
        height = rpc.verify_regtest()
        return {"ok": True, "network": "regtest", "blocks": height}
    except BitcoinError:
        return JSONResponse(status_code=503, content={"ok": False, "network": "regtest"})


@app.post("/api/auth/register", status_code=201)
def register(payload: Register, response: Response):
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Please enter your name")
    # Verify node before creating an account. On regtest, no real funds can be spent.
    rpc.verify_regtest()
    with db.connection() as sql:
        existing = sql.execute("SELECT id FROM users WHERE email=?", (payload.email.lower(),)).fetchone()
        if existing:
            raise HTTPException(409, "An account with this email already exists")
        try:
            unique = secrets.token_hex(12)
            cursor = sql.execute("INSERT INTO users(name,email,password_hash,merchant_wallet,customer_wallet) "
                                 "VALUES(?,?,?,?,?)",
                                 (name, payload.email.lower(), hash_password(payload.password),
                                  f"sunupay_merchant_{unique}", f"sunupay_customer_{unique}"))
            uid = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account with this email already exists")
    with db.connection() as sql:
        user = sql.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    # On a transient node error, account remains and user may sign in to retry.
    ensure_wallets(user)
    set_login_cookie(response, uid)
    return {"user": public_user(user)}


@app.post("/api/auth/login")
def login(payload: Login, response: Response):
    with db.connection() as sql:
        user = sql.execute("SELECT * FROM users WHERE email=?", (payload.email.lower(),)).fetchone()
    # Run expensive password check also on unknown accounts to reduce timing oracles.
    if not user or not check_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    ensure_wallets(user)
    set_login_cookie(response, user["id"])
    return {"user": public_user(user)}


@app.post("/api/auth/logout")
def logout(response: Response, token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None):
    if token:
        with db.connection() as sql:
            sql.execute("DELETE FROM sessions WHERE hash=?", (session_hash(token),))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(require_user)):
    return {"user": public_user(user)}


@app.put('/api/me/profile')
def update_profile(payload: MerchantProfile, user=Depends(require_user)):
    name, city = payload.business_name.strip(), payload.city.strip()
    if len(name) < 2 or len(city) < 2:
        raise HTTPException(422, 'Enter a valid business name and city')
    with db.connection() as sql:
        sql.execute('UPDATE users SET business_name=?,city=?,language=? WHERE id=?',
                    (name, city, payload.language, user['id']))
        row = sql.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone()
    return {'user': public_user(row)}


@app.get('/api/features')
def features():
    return {'lightning_configured': lnbits.enabled, 'onchain_network': 'regtest',
            'lightning_network': 'Configured Lightning provider (separate from regtest)' if lnbits.enabled else None,
            'xof_market_rates': True, 'price_source': 'Kraken BTC/EUR + BCEAO fixed parity',
            'fiat_settlement': False}


@app.get('/api/rates')
def market_rates():
    return prices.current()


@app.get('/api/rates/quote')
def quote_xof(amount_xof: int):
    return prices.quote_xof(amount_xof)


@app.get("/api/overview")
def overview(user=Depends(require_user)):
    ensure_wallets(user)
    with db.connection() as sql:
        invoices = sql.execute("SELECT * FROM invoices WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
    items = [invoice_status(row, user["merchant_wallet"], tolerate_outage=True) for row in invoices]
    return {
        "user": public_user(user), "network": "regtest", "blocks": rpc.verify_regtest(),
        "merchant_balance_sats": rpc.get_balance_sats(user["merchant_wallet"]),
        "customer_balance_sats": rpc.get_balance_sats(user["customer_wallet"]),
        "invoice_count": len(items), "paid_count": sum(x["status"] == "paid" for x in items),
        "collected_sats": sum(x["confirmed_sats"] for x in items),
        "invoices": items[:8], "collected_xof": sum(x['amount_xof'] or 0 for x in items if x['status']=='paid'),
    }


@app.get("/api/invoices")
def list_invoices(user=Depends(require_user)):
    ensure_wallets(user)
    with db.connection() as sql:
        rows = sql.execute("SELECT * FROM invoices WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
    return {"invoices": [invoice_status(r, user["merchant_wallet"], tolerate_outage=True) for r in rows]}


@app.post('/api/invoices', status_code=201)
def create_invoice(payload: CreateInvoice, user=Depends(require_user)):
    title = payload.title.strip()
    if len(title) < 2:
        raise HTTPException(422, 'Invoice description is required')
    if payload.currency == 'XOF':
        if payload.amount_xof is None or payload.amount_sats is not None:
            raise HTTPException(422, 'Provide amount_xof only for a CFA franc invoice')
        quote = prices.quote_xof(payload.amount_xof)
        amount_sats = quote['amount_sats']
        rate_btc_eur = quote['rate']['btc_eur']
        quoted_at = quote['rate']['quoted_at']
        amount_xof = payload.amount_xof
    else:
        if payload.amount_sats is None or payload.amount_xof is not None:
            raise HTTPException(422, 'Provide amount_sats only for a satoshi invoice')
        amount_sats = payload.amount_sats
        rate_btc_eur = quoted_at = amount_xof = None

    address = None
    checking_id = bolt11 = expires_at = None
    if payload.method == 'onchain':
        ensure_wallets(user)
        address = rpc.new_address(user['merchant_wallet'], 'Invoice')
    else:
        if not lnbits.enabled:
            raise HTTPException(409, 'Lightning unavailable: configure LNbits invoice credentials first')
        response = lnbits.create_invoice(amount_sats, title)
        checking_id, bolt11 = response['checking_id'], response['payment_request']
        address = 'lightning:' + checking_id
        expires_at = int(time.time()) + 3600
    token = secrets.token_urlsafe(24)
    with db.connection() as sql:
        cursor = sql.execute("""INSERT INTO invoices(user_id,public_token,title,amount_sats,address,method,
             amount_xof,rate_btc_eur,rate_at,lightning_checking_id,lightning_invoice,expires_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
             (user['id'], token, title, amount_sats, address, payload.method,
              amount_xof, rate_btc_eur, quoted_at, checking_id, bolt11, expires_at))
        row = sql.execute('SELECT * FROM invoices WHERE id=?', (cursor.lastrowid,)).fetchone()
    return invoice_status(row, user['merchant_wallet'])


@app.get("/api/invoices/export.csv")
def export_invoices(user=Depends(require_user)):
    import csv
    ensure_wallets(user)
    with db.connection() as sql:
        rows = sql.execute("SELECT * FROM invoices WHERE user_id=? ORDER BY id DESC LIMIT 1000", (user["id"],)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['invoice_id','description','method','amount_xof','amount_sats','confirmed_sats','status','address','paid_at','created_at'])
    for row in rows:
        item = invoice_status(row, user["merchant_wallet"], tolerate_outage=True)
        # Prevent CSV spreadsheet formula injection from user-supplied titles.
        safe_title = ("'" + item["title"] if item["title"].startswith(("=", "+", "-", "@", "\t", "\r")) else item["title"])
        writer.writerow([item['id'], safe_title, item['method'], item['amount_xof'], item['amount_sats'],
                         item['confirmed_sats'], item['status'], item['address'], item['paid_at'], item['created_at']])
    return Response(content=output.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="sunupay-invoices.csv"'
    })


@app.get("/api/invoices/{invoice_id}")
def detail(invoice_id: int, user=Depends(require_user)):
    ensure_wallets(user)
    return invoice_status(fetch_invoice(invoice_id, user), user['merchant_wallet'])


def public_invoice(token: str):
    with db.connection() as sql:
        row = sql.execute("""SELECT invoices.*, users.merchant_wallet FROM invoices
                             JOIN users ON users.id=invoices.user_id WHERE invoices.public_token=?""",
                          (token,)).fetchone()
    if not row:
        raise HTTPException(404, "Payment request not found")
    return row


def bitcoin_uri(row):
    # Regtest addresses use the Bitcoin BIP-21 URI scheme, but are not mainnet assets.
    if row['method'] == 'lightning':
        return 'lightning:' + row['lightning_invoice'].upper()
    return f"bitcoin:{row['address']}?amount={Decimal(row['amount_sats']) / Decimal('100000000'):.8f}"


@app.get("/api/public/invoices/{token}")
def view_public_invoice(token: str):
    row = public_invoice(token)
    status = invoice_status(row, row["merchant_wallet"])
    # Do not reveal merchant email, names, internal wallet names or lab transactions.
    return {k: status[k] for k in ('title','amount_sats','amount_xof','received_sats',
            'confirmed_sats','status','address','created_at','method','lightning_invoice',
            'rate_at','expires_at','paid_at')} | {
            'payment_uri': bitcoin_uri(row), 'bitcoin_uri': bitcoin_uri(row) if row['method']=='onchain' else None, 'network': 'regtest' if row['method']=='onchain' else 'lightning provider'}


@app.get("/api/public/invoices/{token}/qr")
def get_qr(token: str):
    row = public_invoice(token)
    qr = qrcode.QRCode(version=None, box_size=8, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(bitcoin_uri(row))
    qr.make(fit=True)
    image = qr.make_image(fill_color="#183F35", back_color="#FFFFFF")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return StreamingResponse(stream, media_type="image/png")


@app.get('/api/analytics')
def analytics(user=Depends(require_user)):
    ensure_wallets(user)
    with db.connection() as sql:
        rows = sql.execute('SELECT * FROM invoices WHERE user_id=? ORDER BY id DESC LIMIT 1000',
                           (user['id'],)).fetchall()
    items = [invoice_status(r, user['merchant_wallet'], tolerate_outage=True) for r in rows]
    paid = [x for x in items if x['status']=='paid']
    today = datetime.datetime.now(datetime.timezone.utc).date()
    days = []
    for offset in range(13, -1, -1):
        d = today - datetime.timedelta(days=offset)
        entries = [x for x in paid if x['paid_at'] and x['paid_at'][:10] == d.isoformat()]
        days.append({'date': d.isoformat(), 'total_sats': sum(x['amount_sats'] for x in entries),
                     'total_xof': sum(x['amount_xof'] or 0 for x in entries), 'count': len(entries)})
    return {
        'days': days,
        'total_paid_sats': sum(x['amount_sats'] for x in paid),
        'total_paid_xof': sum(x['amount_xof'] or 0 for x in paid),
        'paid_count': len(paid),
        'unpaid_count': sum(x['status'] in ('unpaid','confirming','partial') for x in items),
        'unavailable_count': sum(x['status']=='unavailable' for x in items),
        'by_method': {method: sum(x['status']=='paid' and x['method']==method for x in items)
                      for method in ('onchain','lightning')},
        'reporting_note': 'Paid date is first observed by SunuPay; older paid invoices are dated when first checked.'
    }


@app.get('/receipt/{token}')
def receipt(token: str):
    row = public_invoice(token)
    item = invoice_status(row, row['merchant_wallet'])
    if item['status'] != 'paid':
        raise HTTPException(409, 'Receipt available only after confirmed payment')
    with db.connection() as sql:
        user = sql.execute('SELECT business_name,city FROM users WHERE id=?', (row['user_id'],)).fetchone()
    business = (user['business_name'] or 'SunuPay Merchant') if user else 'SunuPay Merchant'
    city = user['city'] if user else ''
    e = lambda value: html.escape(str(value or ''), quote=True)
    amount = f"{item['amount_sats']:,} sats"
    if item['amount_xof']:
        amount = f"{item['amount_xof']:,} XOF / {amount}"
    method_text = 'Bitcoin Core · regtest' if item['method'] == 'onchain' else 'Lightning · LNbits'
    network_note_key = 'testNote' if item['method'] == 'onchain' else 'lightningNote'
    receipt_html = f"""<!doctype html><html lang='fr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Reçu SunuPay #{item['id']}</title><script src='/static/receipt.js?v=4' defer></script>
<style>
body{{font:16px Arial,sans-serif;color:#18392f;background:#f6f7f4;padding:30px}}
main{{max-width:620px;margin:auto;background:white;padding:38px;border:1px solid #dce5dd;border-radius:16px}}
h1{{font-size:27px}}.muted{{color:#65776b}}.row{{padding:12px 0;border-bottom:1px solid #e8ebe7;display:flex;gap:15px;justify-content:space-between}}
strong{{text-align:right;word-break:break-word}}.paid{{color:#167348;font-weight:700}}
button{{background:#18392f;color:white;border:0;border-radius:8px;padding:12px 20px;cursor:pointer;margin-top:24px}}
select{{background:white;border:1px solid #dce5dd;border-radius:8px;padding:8px;margin-left:8px}}
.top{{display:flex;justify-content:space-between;align-items:center;gap:12px}}.row span{{min-width:110px}}
@media print{{body{{padding:0;background:white}}main{{border:0;padding:0}}button,select,.language-control{{display:none}}}}
</style></head><body><main>
<div class='top'><p><b>SUNUPAY</b> · <span data-i18n='receiptTitle'>REÇU DE PAIEMENT</span></p>
<label class='language-control'><span data-i18n='language'>Langue</span><select id='receipt-lang' aria-label='Langue'><option value='fr'>FR</option><option value='en'>EN</option></select></label></div>
<h1>{e(business)}</h1><p class='muted'>{e(city)}</p><p class='paid' data-i18n='paid'>✓ Confirmé</p>
<div class='row'><span data-i18n='reference'>Référence</span><strong>SP-{item['id']:06d}</strong></div>
<div class='row'><span data-i18n='item'>Article</span><strong>{e(item['title'])}</strong></div>
<div class='row'><span data-i18n='amount'>Montant</span><strong>{e(amount)}</strong></div>
<div class='row'><span data-i18n='network'>Réseau</span><strong>{e(method_text)}</strong></div>
<div class='row'><span data-i18n='verifiedAt'>Vérifié le</span><strong>{e(item['paid_at'])} UTC</strong></div>
<p class='muted' data-i18n='receiptNote'>Ce reçu correspond à un paiement vérifié par SunuPay.</p>
<p class='muted' data-i18n='{network_note_key}'>Les bitcoins regtest n’ont aucune valeur financière.</p>
<button id='print-receipt' type='button' data-i18n='print'>Imprimer / Enregistrer en PDF</button>
</main></body></html>"""
    return Response(receipt_html, media_type='text/html; charset=utf-8')


@app.get("/api/wallet")
def wallet_data(user=Depends(require_user)):
    ensure_wallets(user)
    txs = rpc.history(user["merchant_wallet"], 30)
    simplified = [
        {"txid": t.get("txid"), "category": t.get("category"),
         "amount_sats": btc_to_sats(t.get("amount", 0)),
         "confirmations": int(t.get("confirmations", 0)),
         "time": int(t.get("time", time.time()))}
        for t in reversed(txs) if t.get("txid") and t.get("category") in ("send", "receive")
    ]
    return {"balance_sats": rpc.get_balance_sats(user["merchant_wallet"]), "transactions": simplified}


@app.post("/api/wallet/receive")
def receive_address(user=Depends(require_user)):
    ensure_wallets(user)
    address = rpc.new_address(user["merchant_wallet"], "Personal wallet receive")
    return {"address": address}


@app.post("/api/wallet/send")
def send_from_merchant(payload: SendBitcoin, user=Depends(require_user)):
    ensure_wallets(user)
    transaction_id = rpc.send_sats(user["merchant_wallet"], payload.address.strip(), payload.amount_sats)
    return {"txid": transaction_id, "status": "broadcast"}


@app.get("/api/lab")
def lab_state(user=Depends(require_user)):
    ensure_wallets(user)
    return {"customer_balance_sats": rpc.get_balance_sats(user["customer_wallet"]),
            "block_height": rpc.verify_regtest()}


@app.post("/api/lab/fund")
def fund_test_wallet(user=Depends(require_user)):
    ensure_wallets(user)
    # Faucet is deliberately available only in private, valueless regtest.
    with payment_lock:
        if rpc.get_balance_sats(user["customer_wallet"]) >= 50_000_000:
            raise HTTPException(400, "Test wallet already has at least 0.5 BTC")
        transaction_id = rpc.fund_customer(user["customer_wallet"], 5_000_000)
    return {"txid": transaction_id, "customer_balance_sats": rpc.get_balance_sats(user["customer_wallet"])}


@app.post("/api/lab/pay/{invoice_id}")
def test_payment(invoice_id: int, user=Depends(require_user)):
    ensure_wallets(user)
    with payment_lock:
        row = fetch_invoice(invoice_id, user)
        current = invoice_status(row, user["merchant_wallet"])
        if row['method'] != 'onchain':
            raise HTTPException(409, 'Transaction lab supports Bitcoin regtest invoices only')
        if current["received_sats"] > 0 or row["lab_txid"]:
            raise HTTPException(409, "Invoice already has an incoming payment")
        txid = rpc.send_sats(user["customer_wallet"], row["address"], row["amount_sats"])
        with db.connection() as sql:
            sql.execute("UPDATE invoices SET lab_txid=? WHERE id=? AND user_id=?",
                        (txid, invoice_id, user["id"]))
    return {"txid": txid, "invoice": invoice_status(fetch_invoice(invoice_id, user), user["merchant_wallet"])}


@app.post("/api/lab/mine")
def confirm_payment(user=Depends(require_user)):
    ensure_wallets(user)
    with payment_lock:
        height = rpc.mine(1)
    return {"block_height": height, "message": "A real regtest block was mined"}




@app.get("/pay/{token}")
def pay_page(token: str):
    if len(token) > 80 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise HTTPException(404, "Payment request not found")
    return FileResponse(ROOT / "static" / "pay.html")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/{path:path}")
def fallback(path: str):
    return RedirectResponse("/")
