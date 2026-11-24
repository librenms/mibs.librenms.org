from __future__ import annotations

import hashlib
import re

import minify_html
import shutil
from collections import ChainMap, Counter, defaultdict
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape as html_escape

from . import jsonld
from .discover import EXTERNAL_DIR, ROOT_DIR
from .model import Module, Obj, TextualConvention
from .parse_pysmi import BASE_MIBS
from .progress import Progress
from .symbols import SymbolTable, build_tree
from .usage import Usage
from .vendors import enterprise_number, module_root_oid, slugify

STATIC = Path(__file__).parent / "static"
ACCESS_ABBR = {"read-only": "RO", "read-write": "RW", "read-create": "RC", "not-accessible": "NA",
               "accessible-for-notify": "AN", "write-only": "WO"}
TIER_LABEL = {"pysmi": "Fully parsed", "netsnmp": "Parsed by net-snmp", "regex": "Partially parsed", "raw": "Unparsed"}
EXTRA_REPO = "https://github.com/librenms/mibs.librenms.org/blob/master/extra-mibs/"


@dataclass
class SiteConfig:
    base_url: str
    librenms_sha: str
    build_date: str


def site_root(cfg: SiteConfig) -> str:
    """URL path the site is served under: "" for a domain root, "/repo" for a GitHub project page."""
    return urlparse(cfg.base_url).path.rstrip("/")


def asset_version() -> str:
    """Cache-busting token for /assets: changes whenever any static file changes (a date would pair a cached
    old search.js with a new site.js after a same-day redeploy)."""
    h = hashlib.sha256()
    for f in sorted(STATIC.iterdir()):
        h.update(f.name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:10]


def enum_label(value: str, enums: dict[str, int] | None) -> str:
    """Show an enumerated example value with its name from the MIB: "1" -> "up(1)"."""
    for name, num in (enums or {}).items():
        if str(num) == value:
            return f"{name}({value})"
    return value


def type_link(syntax: str, links: dict[str, str]) -> Markup:
    """Syntax text with its leading type name linked to the type's definition when known."""
    ty = syntax.split(" ")[0]
    if ty not in links:
        return html_escape(syntax)
    return Markup('<a href="{}">{}</a>{}').format(links[ty], ty, syntax[len(ty):])


def smi_date(value: str) -> tuple[str, str] | None:
    """SMI ExtUTCTime (LAST-UPDATED/REVISION) -> (ISO 8601 for <time datetime>, YYYY-MM-DD to show).
    Forms: YYMMDDHHMMZ (19YY) and YYYYMMDDHHMMZ."""
    v = (value or "").strip().rstrip("Zz")
    if not v.isdigit() or len(v) not in (10, 12):
        return None
    if len(v) == 10:
        v = "19" + v
    y, mo, d, h, mi = v[:4], v[4:6], v[6:8], v[8:10], v[10:12]
    if not ("01" <= mo <= "12" and "01" <= d <= "31" and h <= "23" and mi <= "59"):
        return None
    return f"{y}-{mo}-{d}T{h}:{mi}Z", f"{y}-{mo}-{d}"


def make_env(root: str = "") -> Environment:
    env = Environment(loader=PackageLoader("mibsite", "templates"), autoescape=select_autoescape(["j2", "html"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(jsonld=jsonld, ACCESS_ABBR=ACCESS_ABBR, TIER_LABEL=TIER_LABEL, root=root,
                       asset_v=asset_version())
    env.filters["slug"] = slugify
    env.filters["enum_label"] = enum_label
    env.filters["type_link"] = type_link
    env.filters["smi_date"] = smi_date
    return env


def source_url(m: Module, cfg: SiteConfig) -> str:
    if m.dir == EXTERNAL_DIR:
        return EXTRA_REPO + m.file
    return f"https://github.com/librenms/librenms/blob/{cfg.librenms_sha}/mibs/{m.file}"


META_DESCRIPTION_MAX = 160  # search results show about this much


def clip(text: str, limit: int = META_DESCRIPTION_MAX) -> str:
    """Whitespace collapsed, cut at a word boundary to at most `limit` characters ("…" marks a cut)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:.-") + "…"


_COPYRIGHT = re.compile(r"\s*\bCopyright\b.*", re.I | re.S)


def summary(m: Module) -> str:
    """The MIB's own description on one line, without the copyright notice many MIBs end it with."""
    return " ".join(_COPYRIGHT.sub("", (m.identity or {}).get("description") or "").split())


def _meta_description(m: Module, vendor: str) -> str:
    """The MIB's own description first (what a searcher wants to read), then what the page holds."""
    desc = summary(m)
    of = "" if vendor == "Other" else f" ({vendor})"
    facts = f"{len(m.objects):,} objects with OIDs, types and descriptions."
    if not desc:
        return clip(f"{m.name} SNMP MIB{of}: {facts}")
    if len(desc) < 80:
        desc = (desc if desc[-1] in ".!?" else desc + ".") + " " + facts
    return clip(f"{m.name}: {desc}")


MAX_HINT_VALUES = 40  # enumerations listed in a Type tooltip (IANAifType alone has ~280)


def syntax_hint(o: Obj, tcs: dict[str, TextualConvention]) -> str:
    """Tooltip text for the Type cell: the textual convention behind the syntax and the allowed values."""
    lines = []
    tc = tcs.get((o.syntax or "").split(" ")[0])
    if tc:
        lines.append(f"{tc.name}: {tc.syntax or '?'}" + (f", display hint {tc.display_hint}" if tc.display_hint else ""))
    enums = o.enums or (tc.enums if tc else None)
    if enums:
        values = [f"{k}({v})" for k, v in sorted(enums.items(), key=lambda kv: kv[1])]
        if len(values) > MAX_HINT_VALUES:
            values = values[:MAX_HINT_VALUES] + [f"… {len(values) - MAX_HINT_VALUES} more"]
        lines.append(", ".join(values))
    return "\n".join(lines)


def visible_tcs(m: Module, table: SymbolTable, all_tcs: dict[str, TextualConvention]) -> ChainMap:
    """TCs by name as seen from module m: its own first, then those of the modules it imports, then any.
    A ChainMap, so the ~all-TCs dict is shared rather than copied per module."""
    layers = [{t.name: t for t in m.tcs}]
    for modname in m.imports:
        src = table.resolve_module(m, modname)
        if src is not None and src.tcs:
            layers.append({t.name: t for t in src.tcs})
    return ChainMap(*layers, all_tcs)


_DEFINED: dict[str, frozenset[str]] = {}


def defined_names_of(m: Module) -> frozenset[str]:
    """Names a module defines (objects and textual conventions), computed once per module per build:
    a widely imported MIB (SNMPv2-TC, HUAWEI-MIB) is looked up from thousands of pages."""
    if m.id not in _DEFINED:
        _DEFINED[m.id] = frozenset(x.name for x in [*m.objects, *m.tcs])
    return _DEFINED[m.id]


def mib_context(m: Module, table: SymbolTable, vendor: str, examples: dict[str, list], cfg: SiteConfig,
                all_tcs: dict[str, TextualConvention] | None = None, modified: str | None = None) -> dict:
    root = site_root(cfg)
    imports, defined_in = [], {}  # defined_in: imported symbol -> link to its definition
    for modname, syms in m.imports.items():
        target = table.resolve_module(m, modname)
        names = defined_names_of(target) if target else frozenset()
        links = [(s, f"{root}{target.url}#{s}" if s in names else None) for s in syms]
        defined_in.update((s, h) for s, h in links if h)
        imports.append({"name": modname, "url": target.url if target else None, "symbols": links})
    imported_by = sorted((table.modules[i] for i in table.imported_by.get(m.id, [])), key=lambda x: x.name)
    objs = m.objects if m.shows_objects else []
    tcs = visible_tcs(m, table, all_tcs or {})
    hints = {o.name: h for o in objs if (h := syntax_hint(o, tcs))}
    # Type column: a textual convention links to its definition, here or in the MIB it is imported from
    own_tcs = {t.name for t in m.tcs}
    type_links = {}
    for ty in {(o.syntax or "").split(" ")[0] for o in objs} | {(t.syntax or "").split(" ")[0] for t in m.tcs}:
        if ty in own_tcs:
            type_links[ty] = f"#{ty}"
        elif ty in defined_in:
            type_links[ty] = defined_in[ty]
    return {
        "m": m, "vendor": vendor, "vendor_slug": slugify(vendor), "too_large": not m.shows_objects,
        "tree": build_tree(objs), "unresolved": [o for o in objs if not o.oid], "imports": imports,
        # only what the template shows: whole Modules here would pickle most of the dataset per hub MIB
        "imported_by": [{"name": x.name, "url": x.url, "dir": x.dir} for x in imported_by],
        "notifications": [o for o in objs if o.kind == "notification"],
        "groups": [o for o in objs if o.kind in ("group", "compliance")],
        "examples": examples, "hints": hints, "type_links": type_links, "root_oid": module_root_oid(m), "source_url": source_url(m, cfg),
        "description": _meta_description(m, vendor), "modified": modified,
    }


def minify(html: str) -> str:
    """Minify a rendered page (~12% smaller); the DOM is unchanged, so scripts and tests see the same tree."""
    return minify_html.minify(html, minify_css=True, minify_js=True, keep_closing_tags=False)


_WORKER: dict = {}


def _init(cfg, out):
    _WORKER.update(env=make_env(site_root(cfg)), cfg=cfg, out=out)


def _render_mib(ctx: dict) -> str:
    return _page(_WORKER["env"], _WORKER["out"], ctx["m"].url, "mib.html.j2", _WORKER["cfg"], **ctx)


def _page(env, out: Path, path: str, template: str, cfg: SiteConfig, **ctx) -> str:
    """Render `template` to the file serving URL `path` ("/x/" -> x/index.html, "/404.html" -> 404.html)."""
    dest = out / path.strip("/") if path.endswith(".html") else out / path.strip("/") / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(minify(env.get_template(template).render(cfg=cfg, path=path, **ctx)))
    return path


def _letter(name: str) -> str:
    c = name[:1].upper()
    return c if c.isalpha() else "0-9"


def group_by_letter(items, name) -> list[tuple[str, list]]:
    """[(letter, items)] with letters A–Z first and "0-9" last; item order kept within a letter."""
    groups: dict[str, list] = defaultdict(list)
    for it in items:
        groups[_letter(name(it))].append(it)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "0-9", kv[0]))


def vendor_oid(mods: list[Module]) -> str | None:
    """The enterprise OID (1.3.6.1.4.1.N) most of a vendor's MIBs live under, if any."""
    counts = Counter(n for n in (enterprise_number(module_root_oid(m)) for m in mods) if n is not None)
    return f"1.3.6.1.4.1.{counts.most_common(1)[0][0]}" if counts else None


def write_moved_pages(out: Path, mods: list[Module], cfg: SiteConfig) -> None:
    """Top-level MIBs moved from /mib/_root/NAME/ to /mib/NAME/: leave a redirect (keeping any #anchor)."""
    root = site_root(cfg)
    for m in mods:
        new = escape(f"{root}{m.url}")
        dest = out / "mib" / ROOT_DIR / m.name / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            f'<!doctype html><meta charset="utf-8"><title>{escape(m.name)} has moved</title>'
            f'<meta name="robots" content="noindex"><link rel="canonical" href="{escape(cfg.base_url + m.url)}">'
            f'<meta http-equiv="refresh" content="0; url={new}">'
            f'<script>location.replace("{new}" + location.hash)</script>'
            f'<p>Moved to <a href="{new}">{new}</a>.</p>\n')


def usage_stats(mods: list[Module], table: SymbolTable, examples: dict, usage: Usage | None, top: int = 15) -> dict:
    """Numbers for the statistics page: what LibreNMS uses, what other MIBs import, what has test data."""
    usage = usage or Usage()
    by_id = {m.id: m for m in mods}
    used = {i for i in usage.modules if i in by_id}
    objects = {i: s for i, s in usage.objects.items() if i in by_id}
    imported = [(by_id[i], len(src)) for i, src in table.imported_by.items() if i in by_id]
    return {
        "used_mibs": len(used),
        "used_objects": sum(len(s) for s in objects.values()),
        "by_ref": len(usage.by_ref & used), "by_name": len(usage.by_name & used), "by_oid": len(usage.by_oid & used),
        "by_object": len(usage.by_object & used),
        "used_with_examples": sum(1 for i in used if i in examples),
        "top_used": sorted(((by_id[i], len(s)) for i, s in objects.items()), key=lambda x: (-x[1], x[0].name))[:top],
        "top_imported": sorted(imported, key=lambda x: (-x[1], x[0].name))[:top],
        "example_mibs": len(examples),
        "example_objects": sum(len(v) for v in examples.values()),
    }


def render_site(out: Path, modules: list[Module], table: SymbolTable, vendors: dict[str, str], examples: dict,
                cfg: SiteConfig, jobs: int, usage: Usage | None = None,
                modified: dict[str, str] | None = None) -> list[str]:
    """Write the site to `out`; returns the page paths. modified: module id -> date (YYYY-MM-DD) its MIB file
    last changed, for the sitemap's <lastmod> and the page's dateModified."""
    modified = modified or {}
    out.mkdir(parents=True, exist_ok=True)
    (out / ".mibsite-output").write_text("generated by mibsite\n")
    assets = out / "assets"
    assets.mkdir(exist_ok=True)
    for f in STATIC.iterdir():
        shutil.copy2(f, assets / f.name)

    mods = sorted(modules, key=lambda m: (m.name.lower(), m.id))
    all_tcs = {t.name: t for m in mods for t in m.tcs}
    contexts = [mib_context(m, table, vendors.get(m.id, "Other"), examples.get(m.id, {}), cfg, all_tcs,
                            modified.get(m.id)) for m in mods]
    with Pool(jobs or None, initializer=_init, initargs=(cfg, out)) as pool:
        progress = Progress("MIB pages", len(contexts))
        pages = []
        for page in pool.imap(_render_mib, contexts, chunksize=8):
            pages.append(page)
            progress.step()
        progress.close()

    env = make_env(site_root(cfg))
    # keyed by slug, the page path: IANA names that differ only in case (6527/7483 "Nokia (formerly/Formerly
    # 'Alcatel-Lucent')") share one page, shown under the first of their names
    by_vendor: dict[str, list[Module]] = defaultdict(list)
    vendor_name: dict[str, str] = {}
    for m in mods:
        name = vendors.get(m.id, "Other")
        slug = slugify(name)
        by_vendor[slug].append(m)
        vendor_name[slug] = min(vendor_name.get(slug, name), name)
    mib_groups = group_by_letter(mods, lambda m: m.name)
    letters = [letter for letter, _ in mib_groups]
    stats = {"modules": len(mods), "objects": sum(len(m.objects) for m in mods), "vendors": len(by_vendor),
             "tiers": dict(Counter(m.tier for m in mods)), "examples": len(examples)}
    vendor_list = sorted(((vendor_name[s], s, vm) for s, vm in by_vendor.items()), key=lambda v: v[0].lower())

    vendor_cards = [{"name": v, "slug": s, "count": len(vm), "oid": vendor_oid(vm)} for v, s, vm in vendor_list]
    top_vendors = sorted((c for c in vendor_cards if c["name"] != "Other"), key=lambda c: -c["count"])[:20]

    used = usage_stats(mods, table, examples, usage, top=12)
    popular = [{"m": m, "vendor": vendors.get(m.id, "Other"),
                "desc": clip(summary(m), 110)} for m, _ in used["top_used"]]
    pages.append(_page(env, out, "/", "home.html.j2", cfg, stats=stats, letters=letters, top_vendors=top_vendors,
                       popular=popular, used_mibs=used["used_mibs"]))
    pages.append(_page(env, out, "/mibs/", "letters.html.j2", cfg,
                       groups=mib_groups, vendors=vendors))
    pages.append(_page(env, out, "/vendors/", "vendors.html.j2", cfg,
                       groups=group_by_letter(vendor_cards, lambda c: c["name"])))
    for vendor, slug, vmods in vendor_list:
        pages.append(_page(env, out, f"/vendors/{slug}/", "vendor.html.j2", cfg, vendor=vendor, mods=vmods,
                           groups=group_by_letter(vmods, lambda m: m.name)))
    pages.append(_page(env, out, "/search/", "search.html.j2", cfg))
    problem = [m for m in mods if m.tier != "pysmi" and m.name not in BASE_MIBS]  # base MIBs: net-snmp by design
    pages.append(_page(env, out, "/stats/", "stats.html.j2", cfg, stats=stats, problem=problem,
                       usage=usage_stats(mods, table, examples, usage)))
    _page(env, out, "/404.html", "404.html.j2", cfg)
    write_moved_pages(out, [m for m in mods if m.dir == ROOT_DIR], cfg)

    host = urlparse(cfg.base_url).hostname or ""
    if not site_root(cfg):  # a custom domain serves from "/"; project pages need no CNAME file
        (out / "CNAME").write_text(host + "\n")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {cfg.base_url}/sitemap.xml\n")
    lastmod = {m.url: modified[m.id] for m in mods if m.id in modified}
    urls = "".join(f"<url><loc>{escape(cfg.base_url + p)}</loc>"
                   + (f"<lastmod>{lastmod[p]}</lastmod>" if p in lastmod else "") + "</url>" for p in pages)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    assert len(pages) < 50000, "sitemap needs splitting into a sitemap index"
    return pages
