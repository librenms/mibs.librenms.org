from __future__ import annotations

import datetime as dt
import fnmatch
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

from .asn1text import raw_descriptions, read_text, split_modules
from .discover import discover
from .model import EXTERNAL_DIR
from .parse import parse_all
from .render import SiteConfig, render_site
from .search_index import write_search_index
from .progress import phase
from .snmprec import collect_examples
from .symbols import SymbolTable
from .usage import collect_usage
from .vendors import classify, load_enterprises


def safe_clean(out: Path) -> None:
    if not out.exists():
        return
    if any(out.iterdir()) and not (out / ".mibsite-output").exists():
        raise SystemExit(f"refusing to delete {out}: not empty and not a previous mibsite output")
    shutil.rmtree(out)


def _sha(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "master"


def file_dates(folder: Path) -> dict[str, str]:
    """Last commit date (YYYY-MM-DD) of each file under `folder`, keyed by its path relative to `folder`.
    Empty when `folder` is not in a git repository or the clone is shallow (every file would get the date of
    the one commit there is)."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=True).stdout
    try:
        if git("rev-parse", "--is-shallow-repository").strip() != "false":
            return {}
        # --no-renames: rename detection would fetch blobs in a blob-less (partial) clone
        log = git("-c", "core.quotepath=off", "log", "--no-renames", "--relative", "--name-only", "--format=@%cs", "--", ".")
    except (OSError, subprocess.CalledProcessError):
        return {}
    dates: dict[str, str] = {}
    date = ""
    for line in log.splitlines():
        if line.startswith("@"):
            date = line[1:]
        elif line:
            dates.setdefault(line, date)  # newest commit first
    return dates


def restore_descriptions(modules, mibs_root: Path, extra: Path | None) -> None:
    """Replace parser-normalised descriptions with the text as the MIB lays it out (line breaks kept)."""
    by_file = defaultdict(list)
    for m in modules:
        by_file[(m.dir, m.file)].append(m)
    for (d, rel), mods in by_file.items():
        path = (extra if d == EXTERNAL_DIR else mibs_root) / rel
        if not path.is_file():
            continue
        segments = dict(split_modules(read_text(path)))
        for mod in mods:
            if mod.name not in segments:
                continue
            descs = raw_descriptions(segments[mod.name])
            for x in [*mod.objects, *mod.tcs]:
                if x.name in descs:
                    x.description = descs[x.name]
            ident = next((o for o in mod.objects if o.kind == "identity"), None)
            if mod.identity and ident is not None and ident.name in descs:
                mod.identity["description"] = descs[ident.name]


def build(librenms: Path, out: Path, base_url: str, jobs: int, only: str | None, extra: Path | None,
          cache: Path) -> dict:
    t0 = time.time()
    mibs_root = librenms / "mibs"
    extra = extra if extra and extra.is_dir() else None
    with phase("Finding MIB files"):
        all_files = discover(mibs_root, extra)
        available = {m for f in all_files for m in f.modules}
        files = [f for f in all_files if fnmatch.fnmatch(f.rel, only)] if only else all_files
        print(f"  {len(files):,} files" + (f" (of {len(all_files):,}, --only {only})" if only else ""))
    with phase("Parsing MIBs"):
        modules = parse_all(files, mibs_root, extra, jobs, cache / "parsed", available, all_files)
        tiers = Counter(m.tier for m in modules)
        print(f"  {len(modules):,} modules: " + ", ".join(f"{n:,} {t}" for t, n in tiers.most_common()))
    with phase("Restoring description layout"):
        # cheap, so done on every build after the parse cache: layout changes never force a re-parse
        restore_descriptions(modules, mibs_root, extra)
    with phase("Resolving symbols and vendors"):
        table = SymbolTable(modules)
        enterprises = load_enterprises(cache / "iana")
        vendors = {m.id: classify(m, enterprises) for m in modules}
    with phase("Collecting example values"):
        snmprec_dir = librenms / "tests" / "snmpsim"
        examples = collect_examples(snmprec_dir, table) if snmprec_dir.is_dir() else {}
        print(f"  {len(examples):,} MIBs with examples")
    with phase("Collecting LibreNMS usage"):
        usage = collect_usage(librenms, modules)
        print(f"  {len(usage.modules):,} MIBs used by LibreNMS")
    with phase("Reading MIB file dates"):
        dates = {"": file_dates(mibs_root), EXTERNAL_DIR: file_dates(extra) if extra else {}}
        modified = {m.id: d for m in modules if (d := dates[EXTERNAL_DIR if m.dir == EXTERNAL_DIR else ""].get(m.file))}
        print(f"  {len(modified):,} MIBs with a date")
    cfg = SiteConfig(base_url.rstrip("/"), _sha(librenms), dt.date.today().isoformat())
    safe_clean(out)
    with phase("Rendering pages"):
        render_site(out, modules, table, vendors, examples, cfg, jobs, usage, modified)
    with phase("Writing search index"):
        write_search_index(out / "data" / "search", modules, vendors)
    stats = {"modules": len(modules), "objects": sum(len(m.objects) for m in modules),
             "tiers": dict(tiers), "seconds": round(time.time() - t0)}
    print(f"Built {out} in {stats['seconds']}s: {stats['modules']:,} MIBs, {stats['objects']:,} objects")
    return stats
