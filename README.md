# mibs.librenms.org

Static browser for every SNMP MIB shipped with [LibreNMS](https://github.com/librenms/librenms), published at <https://mibs.librenms.org>.

## Build locally

Requirements: [uv](https://docs.astral.sh/uv/), git, net-snmp (`snmptranslate`; Debian/Ubuntu `apt install snmp`, Arch `pacman -S net-snmp`).

```bash
scripts/build.sh                      # fetch LibreNMS into .cache/librenms and build ./site
scripts/build.sh --only 'cisco/*'     # build a subset
python3 -m http.server -d site 8000   # preview
uv run pytest                         # tests
```

## How it works

Each MIB is parsed by the first tier that succeeds: pysmi → net-snmp `snmptranslate` → regex scrape → raw. Every MIB gets a page; the badge shows which tier was used and any parser messages are listed. Import aliases follow net-snmp's `module_map` (e.g. `RFC-1213` → `RFC1213-MIB`). Vendors come from the IANA enterprise number of the MIB's root OID. Example values come from LibreNMS `tests/snmpsim/*.snmprec`. Search is a static, prefix-sharded JSON index under `/data/search/`. The sitemap's `<lastmod>` (and the page's `dateModified`) is the last git commit date of the MIB file, so the LibreNMS clone keeps its full history (without blobs); a shallow clone gives no dates.

## External MIBs

`extra-mibs/` holds public MIBs that LibreNMS MIBs import but LibreNMS does not ship. Refresh with `uv run mibsite fetch-extra`. Sources are listed in `extra-mibs/SOURCES.md`.

## Deployment

`.github/workflows/build.yml` builds on push to `master`, on manual dispatch and on a `librenms-mibs-updated` repository dispatch (sent by LibreNMS CI when its MIBs change), then deploys to GitHub Pages.
