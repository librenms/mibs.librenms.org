"""schema.org JSON-LD for the pages. Every page points at the one WebSite (described on the home page) and at
LibreNMS, the publisher, by @id, so the graph links up without repeating them."""
from __future__ import annotations

from jinja2.utils import htmlsafe_json_dumps
from markupsafe import Markup

ORG_ID = "https://www.librenms.org/#org"
CONTEXT = "https://schema.org"


def ld(data: dict) -> Markup:
    """A <script type="application/ld+json"> block (escaped so a description can never close the script)."""
    return Markup('<script type="application/ld+json">') + htmlsafe_json_dumps(data) + Markup("</script>")


def website_id(cfg) -> str:
    return cfg.base_url + "/#website"


def home(cfg, description: str) -> Markup:
    return ld({"@context": CONTEXT, "@graph": [
        {"@type": "WebSite", "@id": website_id(cfg), "name": "LibreNMS MIB Browser", "url": cfg.base_url + "/",
         "description": description, "inLanguage": "en", "publisher": {"@id": ORG_ID},
         "potentialAction": {"@type": "SearchAction", "target": cfg.base_url + "/search/?q={search_term_string}",
                             "query-input": "required name=search_term_string"}},
        {"@type": "Organization", "@id": ORG_ID, "name": "LibreNMS", "url": "https://www.librenms.org/",
         "logo": cfg.base_url + "/assets/logo-light.svg", "sameAs": ["https://github.com/librenms/librenms"]},
    ]})


def breadcrumbs(cfg, crumbs: list[tuple[str, str]]) -> Markup:
    """crumbs: [(name, URL path)] from the top down; the last is the page itself."""
    return ld({"@context": CONTEXT, "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": name, "item": cfg.base_url + path}
        for i, (name, path) in enumerate(crumbs, 1)]})


def collection(cfg, name: str, description: str, path: str, items: list | None = None) -> Markup:
    """A list page; items: the MIBs (anything with .name and .url) it lists, when few enough to be worth naming."""
    page = {"@context": CONTEXT, "@type": "CollectionPage", "name": name, "description": description,
            "url": cfg.base_url + path, "isPartOf": {"@id": website_id(cfg)}, "publisher": {"@id": ORG_ID},
            "inLanguage": "en"}
    if items is not None:
        page["mainEntity"] = {"@type": "ItemList", "numberOfItems": len(items), "itemListElement": [
            {"@type": "ListItem", "position": i, "name": x.name, "url": cfg.base_url + x.url} for i, x in enumerate(items, 1)]}
    return ld(page)


def article(cfg, headline: str, description: str, path: str, about: str, modified: str | None) -> Markup:
    data = {"@context": CONTEXT, "@type": "TechArticle", "headline": headline, "description": description,
            "url": cfg.base_url + path, "about": about, "isPartOf": {"@id": website_id(cfg)},
            "publisher": {"@id": ORG_ID}, "inLanguage": "en"}
    if modified:
        data["dateModified"] = modified
    return ld(data)
