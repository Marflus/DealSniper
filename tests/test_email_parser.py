import re
from email.message import EmailMessage

from dealsniper.sources.email_parser import detect_source, extract_ads, parse_email

PATTERNS = [re.compile(r"leboncoin\.fr/(?:ad|vi|[a-z_]+)/.*\d{6,}", re.I)]
SENDERS = {"leboncoin": "leboncoin", "ebay": "ebay"}

ALERT_HTML = """
<html><body><table>
 <tr><td>
   <a href="https://www.leboncoin.fr/ad/ordinateurs/2511111111?utm_source=alerte"><img src="x.jpg"></a>
   <a href="https://www.leboncoin.fr/ad/ordinateurs/2511111111?utm_source=alerte">ThinkPad T470 sans disque dur</a>
   <p>95 €</p><p>Lyon 69003</p>
 </td></tr>
 <tr><td>
   <a href="https://click.example.com/r?url=https%3A%2F%2Fwww.leboncoin.fr%2Fad%2Fconsoles%2F2522222222">PS4 slim surchauffe</a>
   <span>40 €</span> <span>Livraison 5,50 €</span>
 </td></tr>
 <tr><td><a href="https://www.leboncoin.fr/aide">Aide</a> <a href="https://www.leboncoin.fr/compte/desinscription">Se désinscrire</a></td></tr>
</table></body></html>
"""

# Lien de suivi opaque (aucune URL d'annonce lisible) : gardé car il entoure un prix.
OPAQUE_HTML = """
<table><tr><td>
  <a href="https://links.mail.example.net/ls/click?upn=AbC123"><img src="p.jpg"></a>
  <a href="https://links.mail.example.net/ls/click?upn=XyZ789">Nintendo Switch joycon drift</a>
  <div>80 €</div><div>Nantes 44000</div>
</td></tr>
<tr><td><a href="https://links.mail.example.net/ls/click?upn=Logo">leboncoin</a></td></tr></table>
"""


def _msg(sender, subject, html):
    msg = EmailMessage()
    msg["From"], msg["Subject"] = sender, subject
    msg.set_content("version texte")
    msg.add_alternative(html, subtype="html")
    return msg


def test_alert_email_extraction():
    ads = {a.title: a for a in extract_ads(ALERT_HTML, "leboncoin", PATTERNS)}
    assert set(ads) == {"ThinkPad T470 sans disque dur", "PS4 slim surchauffe"}
    t = ads["ThinkPad T470 sans disque dur"]
    assert t.price == 95.0 and t.uid == "lbc:2511111111" and t.location == "Lyon 69003"
    p = ads["PS4 slim surchauffe"]
    assert p.price == 40.0 and p.shipping == 5.5 and p.url.startswith("https://www.leboncoin.fr/ad/consoles/")


def test_opaque_tracking_links_are_resolved_or_kept():
    ads = extract_ads(OPAQUE_HTML, "leboncoin", PATTERNS)
    assert len(ads) == 1 and ads[0].price == 80.0 and not ads[0].url_is_stable

    resolved = extract_ads(OPAQUE_HTML, "leboncoin", PATTERNS,
                           resolver=lambda url: "https://www.leboncoin.fr/ad/consoles/2533333333")
    assert resolved[0].url_is_stable and resolved[0].uid == "lbc:2533333333"


def test_forwarded_email_is_recognized():
    parsed = parse_email(_msg("Moi <moi@hotmail.fr>", "TR: Nouvelles annonces leboncoin", ALERT_HTML),
                         SENDERS, PATTERNS)
    assert parsed.source == "leboncoin" and parsed.forwarded and len(parsed.ads) == 2


def test_non_alert_emails_produce_no_fake_ads():
    welcome = "<p>Bienvenue sur leboncoin ! <a href='https://www.leboncoin.fr/compte'>Mon compte</a></p>"
    parsed = parse_email(_msg("leboncoin <no-reply@leboncoin.fr>", "Bienvenue", welcome), SENDERS, PATTERNS)
    assert parsed.source == "leboncoin" and parsed.ads == []
    google = parse_email(_msg("Google <no-reply@accounts.google.com>", "Alerte de sécurité", "<p>Connexion</p>"),
                         SENDERS, PATTERNS)
    assert google.source is None


def test_detect_source_and_broken_html():
    assert detect_source("alertes@ebay.fr", "", "", SENDERS) == "ebay"
    assert detect_source("x@y.fr", "", "rien", SENDERS) is None
    assert extract_ads("<a href='http://x", "x", PATTERNS) == []
