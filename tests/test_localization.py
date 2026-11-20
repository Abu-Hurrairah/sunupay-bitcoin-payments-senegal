"""Protect bilingual FR/EN labels from regression to mixed hard-coded UI text."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'app' / 'static'


def _keys(source, first, end):
    block = source.split(first, 1)[1].split(end, 1)[0]
    return set(re.findall(r"\b([A-Za-z][\w]*):'", block))


def test_merchant_has_matching_translation_keys():
    js = (ROOT/'app.js').read_text(encoding='utf-8')
    fr = _keys(js, '  fr: {', '  en: {')
    en = _keys(js, '  en: {', '\n};')
    assert fr == en, (fr - en, en - fr)
    static = (ROOT/'index.html').read_text(encoding='utf-8')
    html_keys = set(re.findall(r'data-i18n(?:-placeholder|-aria|-title)?="([\w]+)"', static))
    dynamic_keys = set(re.findall(r"\bt\('([A-Za-z][\w]*)'\)", js))
    assert not (html_keys | dynamic_keys) - fr, (html_keys | dynamic_keys) - fr
    assert 'data-i18n="breadcrumb"' in static
    assert "heading(t('countryHeading')" in js
    assert "${t('xofCountry')}" in js
    assert "${t('countryMarket')}" in js
    assert "${t('payCount')}" in js


def test_checkout_has_matching_translations():
    js = (ROOT/'pay.js').read_text(encoding='utf-8')
    fr = _keys(js, 'fr:{', '},\nen:{')
    en = _keys(js, 'en:{', '}\n};')
    assert fr == en, (fr - en, en - fr)
    keys = set(re.findall(r"\bt\('([A-Za-z][\w]*)'\)", js))
    assert not keys-fr, keys-fr
    html = (ROOT/'pay.html').read_text(encoding='utf-8')
    assert 'checkout-country' in html
    assert 'checkout-loading' in html
    assert 'unavailableNote' in js


def test_receipt_has_matching_translations():
    js = (ROOT/'receipt.js').read_text(encoding='utf-8')
    fr = _keys(js, '  fr:{', '},\n  en:{')
    en = _keys(js, '  en:{', '}\n};')
    assert fr == en, (fr - en, en - fr)
    assert {'receiptTitle','reference','item','amount','network','verifiedAt','receiptNote','testNote','lightningNote','print','language','paid'} <= fr
    assert "localStorage.setItem('sunupay_lang',lang)" in js
