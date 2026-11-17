import re

from collector import extract_ads_from_email_html

PATTERNS = [re.compile(r"leboncoin\.fr/(?:ad|vi|[a-z_]+)/.*\d{6,}", re.I)]

HTML = """
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
 <tr><td><a href="https://www.leboncoin.fr/aide">Aide</a></td></tr>
</table></body></html>
"""


def test_extract_ads_from_alert_email():
    ads = {a.title: a for a in extract_ads_from_email_html(HTML, "leboncoin", PATTERNS)}
    assert set(ads) == {"ThinkPad T470 sans disque dur", "PS4 slim surchauffe"}
    t = ads["ThinkPad T470 sans disque dur"]
    assert t.price == 95.0 and t.uid == "lbc:2511111111"
    p = ads["PS4 slim surchauffe"]
    assert p.price == 40.0 and p.shipping == 5.5
    assert p.url.startswith("https://www.leboncoin.fr/ad/consoles/")


def test_broken_html_does_not_crash():
    assert extract_ads_from_email_html("<a href='http://x", "x", PATTERNS) == []
