"""`actions-cache-prune.mjs` and `actions-budget-figures.sh cache-headroom`, run for real.

Each case runs the real script, the way a composed repository's workflow does, against a local
HTTP server standing in for GitHub at its REST boundary and replaying a recorded listing
(`external/fixtures/cache-listings/`). Keys are matched by prefix, pages cut by `per_page` and
entries tied on the sort key reordered between pages, as GitHub does, so a script that reads
one page less, or pages by a key entries share, sees what it would on GitHub.
"""

# llmlint: ignore[e2e_not_mocked] Only GitHub's REST service is doubled, over a recorded real listing; the scripts under test run unchanged as subprocesses.
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import threading
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse

import pytest

SKILL = Path(__file__).resolve().parents[2]
PRUNE = SKILL / "assets/ci/actions-cache-prune.mjs"
FIGURES = SKILL / "assets/budgets/actions-budget-figures.sh"
RECORDED = json.loads(
    (SKILL / "tests/external/fixtures/cache-listings/onetaskgraph.json").read_text()
)
REPO = "example/project"
MAIN = "refs/heads/main"
GIB = 1_073_741_824
LINUX = "v0-rust-test-linux-"


class Answer(NamedTuple):
    """One listing answer: the key filter it was asked, its total, the entries it held."""

    key: str
    total: int
    ids: tuple[int, ...]


def key_matches(key: str, prefix: str) -> bool:
    """GitHub's key filter, read live: a case-sensitive prefix where `_` and `%` are wildcards."""
    pattern = "".join(
        "." if c == "_" else ".*" if c == "%" else re.escape(c) for c in prefix
    )
    return re.match(pattern, key, re.DOTALL) is not None


class Github(ThreadingHTTPServer):
    """GitHub's cache endpoints over an entry list: only what these scripts call."""

    def __init__(self, entries: list[dict], limit: int = 10) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.entries = entries
        self.limit = limit
        self.reads: list[str] = []
        self.served: Counter[int] = Counter()
        self.answers: list[Answer] = []
        self.requests_answered = 0
        self.never_listed: set[int] = set()
        self.deleted: list[int] = []
        self.fail_delete: dict[int, int] = {}
        self.fail_page: dict[int, int] = {}
        self.limit_status = 200
        self.remaining = iter(range(4000, 0, -7))
        self.total_count_extra = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class _Handler(BaseHTTPRequestHandler):
    server: Github

    def log_message(self, *_args) -> None:
        pass

    def _reply(self, status: int, body: object = None) -> None:
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-RateLimit-Remaining", str(next(self.server.remaining)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        server = self.server
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        server.reads.append(self.path)
        if self.headers.get("Authorization") != "Bearer test-token":
            return self._reply(401, {"message": "Bad credentials"})
        if parsed.path == f"/repos/{REPO}/actions/cache/storage-limit":
            if server.limit_status != 200:
                return self._reply(server.limit_status, {"message": "refused"})
            return self._reply(200, {"max_cache_size_gb": server.limit})
        if parsed.path != f"/repos/{REPO}/actions/caches":
            return self._reply(404, {"message": "No such route: " + self.path})
        page = int(query.get("page", ["1"])[0])
        if page in server.fail_page:
            return self._reply(server.fail_page[page], {"message": "refused"})
        prefix = query.get("key", [""])[0]
        matching = [e for e in server.entries if key_matches(str(e["key"]), prefix)]
        sort = query["sort"][0]
        # GitHub promises no order among ties, and onepipeline-ui's run 42365784060 read one
        # entry twice and missed another across same-size pages. So ties differ per request.
        server.requests_answered += 1
        nonce = server.requests_answered
        matching.sort(
            key=lambda e: (
                e[sort],
                hashlib.sha256(f"{nonce}:{e['id']}".encode()).hexdigest(),
            ),
            reverse=query["direction"][0] == "desc",
        )
        size = int(query["per_page"][0])
        total = len(matching) + server.total_count_extra
        matching = [e for e in matching if e["id"] not in server.never_listed]
        listed = matching[(page - 1) * size : page * size]
        server.served.update(e["id"] for e in listed)
        server.answers.append(Answer(prefix, total, tuple(e["id"] for e in listed)))
        self._reply(
            200,
            {
                "total_count": total,
                "actions_caches": listed,
            },
        )

    def do_DELETE(self) -> None:
        server = self.server
        match = re.fullmatch(rf"/repos/{REPO}/actions/caches/(\d+)", self.path)
        assert match, self.path
        entry_id = int(match.group(1))
        if entry_id in server.fail_delete:
            return self._reply(server.fail_delete[entry_id], {"message": "refused"})
        server.deleted.append(entry_id)
        server.entries[:] = [e for e in server.entries if e["id"] != entry_id]
        self._reply(204)


@pytest.fixture
def serve() -> Iterator:
    running: list[tuple[Github, threading.Thread]] = []

    def start(entries: list[dict], limit: int = 10) -> Github:
        server = Github(copy.deepcopy(entries), limit)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return server

    yield start
    for server, thread in running:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def entry(
    entry_id: int,
    key: str,
    ref: str = MAIN,
    size: int = 1000,
    age_minutes: int = 0,
) -> dict:
    when = datetime(2026, 11, 6, tzinfo=timezone.utc) - timedelta(minutes=age_minutes)
    stamp = when.isoformat().replace("+00:00", "Z")
    return {
        "id": entry_id,
        "key": key,
        "ref": ref,
        "size_in_bytes": size,
        "created_at": stamp,
        "last_accessed_at": stamp,
        "version": "0" * 64,
    }


def prune(server: Github, tmp_path: Path, *families: str, **extra: str):
    args = [item for family in families for item in ("--family", family)]
    summary = tmp_path / "summary.md"
    return subprocess.run(
        ["node", str(PRUNE), *args],
        env={
            **os.environ,
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": server.url,
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_STEP_SUMMARY": str(summary),
            **extra,
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def staged_figures(tmp_path: Path) -> str:
    """The figures script beside its sibling, as a composed repository's scripts/ holds them."""
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    for source in (FIGURES, SKILL / "assets/ci/nx-remote-cache.mjs"):
        (scripts / source.name).write_bytes(source.read_bytes())
    return str(scripts / FIGURES.name)


def headroom(server: Github, tmp_path: Path, *families: str):
    """The figure and detail the real command wrote, and how it ended."""
    result = tmp_path / "result.json"
    result.unlink(missing_ok=True)
    args = [item for family in families for item in ("--family", family)]
    done = subprocess.run(
        ["bash", staged_figures(tmp_path), "cache-headroom", *args],
        env={
            **os.environ,
            "ONEBUDGETSPEC_RESULT": str(result),
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": server.url,
            "GITHUB_REPOSITORY": REPO,
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done, json.loads(result.read_text()) if result.exists() else None


def recorded_family(prefix: str) -> list[dict]:
    return [e for e in RECORDED["actions_caches"] if e["key"].startswith(prefix)]


def test_the_recorded_listing_holds_what_these_cases_rely_on():
    family = recorded_family(LINUX)
    assert {e["ref"] for e in family} >= {MAIN}
    assert any(e["ref"].startswith("refs/pull/") for e in family)
    assert len(RECORDED["actions_caches"]) > 300  # more than one page
    assert any("tags" in e["ref"] for e in RECORDED["actions_caches"])
    assert any(e["key"].startswith("nx-cache-") for e in RECORDED["actions_caches"])


def test_the_newest_main_copy_survives_and_every_other_ref_goes(serve, tmp_path):
    # The recorded family on main and a pull request, with a tag's copy the way a tag's
    # ref reads on GitHub. The newest entry of all is a pull request's, which must go.
    family = recorded_family(LINUX)
    newest_main = max(
        (e for e in family if e["ref"] == MAIN), key=lambda e: e["created_at"]
    )
    tag = {
        **family[0],
        "id": 91,
        "ref": "refs/heads/refs/tags/v0.14.5",
        "created_at": "2026-11-09T00:00:00.000000Z",
    }
    pull = {
        **family[0],
        "id": 92,
        "ref": "refs/pull/7/merge",
        "created_at": "2026-11-10T00:00:00.000000Z",
    }
    other = entry(93, "v0-rust-test-windows-Windows_NT-x64-a-b", size=5)
    result = entry(94, "nx-cache-123", size=5)
    server = serve([*family, tag, pull, other, result])
    done = prune(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert {e["id"] for e in server.entries} == {newest_main["id"], 93, 94}
    gone = [e for e in [*family, tag, pull] if e["id"] != newest_main["id"]]
    assert sorted(server.deleted) == sorted(e["id"] for e in gone)
    line = (
        f"{LINUX}: kept {newest_main['key']}, deleted {len(gone)}, "
        f"freed {sum(e['size_in_bytes'] for e in gone)} bytes"
    )
    assert done.stdout.splitlines() == [line]
    assert (tmp_path / "summary.md").read_text().splitlines() == [line]
    # The contract's listing request, prefix and order included.
    query = parse_qs(urlparse(server.reads[0]).query)
    assert query["key"] == [LINUX]
    assert (query["sort"], query["direction"]) == (["created_at"], ["desc"])
    assert query["per_page"] == ["100"]


def test_every_page_of_a_family_is_read(serve, tmp_path):
    copies = [
        entry(i, f"{LINUX}a-{i:08x}", ref=f"refs/pull/{i}/merge", age_minutes=i)
        for i in range(1, 251)
    ]
    keeper = entry(999, f"{LINUX}a-main", age_minutes=9999)
    server = serve([*copies, keeper])
    done = prune(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert [e["id"] for e in server.entries] == [999]
    assert len(server.reads) == 3


def test_a_family_with_no_main_copy_loses_nothing_and_warns(serve, tmp_path):
    family = [e for e in recorded_family(LINUX) if e["ref"] != MAIN]
    assert family
    server = serve(family)
    done = prune(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert server.deleted == []
    assert "::warning::" in done.stderr and "no copy on main" in done.stderr
    assert f"{LINUX}: kept nothing, deleted 0, freed 0 bytes" in done.stdout


@pytest.mark.parametrize(
    "family", ["nx-cache-", "nx-cache-123", ""], ids=["nx", "nx-key", "empty"]
)
def test_a_refused_family_is_a_usage_error_that_touches_nothing(
    serve, tmp_path, family
):
    server = serve([entry(1, "nx-cache-123"), entry(2, "nx-cache-456")])
    done = prune(server, tmp_path, family)
    assert done.returncode == 2, done.stdout
    assert "usage:" in done.stderr
    assert server.reads == [] and server.deleted == []


@pytest.mark.parametrize(
    "args",
    [[], ["--family"], ["--bogus", "x"], ["--family", LINUX, "--repo", "no-slash"]],
    ids=["none", "no-value", "unknown", "bad-repo"],
)
def test_other_invalid_invocations_are_usage_errors(tmp_path, args):
    done = subprocess.run(
        ["node", str(PRUNE), *args],
        env={**os.environ, "GITHUB_REPOSITORY": REPO},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2, done.stdout
    assert "usage:" in done.stderr


def test_a_failed_delete_warns_and_carries_on_to_the_rest(serve, tmp_path):
    keeper = entry(1, f"{LINUX}a-main", size=10)
    refused = entry(2, f"{LINUX}a-old", size=20, age_minutes=5)
    deleted = entry(3, f"{LINUX}a-older", size=40, age_minutes=10)
    server = serve([keeper, refused, deleted])
    server.fail_delete[2] = 403
    done = prune(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert "::warning::" in done.stderr and "HTTP 403" in done.stderr
    assert server.deleted == [3]
    assert f"{LINUX}: kept {LINUX}a-main, deleted 1, freed 40 bytes" in done.stdout


def test_a_delete_answering_404_counts_as_gone(serve, tmp_path):
    keeper = entry(1, f"{LINUX}a-main", size=10)
    server = serve([keeper, entry(2, f"{LINUX}a-old", size=20, age_minutes=5)])
    server.fail_delete[2] = 404
    done = prune(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert f"{LINUX}: kept {LINUX}a-main, deleted 1, freed 20 bytes" in done.stdout


def test_a_failed_read_warns_deletes_nothing_and_goes_on_to_the_next_family(
    serve, tmp_path
):
    # The first family's second page fails, so what its first page showed is not all of it.
    first = [
        entry(i, f"{LINUX}a-{i:08x}", ref=f"refs/pull/{i}/merge", age_minutes=i)
        for i in range(1, 121)
    ]
    other = "v0-rust-test-macos-"
    keeper = entry(500, f"{other}a-main", size=10)
    old = entry(501, f"{other}a-old", size=20, age_minutes=5)
    server = serve([*first, keeper, old])
    server.fail_page[2] = 500
    done = prune(server, tmp_path, LINUX, other)
    assert done.returncode == 0, done.stderr
    assert "::warning::" in done.stderr and "HTTP 500" in done.stderr
    assert server.deleted == [501]
    assert f"{LINUX}: kept unknown, deleted 0, freed 0 bytes" in done.stdout
    assert f"{other}: kept {other}a-main, deleted 1, freed 20 bytes" in done.stdout


@pytest.mark.parametrize(
    "malformed",
    [{"id": "../../../contents"}, {"id": -1}, {"ref": None}, {"size_in_bytes": "big"}],
    ids=["path-in-id", "negative-id", "no-ref", "size-not-a-number"],
)
def test_a_malformed_entry_warns_deletes_nothing_and_goes_on_to_the_next_family(
    serve, tmp_path, malformed
):
    keeper = entry(1, f"{LINUX}a-main", size=10)
    old = entry(2, f"{LINUX}a-old", size=20, age_minutes=5)
    bad = {**entry(3, f"{LINUX}a-bad", size=30, age_minutes=9), **malformed}
    other = "v0-rust-test-macos-"
    other_keeper = entry(500, f"{other}a-main", size=10)
    other_old = entry(501, f"{other}a-old", size=20, age_minutes=5)
    server = serve([keeper, old, bad, other_keeper, other_old])
    done = prune(server, tmp_path, LINUX, other)
    assert done.returncode == 0, done.stderr
    assert "::warning::" in done.stderr and "malformed entry" in done.stderr
    assert server.deleted == [501]
    assert f"{LINUX}: kept unknown, deleted 0, freed 0 bytes" in done.stdout


def test_an_unreachable_host_warns_and_exits_zero(tmp_path):
    done = subprocess.run(
        ["node", str(PRUNE), "--family", LINUX],
        env={
            **os.environ,
            "GITHUB_API_URL": "http://127.0.0.1:1",
            "GITHUB_REPOSITORY": REPO,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0
    assert "::warning::" in done.stderr


def test_a_token_is_never_sent_over_a_host_that_is_not_https_or_loopback(tmp_path):
    done = subprocess.run(
        ["node", str(PRUNE), "--family", LINUX],
        env={
            **os.environ,
            "GITHUB_API_URL": "http://example.com",
            "GITHUB_REPOSITORY": REPO,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2
    assert "HTTPS or loopback" in done.stderr


def family_of(key: str) -> str:
    return re.sub(r"(-[0-9a-f]{6,})+$", "", key)


def expected_figure(entries: list[dict], limit: int, *families: str) -> float:
    counted = 0
    for e in entries:
        named = next((f for f in families if e["key"].startswith(f)), None)
        if named is None:
            counted += e["size_in_bytes"]
    for family in families:
        members = [e for e in entries if e["key"].startswith(family)]
        if members:
            counted += max(members, key=lambda e: e["created_at"])["size_in_bytes"]
    return round((limit * GIB - counted) / 1_000_000_000, 3)


def test_the_recorded_listing_measures_every_entry_on_every_page(serve, tmp_path):
    entries = RECORDED["actions_caches"]
    server = serve(entries)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    # The figure and the detail, computed here from the listing the server answered.
    assert result["value"] == expected_figure(entries, 10)
    detail = result["detail"]
    results = [e for e in entries if e["key"].startswith("nx-cache-")]
    assert (
        f"results: {len(results)} entries, "
        f"{sum(e['size_in_bytes'] for e in results)} bytes" in detail
    )
    non_results: dict[str, int] = {}
    for e in entries:
        if not e["key"].startswith("nx-cache-"):
            family = family_of(e["key"])
            non_results[family] = non_results.get(family, 0) + e["size_in_bytes"]
    for family, bytes_ in non_results.items():
        assert f"non-result {family}: {bytes_} bytes" in detail
    assert "allowance: 10737418240 bytes (max_cache_size_gb 10)" in detail
    assert "oldest result age: " in detail
    # The contract's order, and every page of it. 398 entries are four pages.
    listing = [urlparse(r) for r in server.reads if "actions/caches" in r]
    assert len(listing) == 4
    query = parse_qs(listing[0].query)
    assert (query["sort"], query["direction"]) == (["created_at"], ["desc"])
    assert not any("cache/usage" in r for r in server.reads)
    # The storage limit and the four pages, and the lowest counter any of the five carried.
    assert "requests: 5;" in detail
    assert "lowest X-RateLimit-Remaining: 3972" in detail


def test_a_non_result_on_the_last_page_and_results_above_it_all_count(serve, tmp_path):
    # Results fill the earlier pages; an older cache of another kind sorts to the last.
    results = [
        entry(i, f"nx-cache-{i}", size=1_000_000 + i, ref=MAIN, age_minutes=i)
        for i in range(1, 251)
    ]
    tiny = entry(999, "setup-uv-2-x86-pruned-0123abcd", size=777, age_minutes=999)
    server = serve([*results, tiny])
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure([*results, tiny], 10)
    assert "non-result setup-uv-2-x86-pruned: 777 bytes" in result["detail"]
    assert "results: 250 entries" in result["detail"]
    # Without the last page the figure would differ by that entry, so prove it was read:
    assert any("page=3" in read for read in server.reads)


def test_an_allowance_of_10_is_10_737_gigabytes_before_anything_is_subtracted(
    serve, tmp_path
):
    done, result = headroom(serve([]), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == 10.737
    assert "results: 0 entries, 0 bytes; oldest result age: n/a" in result["detail"]


def test_a_different_allowance_scales_in_gib(serve, tmp_path):
    done, result = headroom(serve([], limit=25), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == round(25 * GIB / 1_000_000_000, 3)
    assert f"allowance: {25 * GIB} bytes (max_cache_size_gb 25)" in result["detail"]
    assert "stated" not in result["detail"]


def test_bytes_over_the_line_read_negative_rather_than_error(serve, tmp_path):
    over = [entry(1, "v0-rust-test-linux-Linux-x64-aa11bb22", size=12_000_000_000)]
    done, result = headroom(serve(over), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == round((10 * GIB - 12_000_000_000) / 1_000_000_000, 3)
    assert result["value"] < 0


def test_a_named_familys_older_copy_leaves_the_figure_and_reaches_the_detail(
    serve, tmp_path
):
    newest = entry(1, f"{LINUX}Linux-x64-aa11bb22-cc33dd44", size=2_000_000_000)
    older = entry(
        2, f"{LINUX}Linux-x64-aa11bb22-ee55ff66", size=3_000_000_000, age_minutes=60
    )
    server = serve([newest, older])
    done, named = headroom(server, tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert named["value"] == round((10 * GIB - 2_000_000_000) / 1_000_000_000, 3)
    assert (
        f"family {LINUX}: newest {newest['key']}; 1 older copy, 3000000000 bytes, "
        "not counted" in named["detail"]
    )
    # The same listing with the family unnamed counts both copies in full.
    done, unnamed = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert unnamed["value"] == round((10 * GIB - 5_000_000_000) / 1_000_000_000, 3)
    assert "older copy" not in unnamed["detail"]


def test_a_named_family_with_three_entries_reads_zero_and_is_named(serve, tmp_path):
    # The recorded onetaskgraph listing's Linux family: two on main, one on a pull request.
    entries = RECORDED["actions_caches"]
    family = recorded_family(LINUX)
    assert len(family) == 3
    done, result = headroom(serve(entries), tmp_path, LINUX)
    assert done.returncode == 0, done.stderr
    assert result["value"] == 0
    assert f"family {LINUX} holds 3 entries" in result["detail"]
    assert f"measured {expected_figure(entries, 10, LINUX)}" in result["detail"]


@pytest.mark.parametrize("status", [403, 429, 500])
def test_a_listing_that_fails_partway_writes_no_figure(serve, tmp_path, status):
    server = serve(RECORDED["actions_caches"])
    server.fail_page[3] = status
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert f"HTTP {status}" in done.stderr


@pytest.mark.parametrize(
    "malformed",
    [{"id": None}, {"key": 7}, {"created_at": "yesterday"}],
    ids=["no-id", "key-not-text", "no-date"],
)
def test_a_malformed_entry_writes_no_figure(serve, tmp_path, malformed):
    server = serve(
        [entry(1, f"{LINUX}a-main", size=10), {**entry(2, "other-a"), **malformed}]
    )
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "malformed entry" in done.stderr


def test_a_storage_limit_answered_403_uses_the_stated_allowance_and_says_so(
    serve, tmp_path
):
    entries = RECORDED["actions_caches"]
    # The double's own limit differs, so only the stated allowance can give this figure.
    server = serve(entries, limit=25)
    server.limit_status = 403
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    detail = result["detail"]
    assert (
        "allowance: 10737418240 bytes (GitHub's stated 10 GiB allowance; "
        "actions/cache/storage-limit answered HTTP 403), 10.73741824 GB" in detail
    )
    assert "max_cache_size_gb" not in detail
    # The refused read and the four pages.
    assert "requests: 5;" in detail


def test_a_storage_limit_403_followed_by_a_refused_listing_writes_no_figure(
    serve, tmp_path
):
    # A rate-limited token is answered 403 on both, and the listing still has to be read.
    server = serve(RECORDED["actions_caches"])
    server.limit_status = 403
    server.fail_page[1] = 403
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "actions/caches?sort=created_at" in done.stderr


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_a_storage_limit_failing_any_other_way_writes_no_figure(
    serve, tmp_path, status
):
    server = serve(RECORDED["actions_caches"])
    server.limit_status = status
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert f"actions/cache/storage-limit answered HTTP {status}" in done.stderr


def test_a_storage_limit_with_no_figure_writes_no_figure(serve, tmp_path):
    server = serve([], limit=0)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "storage limit carries no max_cache_size_gb" in done.stderr


# 280 is the size of the listing the headroom refused at 279 of 280; 200 ends on a full page.
@pytest.mark.parametrize("count", [280, 200])
def test_entries_of_one_size_across_pages_are_each_read_exactly_once(
    serve, tmp_path, count
):
    entries = [
        entry(i, f"nx-cache-{i:04x}", size=583, age_minutes=i)
        for i in range(1, count + 1)
    ]
    server = serve(entries)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert server.served == Counter(e["id"] for e in entries)
    assert result["value"] == expected_figure(entries, 10)
    assert f"results: {count} entries, {count * 583} bytes" in result["detail"]


def tied(entry_id: int, key: str, ref: str = MAIN) -> dict:
    """An entry sharing its size and creation timestamp with every other `tied` entry."""
    return entry(entry_id, key, ref=ref, size=583)


def whole_answers(server: Github) -> list[Answer]:
    """The answers holding all they matched, in one response no page order can disturb."""
    return [a for a in server.answers if a.total == len(a.ids)]


@pytest.mark.parametrize("count", [280, 200])
def test_entries_sharing_size_and_creation_time_across_pages_all_count_once(
    serve, tmp_path, count
):
    entries = [tied(i, f"nx-cache-{i:04x}") for i in range(1, count + 1)]
    server = serve(entries)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    assert f"results: {count} entries, {count * 583} bytes" in result["detail"]
    # Each entry came in exactly one whole answer, and each repeat is a discarded partial one.
    held = Counter(i for a in whole_answers(server) for i in a.ids)
    assert held == Counter(e["id"] for e in entries)


@pytest.mark.parametrize("key", ["zz-hidden", "%-hidden", "é-hidden"])
def test_a_key_no_answer_happens_to_show_is_found_by_naming_its_first_character(
    serve, tmp_path, key
):
    # Older than every tie, so no answer over the whole listing reaches it.
    hidden = entry(999, key, size=583, age_minutes=60)
    entries = [tied(i, f"nx-cache-{i:04x}") for i in range(1, 281)] + [hidden]
    server = serve(entries)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    assert f"non-result {key}: 583 bytes" in result["detail"]


def test_a_wildcard_in_a_key_loses_nothing_and_counts_nothing_twice(serve, tmp_path):
    # `_` matches any one character on GitHub, so `nx_a` also answers for `nxba`.
    # `%` matches any run, so `nx%` answers for all of them and narrows nothing by itself.
    entries = [
        tied(i, f"{stem}-{i:04x}")
        for stem, ids in (
            ("nx_a", range(1, 121)),
            ("nxba", range(121, 241)),
            ("nx%c", range(241, 361)),
        )
        for i in ids
    ]
    done, result = headroom(serve(entries), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    for e in entries:
        assert f"non-result {e['key']}: 583 bytes\n" in result["detail"]


def test_a_key_that_is_a_prefix_of_many_others_still_counts(serve, tmp_path):
    entries = [tied(1, "nx-cache-")] + [
        tied(i, f"nx-cache-{i:04x}") for i in range(2, 282)
    ]
    done, result = headroom(serve(entries), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    assert "results: 281 entries" in result["detail"]


def test_one_key_on_many_refs_counts_every_copy_the_rereads_show(serve, tmp_path):
    # No prefix divides these. Rereading shows them because this double's order moves.
    entries = [
        tied(i, "nx-cache-shared", ref=f"refs/pull/{i}/merge") for i in range(1, 121)
    ]
    done, result = headroom(serve(entries), tmp_path)
    assert done.returncode == 0, done.stderr
    assert result["value"] == expected_figure(entries, 10)
    assert "results: 120 entries" in result["detail"]


def test_one_key_on_many_refs_with_a_copy_never_shown_writes_no_figure(serve, tmp_path):
    # GitHub promises no moving order, so this is the case the API cannot resolve: refused.
    entries = [
        tied(i, "nx-cache-shared", ref=f"refs/pull/{i}/merge") for i in range(1, 121)
    ]
    server = serve(entries)
    server.never_listed.add(7)
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "listing is incomplete: 119 of 120 entries read" in done.stderr


def test_a_prefix_answer_short_of_its_own_total_writes_no_figure(serve, tmp_path):
    server = serve([tied(i, f"nx-cache-{i:04x}") for i in range(1, 281)])
    server.total_count_extra = 1
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "listing is incomplete" in done.stderr


def test_a_listing_shorter_than_its_total_writes_no_figure(serve, tmp_path):
    server = serve(RECORDED["actions_caches"])
    server.total_count_extra = 5
    done, result = headroom(server, tmp_path)
    assert done.returncode == 1
    assert result is None
    assert "listing is incomplete" in done.stderr


@pytest.mark.parametrize(
    "args", [["--family"], ["--repo", "no-slash"], ["--bogus", "x"]]
)
def test_invalid_invocations_exit_2_writing_nothing(serve, tmp_path, args):
    result = tmp_path / "result.json"
    done = subprocess.run(
        ["bash", staged_figures(tmp_path), "cache-headroom", *args],
        env={
            **os.environ,
            "ONEBUDGETSPEC_RESULT": str(result),
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": serve([]).url,
            "GITHUB_REPOSITORY": REPO,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2, done.stdout
    assert not result.exists()


def test_it_needs_no_onevcs_and_refuses_with_no_token(serve, tmp_path):
    result = tmp_path / "result.json"
    env = {
        **os.environ,
        "ONEBUDGETSPEC_RESULT": str(result),
        "GITHUB_API_URL": serve([]).url,
        "GITHUB_REPOSITORY": REPO,
        "ONEVCS": "/nonexistent/onevcs",
    }
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    run = ["bash", staged_figures(tmp_path), "cache-headroom"]
    done = subprocess.run(run, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 1
    assert "no token" in done.stderr
    assert not result.exists()
    env["GITHUB_TOKEN"] = "test-token"
    done = subprocess.run(run, env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(result.read_text())["value"] == 10.737
