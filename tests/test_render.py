from mibsite.model import Module, Obj
from mibsite.usage import Usage
from conftest import canon
from mibsite.render import SiteConfig, render_site
from mibsite.symbols import SymbolTable


def test_render_site(tmp_path):
    good = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi", imports={"SNMPv2-SMI": ["enterprises"]},
                  identity={"organization": "Example Org", "contact": "noc", "description": "A <test> MIB.",
                            "last_updated": "202601010000Z", "revisions": [{"date": "2026-01-01 00:00", "description": "Init"}]},
                  objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity"),
                           Obj("goodName", "1.3.6.1.4.1.99999.1", "scalar", "DisplayString", "read-only", "current",
                               description="The name.")])
    smi = Module("SNMPv2-SMI", "_root", "SNMPv2-SMI", "netsnmp", objects=[Obj("enterprises", "1.3.6.1.4.1", "node")])
    raw = Module("NOT-A-MIB", "acme", "acme/NOT-A-MIB", "raw", errors=["No ASN.1 module definition"])
    mods = [good, smi, raw]
    table = SymbolTable(mods)
    vendors = {"_root/GOOD-MIB": "Example", "_root/SNMPv2-SMI": "Standards (IETF)", "acme/NOT-A-MIB": "Acme"}
    examples = {"_root/GOOD-MIB": {"goodName": [{"v": "w", "t": "OCTET STRING", "i": "0", "f": "acme"}]}}
    cfg = SiteConfig("https://mibs.example.org", "abc1234def", "2026-11-14")
    pages = render_site(tmp_path, mods, table, vendors, examples, cfg, jobs=1,
                        usage=Usage(objects={"_root/GOOD-MIB": {"goodName"}}, by_ref={"_root/GOOD-MIB"}))
    stats_page = canon((tmp_path / "stats/index.html").read_text())
    assert "<th>MIBs LibreNMS uses<td>1" in stats_page.replace("</th>", "")
    assert "1 of 1" in stats_page  # the referenced MIB has example values
    assert 'href="/mib/SNMPv2-SMI/">SNMPv2-SMI</a><td>1 MIBs' in stats_page.replace("</td>", "")  # imported by GOOD-MIB

    page = canon((tmp_path / "mib/GOOD-MIB/index.html").read_text())
    assert "<title>GOOD-MIB" in page
    assert 'href="https://mibs.example.org/mib/GOOD-MIB/" rel="canonical"' in page
    assert 'id="goodName"' in page and "1.3.6.1.4.1.99999.1" in page
    assert '<table class="tt">' in page
    assert "<th>Example" in page and '<td class="exv"><code title="OCTET STRING · instance .0 · acme">w' in page  # first example inline
    assert 'data-d="0" id="goodMIB"' in page and 'data-d="1" id="goodName"' in page
    assert '<tr class="r br" data-d="0" id="goodMIB">' in page
    assert page.count('<tr class="dr" hidden>') == 1  # only goodName has details; goodMIB has none
    assert page.count("Example values from LibreNMS test devices") == 1  # table only for objects with examples
    assert 'href="https://github.com/librenms/librenms/blob/abc1234def/tests/snmpsim/acme.snmprec"' in page
    assert "A &lt;test&gt; MIB." in page
    assert 'href="/mib/SNMPv2-SMI/"' in page
    assert '"@type":"BreadcrumbList"' in page.replace(" ", "")
    assert "github.com/librenms/librenms/blob/abc1234def/mibs/GOOD-MIB" in page
    raw_page = canon((tmp_path / "mib/acme/NOT-A-MIB/index.html").read_text())
    assert "No ASN.1 module definition" not in raw_page  # parse status is not shown to users
    assert "cannot show an OID tree" in raw_page
    assert "Show full MIB" in page and "Fully parsed" not in page
    assert "Statistics" in page
    letters = canon((tmp_path / "mibs/index.html").read_text())
    assert 'id="letter-G"' in letters and "GOOD-MIB" in letters
    vendors_page = canon((tmp_path / "vendors/index.html").read_text())
    assert "Example" in vendors_page
    assert 'id="letter-E"' in vendors_page and 'id="letter-S"' in vendors_page
    assert vendors_page.index('id="letter-A"') < vendors_page.index('id="letter-E"')
    assert (tmp_path / "vendors/example/index.html").exists()
    assert "https://mibs.example.org/mib/GOOD-MIB/" in (tmp_path / "sitemap.xml").read_text()
    assert "Sitemap: https://mibs.example.org/sitemap.xml" in (tmp_path / "robots.txt").read_text()
    assert (tmp_path / "CNAME").read_text().strip() == "mibs.example.org"
    assert (tmp_path / "assets/style.css").exists() and (tmp_path / "404.html").exists()
    assert "/mib/GOOD-MIB/" in pages


def test_render_under_subpath(tmp_path):
    import re
    good = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi", imports={"SNMPv2-SMI": ["enterprises"]},
                  objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity")])
    smi = Module("SNMPv2-SMI", "_root", "SNMPv2-SMI", "netsnmp", objects=[Obj("enterprises", "1.3.6.1.4.1", "node")])
    mods = [good, smi]
    cfg = SiteConfig("https://user.github.io/mibs.example.org", "abc", "2026-11-14")
    render_site(tmp_path, mods, SymbolTable(mods), {}, {}, cfg, jobs=1)
    for f in tmp_path.rglob("*.html"):
        bad = [u for u in re.findall(r'(?:href|src|action)="(/[^"]*)"', canon(f.read_text()))
               if not u.startswith("/mibs.example.org/")]
        assert bad == [], f"{f}: {bad}"
    page = canon((tmp_path / "mib/GOOD-MIB/index.html").read_text())
    assert 'href="/mibs.example.org/mib/SNMPv2-SMI/"' in page
    assert 'data-root="/mibs.example.org"' in page
    assert 'href="https://user.github.io/mibs.example.org/mib/GOOD-MIB/" rel="canonical"' in page
    assert not (tmp_path / "CNAME").exists()


def test_huge_mib_gets_page_without_tree(tmp_path, monkeypatch):
    import mibsite.model as model
    monkeypatch.setattr(model, "MAX_PAGE_OBJECTS", 1)
    big = Module("BIG-MIB", "acme", "acme/BIG-MIB", "regex",
                 objects=[Obj("a", "1.3.6.1.4.1.9", "node"), Obj("b", "1.3.6.1.4.1.9.1", "scalar")])
    render_site(tmp_path, [big], SymbolTable([big]), {}, {}, SiteConfig("https://x.test", "abc", "2026-11-14"), jobs=1)
    page = canon((tmp_path / "mib/acme/BIG-MIB/index.html").read_text())
    assert "does not show the OID tree" in page and '<table class="tt">' not in page


def test_vendor_names_differing_in_case_share_one_page(tmp_path):
    a = Module("A-MIB", "nokia", "nokia/A-MIB", "pysmi")
    b = Module("B-MIB", "nokia", "nokia/B-MIB", "pysmi")
    vendors = {a.id: "Nokia (formerly 'Alcatel-Lucent')", b.id: "Nokia (Formerly 'Alcatel-Lucent')"}
    render_site(tmp_path, [a, b], SymbolTable([a, b]), vendors, {}, SiteConfig("https://x.test", "abc", "2026-11-14"), jobs=1)
    page = canon((tmp_path / "vendors/nokia-formerly-alcatel-lucent/index.html").read_text())
    assert "A-MIB" in page and "B-MIB" in page


def test_enum_label():
    from mibsite.render import enum_label
    assert enum_label("1", {"up": 1, "down": 2}) == "up(1)"
    assert enum_label("7", {"up": 1}) == "7" and enum_label("x", None) == "x"


def test_syntax_hint():
    from mibsite.model import TextualConvention
    from mibsite.render import syntax_hint
    tcs = {"InterfaceIndex": TextualConvention("InterfaceIndex", "Integer32 (1..2147483647)", display_hint="d")}
    assert syntax_hint(Obj("ifIndex", "1.1", "column", "InterfaceIndex"), tcs) == "InterfaceIndex: Integer32 (1..2147483647), display hint d"
    assert syntax_hint(Obj("st", "1.2", "column", "INTEGER", enums={"down": 2, "up": 1}), {}) == "up(1), down(2)"
    many = syntax_hint(Obj("t", "1.3", "column", "INTEGER", enums={f"v{i}": i for i in range(100)}), {})
    assert many.endswith("… 60 more")
    assert syntax_hint(Obj("n", "1.4", "scalar", "Integer32"), {}) == ""


def test_top_level_mibs_leave_a_redirect_at_the_old_url(tmp_path):
    m = Module("TOP-MIB", "_root", "TOP-MIB", "pysmi", objects=[Obj("t", "1.3.6.1.4.1.9", "node")])
    render_site(tmp_path, [m], SymbolTable([m]), {}, {}, SiteConfig("https://x.test/p", "abc", "d"), jobs=1)
    assert (tmp_path / "mib/TOP-MIB/index.html").exists()
    old = (tmp_path / "mib/_root/TOP-MIB/index.html").read_text()
    assert 'url=/p/mib/TOP-MIB/' in old and 'location.replace("/p/mib/TOP-MIB/" + location.hash)' in old


def test_imports_table_and_type_links(tmp_path):
    from mibsite.model import TextualConvention
    tc_mib = Module("TC-MIB", "_root", "TC-MIB", "pysmi", objects=[Obj("tcRoot", "1.3.6.1.4.1.5", "node")],
                    tcs=[TextualConvention("InterfaceIndexOrZero", "Integer32 (0..2147483647)")])
    user = Module("USER-MIB", "_root", "USER-MIB", "pysmi", imports={"TC-MIB": ["InterfaceIndexOrZero", "nope"]},
                  objects=[Obj("u", "1.3.6.1.4.1.6", "scalar", "InterfaceIndexOrZero")])
    mods = [tc_mib, user]
    render_site(tmp_path, mods, SymbolTable(mods), {}, {}, SiteConfig("https://x.test", "abc", "d"), jobs=1)
    page = canon((tmp_path / "mib/USER-MIB/index.html").read_text())
    assert '<a href="/mib/TC-MIB/#InterfaceIndexOrZero">InterfaceIndexOrZero</a>' in page  # imports table + Type column
    assert page.count('href="/mib/TC-MIB/#InterfaceIndexOrZero"') == 2
    assert "#nope" not in page  # not defined in TC-MIB: no link
    tc_page = canon((tmp_path / "mib/TC-MIB/index.html").read_text())
    assert 'id="InterfaceIndexOrZero"' in tc_page


def test_smi_date():
    from mibsite.render import smi_date
    assert smi_date("200006140000Z") == ("2000-06-14T00:00Z", "2000-06-14")
    assert smi_date("9809281000Z") == ("1998-09-28T10:00Z", "1998-09-28")
    assert smi_date("garbage") is None and smi_date("201313010000Z") is None


def test_seo_titles_descriptions_and_lastmod(tmp_path):
    import json
    import re
    long_desc = "The MIB module for managing widgets. " * 10
    good = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi", identity={"description": long_desc},
                  objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity")])
    bare = Module("BARE-MIB", "acme", "acme/BARE-MIB", "pysmi", objects=[Obj("b", "1.3.6.1.4.1.5", "node")])
    mods = [good, bare]
    pages = render_site(tmp_path, mods, SymbolTable(mods), {good.id: "Example"}, {},
                        SiteConfig("https://x.test", "abc", "d"), jobs=1, modified={good.id: "2025-03-04"})
    page = canon((tmp_path / "mib/GOOD-MIB/index.html").read_text())
    assert "<title>GOOD-MIB (1.3.6.1.4.1.99999) – Example SNMP MIB | LibreNMS MIB Browser</title>" in page
    desc = re.search(r'<meta content="([^"]*)" name="description"', page).group(1)
    assert desc.startswith("GOOD-MIB: The MIB module for managing widgets.") and desc.endswith("…") and len(desc) <= 160
    article = [json.loads(j) for j in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page)][1]
    assert article["dateModified"] == "2025-03-04" and article["publisher"] == {"@id": "https://www.librenms.org/#org"}
    bare_page = canon((tmp_path / "mib/acme/BARE-MIB/index.html").read_text())
    assert "<title>BARE-MIB (1.3.6.1.4.1.5) – SNMP MIB |" in bare_page  # vendor "Other" is left out
    assert 'content="BARE-MIB SNMP MIB: 1 objects with OIDs' in bare_page and "dateModified" not in bare_page
    sitemap = (tmp_path / "sitemap.xml").read_text()
    assert "<loc>https://x.test/mib/GOOD-MIB/</loc><lastmod>2025-03-04</lastmod></url>" in sitemap
    assert "<loc>https://x.test/mib/acme/BARE-MIB/</loc></url>" in sitemap
    descriptions = set()
    for p in pages:
        if p.startswith("/mib/"):
            continue
        html = canon((tmp_path / p.strip("/") / "index.html").read_text())
        descriptions.add(re.search(r'<meta content="([^"]*)" name="description"', html).group(1))
    assert len(descriptions) == len([p for p in pages if not p.startswith("/mib/")])  # no two pages share one
    assert any(d.startswith("All 2 SNMP MIBs") for d in descriptions)


def test_clip():
    from mibsite.render import clip
    assert clip("short  text") == "short text"
    assert clip("one two three four", 12) == "one two…"
    assert clip("x" * 20, 10) == "x" * 9 + "…"


def test_home_page_has_popular_mibs_and_about_text(tmp_path):
    import re
    good = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi",
                  identity={"description": "Widgets on a device. Copyright (c) 2009 IETF Trust and others."},
                  objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity")])
    render_site(tmp_path, [good], SymbolTable([good]), {}, {}, SiteConfig("https://x.test", "abc", "d"), jobs=1,
                usage=Usage(objects={good.id: {"goodMIB"}}))
    home = canon((tmp_path / "index.html").read_text())
    assert "Popular MIBs" in home and '<a href="/mib/GOOD-MIB/">GOOD-MIB</a>' in home
    assert "Widgets on a device." in home and "Copyright" not in home
    assert "About the LibreNMS MIB Browser" in home
    text = re.sub(r"<[^>]+>", " ", home.split("<main>")[1].split("</main>")[0])
    assert len(text.split()) >= 250


def test_home_meta_description_length(tmp_path):
    import re
    m = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi", objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity")])
    render_site(tmp_path, [m], SymbolTable([m]), {}, {}, SiteConfig("https://x.test", "abc", "d"), jobs=1)
    desc = re.search(r'<meta content="([^"]*)" name="description"', canon((tmp_path / "index.html").read_text())).group(1)
    assert desc.startswith("Browse all 1 SNMP MIBs in LibreNMS from 1 vendors:") and 140 <= len(desc) <= 160


def test_json_ld(tmp_path):
    import json
    import re
    good = Module("GOOD-MIB", "_root", "GOOD-MIB", "pysmi", identity={"description": "Has </script> in it."},
                  objects=[Obj("goodMIB", "1.3.6.1.4.1.99999", "identity")])
    render_site(tmp_path, [good], SymbolTable([good]), {good.id: "Example"}, {},
                SiteConfig("https://x.test", "abc", "d"), jobs=1, modified={good.id: "2025-01-02"})

    def blocks(path):
        html = (tmp_path / path).read_text()
        return [json.loads(j) for j in re.findall(r'<script type="?application/ld\+json"?>(.*?)</script>', html, re.S)]

    graph = blocks("index.html")[0]["@graph"]
    site = next(x for x in graph if x["@type"] == "WebSite")
    assert site["@id"] == "https://x.test/#website" and site["description"].startswith("Browse all 1 SNMP MIBs")
    assert site["potentialAction"]["target"] == "https://x.test/search/?q={search_term_string}"
    assert any(x["@type"] == "Organization" for x in graph)
    mib = blocks("mib/GOOD-MIB/index.html")
    assert [b["@type"] for b in mib] == ["BreadcrumbList", "TechArticle"]
    assert mib[1]["isPartOf"] == {"@id": "https://x.test/#website"} and mib[1]["dateModified"] == "2025-01-02"
    assert "</script>" in mib[1]["description"]  # escaped in the HTML, intact as JSON
    vendor = blocks("vendors/example/index.html")
    assert vendor[0]["@type"] == "CollectionPage" and vendor[0]["mainEntity"]["numberOfItems"] == 1
    assert vendor[0]["mainEntity"]["itemListElement"][0]["url"] == "https://x.test/mib/GOOD-MIB/"
    assert vendor[1]["itemListElement"][-1]["item"] == "https://x.test/vendors/example/"
    assert blocks("mibs/index.html")[0]["@type"] == "CollectionPage"
    assert blocks("vendors/index.html")[0]["@type"] == "CollectionPage"


def test_short_description_gets_a_full_stop_before_the_facts():
    from mibsite.render import _meta_description
    m = Module("S-MIB", "s", "s/S-MIB", "pysmi", identity={"description": "Enterprise-subtree for Stulz devices"})
    assert _meta_description(m, "Stulz") == "S-MIB: Enterprise-subtree for Stulz devices. 0 objects with OIDs, types and descriptions."
    m.identity["description"] = "Done."
    assert _meta_description(m, "Stulz").startswith("S-MIB: Done. 0 objects")
