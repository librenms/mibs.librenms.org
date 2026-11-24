import json
import shutil
import pytest
from mibsite.build import build, safe_clean


def _fake_librenms(fixtures_dir, tmp_path):
    lib = tmp_path / "librenms"
    shutil.copytree(fixtures_dir / "mibs", lib / "mibs")
    shutil.copytree(fixtures_dir / "snmpsim", lib / "tests" / "snmpsim")
    return lib


def test_safe_clean_refuses_foreign_dir(tmp_path):
    (tmp_path / "important.txt").write_text("x")
    with pytest.raises(SystemExit):
        safe_clean(tmp_path)
    assert (tmp_path / "important.txt").exists()


def test_build_end_to_end(fixtures_dir, tmp_path, monkeypatch):
    monkeypatch.setattr("mibsite.build.load_enterprises", lambda cache: {99999: "Example Org"})
    lib = _fake_librenms(fixtures_dir, tmp_path)
    out = tmp_path / "site"
    stats = build(lib, out, "https://mibs.example.org", jobs=2, only=None, extra=None, cache=tmp_path / "cache")
    assert stats["modules"] >= 6
    page = (out / "mib/GOOD-MIB/index.html").read_text()
    assert "Example Org" in page
    assert (out / "vendors/example-org/index.html").exists()
    assert "widget-1" in page  # snmprec example rendered into the page
    assert not (out / "data/examples").exists()
    man = json.loads((out / "data/search/manifest.json").read_text())
    assert any(m[0] == "GOOD-MIB" for m in man["modules"])
    assert (out / "mib/acme/NOT-A-MIB/index.html").exists()
    # rebuild into the same dir is allowed (marker present)
    build(lib, out, "https://mibs.example.org", jobs=2, only="GOOD-MIB", extra=None, cache=tmp_path / "cache")
    assert not (out / "mib/acme/NOT-A-MIB/index.html").exists()


def test_file_dates(tmp_path):
    import subprocess
    from mibsite.build import file_dates
    def git(*a, date="2024-01-02T00:00:00Z"):
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                       capture_output=True, env=env)
    git("init", "-q")
    (tmp_path / "mibs" / "acme").mkdir(parents=True)
    (tmp_path / "mibs" / "A-MIB").write_text("a")
    (tmp_path / "mibs" / "acme" / "B-MIB").write_text("b")
    git("add", "."); git("commit", "-qm", "one")
    (tmp_path / "mibs" / "acme" / "B-MIB").write_text("b2")
    git("commit", "-qam", "two", date="2025-06-07T00:00:00Z")
    assert file_dates(tmp_path / "mibs") == {"A-MIB": "2024-01-02", "acme/B-MIB": "2025-06-07"}
    assert file_dates(tmp_path / "nowhere") == {}
