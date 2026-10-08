#!/usr/bin/env python3
"""Fetch the sources of the model facts pipeline and report what changed."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.agents.model_facts_common import (  # noqa: E402  # reason: module setup (ROOT or sys.path) must run before the package imports
    EXTRACTED_KEYS,
    NOTICE_DAYS,
    STALE_DAYS,
    FactsError,
    cache_file,
    cache_path,
    cached_text,
    facts_root,
    load_facts,
    load_sources,
    unit_kind,
    unit_names,
    write_json,
    write_text_atomic,
)

USER_AGENT = "pitwall-model-facts/1 (+https://github.com/Buckeyes22/pitwall)"
TIMEOUT_SECONDS = 45
RETRIES = 2
HUB = "https://huggingface.co"
HUB_FILES = (
    "README.md",
    "config.json",
    "generation_config.json",
    "tokenizer_config.json",
    "chat_template.jinja",
)
CONTEXT_KEYS = ("max_position_embeddings", "max_seq_len", "n_positions", "max_sequence_length")
SAMPLING_KEYS = ("temperature", "top_p", "top_k")
LICENSE_NAMES = {"mit": "MIT", "apache-2.0": "Apache-2.0"}
SKIPPED_TAGS = {"script", "style", "nav", "header", "footer", "noscript"}


class FetchResult(NamedTuple):
    status: int
    content_type: str
    body: bytes


Fetcher = Callable[[str], FetchResult]


def http_fetcher(url: str) -> FetchResult:
    """One GET with no credential. A connection error or HTTP 5xx is retried twice."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = FetchResult(0, "", b"")
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                content_type = response.headers.get("Content-Type", "")
                return FetchResult(response.status, content_type, response.read())
        except urllib.error.HTTPError as exc:
            exc.close()
            last = FetchResult(exc.code, "", b"")
            if exc.code < 500:
                return last
        except urllib.error.URLError, TimeoutError, OSError:
            last = FetchResult(0, "", b"")
        if attempt < RETRIES:
            time.sleep(2**attempt)
    return last


BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "br", "caption", "dd", "details",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "html", "li", "main", "ol", "p", "pre", "section", "summary", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
} | SKIPPED_TAGS  # fmt: skip


class _VisibleText(HTMLParser):
    """Visible text with one line per block element, inline text joined by single spaces."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._skipped = 0
        self._pre = 0

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in SKIPPED_TAGS:
            self._skipped += 1
        elif tag == "pre":
            self._pre += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS and tag not in SKIPPED_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in SKIPPED_TAGS and self._skipped:
            self._skipped -= 1
        elif tag == "pre" and self._pre:
            self._pre -= 1

    def handle_data(self, data: str) -> None:
        if self._skipped:
            return
        if self._pre:
            for line in data.split("\n"):
                self._buffer.append(line)
                self._flush()
        else:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def normalise_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept) + "\n"


def looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def _drop_keys(document: Any, keys: frozenset[str]) -> Any:
    if isinstance(document, dict):
        return {key: _drop_keys(value, keys) for key, value in document.items() if key not in keys}
    if isinstance(document, list):
        return [_drop_keys(item, keys) for item in document]
    return document


def normalise(fmt: str, body: bytes, volatile: frozenset[str] = frozenset()) -> str:
    """The text that is hashed, so that page chrome does not cause a false change.

    `volatile` names JSON keys whose value changes on every request, such as a timestamp.
    """
    text = body.decode("utf-8", errors="replace")
    if fmt == "json":
        document = _drop_keys(json.loads(text), volatile)
        return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if fmt == "html":
        parser = _VisibleText()
        parser.feed(text)
        parser.close()
        return normalise_text("\n".join(parser.lines))
    return normalise_text(text)


def source_hash(source: dict[str, Any], text: str) -> str:
    """The hash of a source's normalised text.

    An `unordered` source is hashed with its lines sorted, because some sites render the rows of a
    table in a different order on each request. A reordering is then not a change; an edit still is.
    """
    if source.get("unordered"):
        text = "\n".join(sorted(text.splitlines())) + "\n"
    return content_hash(text)


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Fetched:
    hash: str | None = None
    reason: str = ""
    files: dict[str, str] = field(default_factory=dict)


def fetch_http(source: dict[str, Any], fetcher: Fetcher) -> Fetched:
    result = fetcher(source["url"])
    if result.status != 200:
        return Fetched(reason=f"http {result.status}" if result.status else "no response")
    try:
        text = normalise(source["format"], result.body, frozenset(source.get("volatile", [])))
    except json.JSONDecodeError:
        return Fetched(reason="format")
    raw = result.body.decode("utf-8", errors="replace")
    if source["format"] != "html" and looks_like_html(raw):
        return Fetched(reason="format")
    return Fetched(hash=source_hash(source, text), files={"": text})


def fetch_huggingface(source: dict[str, Any], fetcher: Fetcher) -> Fetched:
    repo = source["repo"]
    result = fetcher(f"{HUB}/api/models/{repo}")
    if result.status != 200:
        return Fetched(reason=f"http {result.status}" if result.status else "no response")
    try:
        record = json.loads(result.body)
        revision = str(record["sha"])
    except json.JSONDecodeError, KeyError, TypeError:
        return Fetched(reason="format")
    files = {"record.json": json.dumps(record, indent=2, sort_keys=True) + "\n"}
    present = {str(item.get("rfilename")) for item in record.get("siblings", [])}
    for name in HUB_FILES:
        if name not in present:
            continue
        part = fetcher(f"{HUB}/{repo}/resolve/{revision}/{name}")
        if part.status == 200:
            files[name] = normalise_text(part.body.decode("utf-8", errors="replace"))
    return Fetched(hash=f"revision:{revision}", files=files)


def fetch_source(source: dict[str, Any], fetcher: Fetcher) -> Fetched:
    if source["fetch"] == "huggingface":
        return fetch_huggingface(source, fetcher)
    return fetch_http(source, fetcher)


def write_cache(unit: str, source_id: str, fetched: Fetched, root: Path) -> None:
    for name, text in fetched.files.items():
        # Every cached file ends in .txt, so no fetched page is ever read as repository Markdown.
        target = (
            cache_path(unit, source_id, root) / f"{name}.txt"
            if name
            else cache_file(unit, source_id, root)
        )
        write_text_atomic(target, text)


def _first_number(document: Any, keys: tuple[str, ...]) -> int | None:
    if not isinstance(document, dict):
        return None
    for key in keys:
        value = document.get(key)
        if type(value) is int and value > 0:
            return value
    for value in document.values():
        found = _first_number(value, keys)
        if found is not None:
            return found
    return None


def extract(repo: str, files: dict[str, str]) -> dict[str, Any]:
    """The facts a Hugging Face record and its small files state, with no judgement."""
    record = json.loads(files["record.json"])
    card = record.get("cardData") or {}
    facts: dict[str, Any] = {"revision": str(record["sha"])}
    total = (record.get("safetensors") or {}).get("total")
    if type(total) is int and total > 0:
        facts["parameters"] = total
    name = card.get("license_name") if card.get("license") == "other" else card.get("license")
    if isinstance(name, str) and name:
        link = card.get("license_link")
        if not isinstance(link, str) or not link:
            url = None
        elif link.startswith("https://"):
            url = link
        else:
            url = f"{HUB}/{repo}/blob/main/{link}"
        facts["license"] = {"name": LICENSE_NAMES.get(name.lower(), name), "url": url}
    window = (
        _first_number(json.loads(files["config.json"]), CONTEXT_KEYS)
        if "config.json" in files
        else None
    )
    if window is None and "tokenizer_config.json" in files:
        limit = json.loads(files["tokenizer_config.json"]).get("model_max_length")
        window = limit if type(limit) is int and 0 < limit < 10**9 else None
    if window is not None:
        facts["contextWindow"] = window
    if "generation_config.json" in files:
        generation = json.loads(files["generation_config.json"])
        sampling = {key: generation[key] for key in SAMPLING_KEYS if key in generation}
        if sampling:
            facts["samplingDefaults"] = sampling
    return facts


def apply_extracted(
    facts: dict[str, Any], source: dict[str, Any], extracted: dict[str, Any]
) -> bool:
    """Set the extracted facts of every model released as this repository. Reviewed facts win."""
    changed = False
    for model in facts.get("models", {}).values():
        if model.get("artifact") != source["repo"]:
            continue
        for key in EXTRACTED_KEYS:
            if key not in extracted:
                continue
            current = model["facts"].get(key)
            if current is not None and current.get("method") != "extracted":
                continue
            own = {"source": source["id"], "locator": EXTRACTED_FROM[key]}
            # Keep what a reviewer added: other evidence (such as a disagreeing page) and the note.
            others = [
                item
                for item in (current or {}).get("evidence", [])
                if item.get("source") != source["id"] or item.get("disagrees")
            ]
            entry = {"value": extracted[key], "method": "extracted", "evidence": [own, *others]}
            if current is not None and "note" in current:
                entry["note"] = current["note"]
            if current != entry:
                model["facts"][key] = entry
                changed = True
    return changed


EXTRACTED_FROM = {
    "revision": "record.json sha",
    "parameters": "record.json safetensors.total",
    "license": "record.json cardData",
    "contextWindow": "config.json",
    "samplingDefaults": "generation_config.json",
}


def name_key(name: str) -> str:
    """Pages spell `grok-4.7` as `grok-4-7`; compare names without case or separators."""
    return re.sub(r"[._]", "-", name.lower())


def known_names(root: Path) -> set[str]:
    """Every model name any facts file already uses, as a name key."""
    names: set[str] = set()
    for unit in unit_names(root):
        facts = load_facts(unit, root) or {}
        for model_id, model in facts.get("models", {}).items():
            spellings = [model_id]
            if "artifact" in model:
                spellings += [model["artifact"], model["artifact"].split("/", 1)[1]]
            for route in model.get("routes", []):
                spellings += [route["model"], route["model"].rsplit("/", 1)[-1]]
            names.update(name_key(spelling) for spelling in spellings)
    return names


def watch_names(watch: dict[str, Any], fetcher: Fetcher) -> list[str] | None:
    """The names a watch entry finds, or None when its page cannot be read."""
    pattern = re.compile(watch["pattern"])
    if watch["kind"] == "huggingface-org":
        url = f"{HUB}/api/models?author={watch['org']}&sort=createdAt&direction=-1&limit=100"
        result = fetcher(url)
        if result.status != 200:
            return None
        try:
            listing = json.loads(result.body)
        except json.JSONDecodeError:
            return None
        found = [str(item["id"]).split("/", 1)[1] for item in listing if "/" in str(item.get("id"))]
        names = [name for name in found if pattern.fullmatch(name)]
    else:
        result = fetcher(watch["url"])
        if result.status != 200:
            return None
        matches = pattern.findall(result.body.decode("utf-8", errors="replace"))
        names = [match if isinstance(match, str) else match[0] for match in matches]
    ignored = [re.compile(item) for item in watch["ignore"]]
    unique: list[str] = []
    for name in names:
        if name not in unique and not any(item.fullmatch(name) for item in ignored):
            unique.append(name)
    return unique


@dataclass
class Report:
    pending: list[dict[str, str]] = field(default_factory=list)
    unreachable: list[dict[str, str]] = field(default_factory=list)
    newModels: list[dict[str, str]] = field(default_factory=list)  # noqa: N815  # reason: field name mirrors the on-disk JSON key
    retiring: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def anything(self) -> bool:
        return bool(
            self.pending or self.unreachable or self.newModels or self.retiring or self.stale
        )


def _days_until(date: str, today: datetime.date) -> int:
    return (datetime.date.fromisoformat(date) - today).days


def run(
    units: list[str],
    *,
    root: Path = ROOT,
    fetcher: Fetcher = http_fetcher,
    today: datetime.date | None = None,
    write: bool = False,
    cache: bool = True,
    watches: bool = True,
) -> Report:
    """Fetch every source of the units and report. Only `write` changes a tracked file."""
    today = today or datetime.date.today()
    report = Report()
    known = known_names(root) if watches else set()
    for unit in units:
        unit_kind(unit)
        sources = load_sources(unit, root)
        facts = load_facts(unit, root)
        sources_changed = facts_changed = False
        for source in sources["sources"]:
            where = {"unit": unit, "source": source["id"], "url": source["url"]}
            if source["fetch"] == "manual":
                report.skipped.append(where)
                reviewed = source.get("reviewedAt")
                if reviewed is None or -_days_until(reviewed, today) > STALE_DAYS:
                    report.stale.append({**where, "reviewedAt": reviewed or "never"})
                continue
            fetched = fetch_source(source, fetcher)
            if fetched.hash is None:
                report.unreachable.append({**where, "reason": fetched.reason})
                continue
            if cache:
                write_cache(unit, source["id"], fetched, root)
            if source.get("observedHash") != fetched.hash or "observedAt" not in source:
                source["observedHash"] = fetched.hash
                source["observedAt"] = today.isoformat()
                sources_changed = True
            if source.get("reviewedHash") != fetched.hash:
                report.pending.append(where)
            if write and facts is not None and source["fetch"] == "huggingface":
                facts_changed |= apply_extracted(
                    facts, source, extract(source["repo"], fetched.files)
                )
        for watch in sources["watch"] if watches else []:
            names = watch_names(watch, fetcher)
            where = {"unit": unit, "watch": watch["id"]}
            if names is None:
                report.unreachable.append({**where, "source": watch["id"], "reason": "watch"})
                continue
            seen = {name_key(name) for name in watch.get("seen", [])}
            for name in names:
                if name_key(name) not in known and name_key(name) not in seen:
                    report.newModels.append({**where, "name": name})
        for model_id, model in (facts or {}).get("models", {}).items():
            retires = model.get("facts", {}).get("retires", {}).get("value")
            if isinstance(retires, str) and _days_until(retires, today) <= NOTICE_DAYS:
                days = _days_until(retires, today)
                report.retiring.append(
                    {"unit": unit, "model": model_id, "retires": retires, "days": days}
                )
        if write and sources_changed:
            write_json(facts_root(root) / unit / "sources.json", sources)
        if write and facts_changed:
            write_json(facts_root(root) / unit / "facts.json", facts)
    return report


def review(
    unit: str, source_ids: list[str], *, root: Path = ROOT, today: datetime.date | None = None
) -> list[str]:
    """Record that these sources were read as they are now. Returns the ids it marked."""
    today = today or datetime.date.today()
    sources = load_sources(unit, root)
    by_id = {source["id"]: source for source in sources["sources"]}
    unknown = [source_id for source_id in source_ids if source_id not in by_id]
    if unknown:
        raise FactsError(f"{unit} has no source {', '.join(unknown)}")
    for source_id in source_ids:
        source = by_id[source_id]
        if source["fetch"] == "manual":
            text = cached_text(unit, source_id, root)
            if text is None:
                raise FactsError(
                    f"{unit} {source_id} is manual: save its text to {cache_file(unit, source_id, root)} first"
                )
            source["observedHash"] = source_hash(source, normalise_text(text))
            source["observedAt"] = today.isoformat()
        elif "observedHash" not in source:
            raise FactsError(f"{unit} {source_id} has not been fetched; run fetch first")
        source["reviewedHash"] = source["observedHash"]
        source["reviewedAt"] = today.isoformat()
    write_json(facts_root(root) / unit / "sources.json", sources)
    return source_ids


def baseline(
    unit: str, *, root: Path = ROOT, fetcher: Fetcher = http_fetcher, keep: list[str] | None = None
) -> list[str]:
    """Acknowledge every name the unit's watches report now, except `keep`. Returns the names acknowledged."""
    kept = {name_key(name) for name in keep or []}
    known = known_names(root)
    sources = load_sources(unit, root)
    acknowledged: list[str] = []
    for watch in sources["watch"]:
        names = watch_names(watch, fetcher)
        if names is None:
            raise FactsError(f"{unit} watch {watch['id']} could not be read")
        seen = list(watch.get("seen", []))
        for name in names:
            key = name_key(name)
            if key in known or key in kept or key in {name_key(item) for item in seen}:
                continue
            seen.append(name)
            acknowledged.append(name)
        watch["seen"] = sorted(seen, key=str.lower)
    write_json(facts_root(root) / unit / "sources.json", sources)
    return acknowledged


def render(report: Report) -> str:
    lines: list[str] = []
    for item in report.pending:
        lines.append(f"pending      {item['unit']} {item['source']}  {item['url']}")
    for item in report.unreachable:
        lines.append(f"unreachable  {item['unit']} {item['source']}  {item['reason']}")
    for item in report.newModels:
        lines.append(f"new model    {item['unit']} {item['name']}  (watch {item['watch']})")
    for entry in report.retiring:
        when = f"in {entry['days']} days" if entry["days"] >= 0 else f"{-entry['days']} days ago"
        lines.append(f"retiring     {entry['unit']} {entry['model']}  {entry['retires']} ({when})")
    for item in report.stale:
        lines.append(f"stale        {item['unit']} {item['source']}  reviewed {item['reviewedAt']}")
    if not lines:
        lines.append("model sources are current")
    return "\n".join(lines) + "\n"


def main(
    argv: list[str] | None = None, *, fetcher: Fetcher = http_fetcher, root: Path | None = None
) -> int:
    root = root or ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    fetch = verbs.add_parser("fetch", help="download sources into the cache")
    fetch.add_argument("units", nargs="*")
    check = verbs.add_parser("check", help="report what changed")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    check.add_argument("--write", action="store_true", help="record what was seen")
    check.add_argument("units", nargs="*")
    mark = verbs.add_parser("review", help="record that sources were read as they are now")
    mark.add_argument("unit")
    mark.add_argument("sources", nargs="+")
    base = verbs.add_parser("baseline", help="acknowledge the names the watches report now")
    base.add_argument("unit")
    base.add_argument(
        "--except", dest="keep", action="append", default=[], help="a name to keep reporting"
    )
    args = parser.parse_args(argv)
    try:
        if args.verb == "baseline":
            for name in baseline(args.unit, root=root, fetcher=fetcher, keep=args.keep):
                print(f"seen         {args.unit} {name}")
            return 0
        if args.verb == "review":
            for source_id in review(args.unit, args.sources, root=root):
                print(f"reviewed     {args.unit} {source_id}")
            return 0
        units = args.units or unit_names(root)
        if args.verb == "fetch":
            report = run(units, root=root, fetcher=fetcher, write=True, watches=False)
            for item in report.skipped:
                print(f"manual       {item['unit']} {item['source']}  {item['url']}")
            for item in report.unreachable:
                print(f"unreachable  {item['unit']} {item['source']}  {item['reason']}")
            print(f"fetched {len(units)} units into {facts_root(root).name}/.cache")
            return 1 if report.unreachable else 0
        report = run(units, root=root, fetcher=fetcher, write=args.write)
    except (FactsError, KeyError, ValueError) as exc:
        print(f"model sources: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(report), indent=2) if args.json else render(report), end="")
    if args.json:
        print()
    return 1 if report.anything() else 0


if __name__ == "__main__":
    raise SystemExit(main())
