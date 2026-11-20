'use strict';
const receiptTexts={
  fr:{receiptTitle:'REÇU DE PAIEMENT',language:'Langue',paid:'✓ Confirmé',reference:'Référence',item:'Article',amount:'Montant',network:'Réseau',verifiedAt:'Vérifié le',receiptNote:'Ce reçu correspond à un paiement vérifié par SunuPay.',testNote:'Les bitcoins regtest n’ont aucune valeur financière.',lightningNote:'Les fonds Lightning sont gérés par le fournisseur LNbits configuré.',print:'Imprimer / Enregistrer en PDF',title:'Reçu SunuPay'},
  en:{receiptTitle:'PAYMENT RECEIPT',language:'Language',paid:'✓ Confirmed',reference:'Reference',item:'Item',amount:'Amount',network:'Network',verifiedAt:'Verified at',receiptNote:'This receipt reflects a payment verified by SunuPay.',testNote:'Regtest bitcoins have no financial value.',lightningNote:'Lightning funds are handled by the configured LNbits provider.',print:'Print / Save as PDF',title:'SunuPay receipt'}
};
const langSelect=document.getElementById('receipt-lang');
function setReceiptLanguage(language){
  const lang=language==='en'?'en':'fr';
  document.documentElement.lang=lang;
  localStorage.setItem('sunupay_lang',lang);
  langSelect.value=lang;
  langSelect.setAttribute('aria-label',receiptTexts[lang].language);
  document.title=receiptTexts[lang].title;
  document.querySelectorAll('[data-i18n]').forEach(el=>{const key=el.dataset.i18n;if(receiptTexts[lang][key]!==undefined)el.textContent=receiptTexts[lang][key]});
}
langSelect.addEventListener('change',event=>setReceiptLanguage(event.target.value));
const printButton=document.getElementById('print-receipt');
if(printButton)printButton.addEventListener('click',()=>window.print());
setReceiptLanguage(localStorage.getItem('sunupay_lang'));
