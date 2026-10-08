"""The model facts source tool: normalisation, fetch, check, extraction, and watch entries."""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.agents import (
    model_sources as ms,
)

ROOT = Path(__file__).resolve().parents[2]

TODAY = datetime.date(2026, 9, 28)
SHA = "a" * 40


def page(body: str, status: int = 200, content_type: str = "text/markdown") -> ms.FetchResult:
    return ms.FetchResult(status, content_type, body.encode("utf-8"))


class FakeFetcher:
    def __init__(self, pages: dict[str, ms.FetchResult]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> ms.FetchResult:
        self.calls.append(url)
        return self.pages.get(url, ms.FetchResult(404, "", b""))


def hub_pages(repo: str, sha: str = SHA) -> dict[str, ms.FetchResult]:
    record = {
        "sha": sha,
        "safetensors": {"total": 1000},
        "cardData": {
            "license": "other",
            "license_name": "vendor-licence",
            "license_link": "LICENSE",
        },
        "siblings": [{"rfilename": "config.json"}, {"rfilename": "generation_config.json"}],
    }
    return {
        f"{ms.HUB}/api/models/{repo}": page(json.dumps(record), content_type="application/json"),
        f"{ms.HUB}/{repo}/resolve/{sha}/config.json": page(
            json.dumps({"text_config": {"max_position_embeddings": 262144}})
        ),
        f"{ms.HUB}/{repo}/resolve/{sha}/generation_config.json": page(
            json.dumps({"temperature": 1.0, "top_p": 0.95})
        ),
    }


class UnitTree:
    """A temporary repository root holding one family unit."""

    def __init__(
        self, sources: list[dict], facts: dict | None = None, watch: list[dict] | None = None
    ) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.unit = "families/demo"
        base = self.root / "docs/agents/model-facts" / self.unit
        base.mkdir(parents=True)
        document = {
            "schemaVersion": 1,
            "unit": self.unit,
            "sources": sources,
            "notPublished": [],
            "watch": watch or [],
        }
        (base / "sources.json").write_text(json.dumps(document), encoding="utf-8")
        if facts is not None:
            (base / "facts.json").write_text(json.dumps(facts), encoding="utf-8")

    def sources(self) -> dict:
        return json.loads(
            (self.root / "docs/agents/model-facts" / self.unit / "sources.json").read_text(
                encoding="utf-8"
            )
        )

    def facts(self) -> dict:
        return json.loads(
            (self.root / "docs/agents/model-facts" / self.unit / "facts.json").read_text(
                encoding="utf-8"
            )
        )

    def close(self) -> None:
        self.tmp.cleanup()


def http_source(**extra: str) -> dict:
    return {
        "id": "vendor-page",
        "url": "https://docs.example/model.md",
        "kind": "vendor",
        "pageType": "model",
        "fetch": "http",
        "format": "markdown",
        **extra,
    }


class NormaliseTests(unittest.TestCase):
    def test_markdown_line_endings_trailing_space_and_blank_runs(self) -> None:
        self.assertEqual("a\n\nb\n", ms.normalise("markdown", b"a  \r\n\r\n\r\n\r\nb\r\n\r\n"))

    def test_json_is_sorted(self) -> None:
        self.assertEqual(
            ms.normalise("json", b'{"b": 1, "a": 2}'), ms.normalise("json", b'{"a":2,"b":1}')
        )

    def test_an_unordered_source_ignores_row_order_but_not_edits(self) -> None:
        unordered = {"unordered": True}
        one = "| a | 1 |\n| b | 2 |\n"
        two = "| b | 2 |\n| a | 1 |\n"
        edited = "| b | 2 |\n| a | 3 |\n"
        self.assertEqual(ms.source_hash(unordered, one), ms.source_hash(unordered, two))
        self.assertNotEqual(ms.source_hash(unordered, one), ms.source_hash(unordered, edited))
        self.assertNotEqual(ms.source_hash({}, one), ms.source_hash({}, two))

    def test_volatile_json_keys_do_not_change_the_hash(self) -> None:
        one = b'{"data": [{"id": "kimi-k3", "created": 1}]}'
        two = b'{"data": [{"id": "kimi-k3", "created": 2}]}'
        volatile = frozenset({"created"})
        self.assertEqual(ms.normalise("json", one, volatile), ms.normalise("json", two, volatile))
        self.assertNotEqual(ms.normalise("json", one), ms.normalise("json", two))

    def test_html_chrome_does_not_change_the_hash(self) -> None:
        one = b"<html><nav>Menu A</nav><main><h1>Model</h1><p>Context 1M</p></main><script>x=1</script></html>"
        two = b"<html><nav>Menu B, new link</nav><main><h1>Model</h1><p>Context 1M</p></main><footer>2026</footer></html>"
        self.assertEqual(ms.normalise("html", one), ms.normalise("html", two))
        self.assertEqual("Model\nContext 1M\n", ms.normalise("html", one))

    def test_html_inline_text_is_joined_and_lines_break_at_blocks(self) -> None:
        page = (
            b"<html><body><div><div><p>Set <code>effort</code> to <a href='#'><span>high"
            b"</span></a>, or\n  leave it.</p></div></div><ul><li>One <b>bold</b></li>"
            b"<li>Two</li></ul><pre>a = 1\n  b = 2</pre><p>Line<br>break</p>"
            b"<table><tr><td>K</td><td>V</td></tr></table></body></html>"
        )
        self.assertEqual(
            "Set effort to high, or leave it.\nOne bold\nTwo\na = 1\nb = 2\nLine\nbreak\nK\nV\n",
            ms.normalise("html", page),
        )

    def test_html_adjacent_inline_elements_are_not_split(self) -> None:
        page = b"<p><span>con</span><span>text</span> <em>window</em></p>"
        self.assertEqual("context window\n", ms.normalise("html", page))


class FetchTests(unittest.TestCase):
    def test_fetch_writes_the_cache_and_observed_fields(self) -> None:
        tree = UnitTree([http_source()])
        self.addCleanup(tree.close)
        fetcher = FakeFetcher({"https://docs.example/model.md": page("# Model\n")})
        report = ms.run(
            [tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, write=True, watches=False
        )
        source = tree.sources()["sources"][0]
        self.assertEqual(ms.content_hash("# Model\n"), source["observedHash"])
        self.assertEqual("2026-09-28", source["observedAt"])
        cached = tree.root / "docs/agents/model-facts" / ".cache" / tree.unit / "vendor-page.txt"
        self.assertEqual("# Model\n", cached.read_text(encoding="utf-8"))
        self.assertEqual(1, len(report.pending))

    def test_manual_sources_are_skipped(self) -> None:
        tree = UnitTree([http_source(fetch="manual", reviewedAt="2026-09-20")])
        self.addCleanup(tree.close)
        fetcher = FakeFetcher({})
        report = ms.run(
            [tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, write=True, watches=False
        )
        self.assertEqual([], fetcher.calls)
        self.assertEqual(1, len(report.skipped))
        self.assertEqual([], report.stale)

    def test_a_failed_fetch_leaves_the_hashes_alone(self) -> None:
        tree = UnitTree(
            [http_source(observedHash=ms.content_hash("old\n"), observedAt="2026-09-01")]
        )
        self.addCleanup(tree.close)
        fetcher = FakeFetcher({"https://docs.example/model.md": page("", status=503)})
        report = ms.run(
            [tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, write=True, watches=False
        )
        self.assertEqual(ms.content_hash("old\n"), tree.sources()["sources"][0]["observedHash"])
        self.assertEqual("http 503", report.unreachable[0]["reason"])

    def test_html_where_markdown_was_listed_is_a_format_error(self) -> None:
        tree = UnitTree([http_source()])
        self.addCleanup(tree.close)
        fetcher = FakeFetcher(
            {"https://docs.example/model.md": page("<!DOCTYPE html><html></html>")}
        )
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, watches=False)
        self.assertEqual("format", report.unreachable[0]["reason"])


class CheckTests(unittest.TestCase):
    def test_a_reviewed_unchanged_source_is_not_pending(self) -> None:
        digest = ms.content_hash("# Model\n")
        tree = UnitTree(
            [
                http_source(
                    reviewedHash=digest,
                    reviewedAt="2026-09-28",
                    observedHash=digest,
                    observedAt="2026-09-28",
                )
            ]
        )
        self.addCleanup(tree.close)
        fetcher = FakeFetcher({"https://docs.example/model.md": page("# Model\n")})
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, watches=False)
        self.assertFalse(report.anything())

    def test_a_redesigned_site_is_pending_not_a_failure(self) -> None:
        digest = ms.content_hash("# Model\n")
        sources = [
            http_source(id=f"p{n}", url=f"https://docs.example/{n}.md", reviewedHash=digest)
            for n in range(3)
        ]
        tree = UnitTree(sources)
        self.addCleanup(tree.close)
        fetcher = FakeFetcher(
            {f"https://docs.example/{n}.md": page("# New layout\n") for n in range(3)}
        )
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, watches=False)
        self.assertEqual(3, len(report.pending))
        self.assertEqual([], report.unreachable)

    def test_without_write_no_tracked_file_changes(self) -> None:
        tree = UnitTree([http_source()])
        self.addCleanup(tree.close)
        before = tree.sources()
        fetcher = FakeFetcher({"https://docs.example/model.md": page("# Model\n")})
        ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY, watches=False)
        self.assertEqual(before, tree.sources())

    def test_a_manual_source_reviewed_long_ago_is_stale(self) -> None:
        tree = UnitTree([http_source(fetch="manual", reviewedAt="2026-07-01")])
        self.addCleanup(tree.close)
        report = ms.run(
            [tree.unit], root=tree.root, fetcher=FakeFetcher({}), today=TODAY, watches=False
        )
        self.assertEqual("2026-07-01", report.stale[0]["reviewedAt"])

    def test_retiring_models_are_reported_within_the_notice_period(self) -> None:
        facts = {
            "schemaVersion": 1,
            "unit": "families/demo",
            "guidance": [],
            "models": {
                "soon": {
                    "facts": {
                        "retires": {
                            "value": "2026-10-21",
                            "method": "reviewed",
                            "evidence": [{"source": "x", "locator": "y"}],
                        }
                    }
                },
                "later": {
                    "facts": {
                        "retires": {
                            "value": "2027-06-01",
                            "method": "reviewed",
                            "evidence": [{"source": "x", "locator": "y"}],
                        }
                    }
                },
            },
        }
        tree = UnitTree([], facts)
        self.addCleanup(tree.close)
        report = ms.run(
            [tree.unit], root=tree.root, fetcher=FakeFetcher({}), today=TODAY, watches=False
        )
        self.assertEqual(
            [("soon", 23)], [(item["model"], item["days"]) for item in report.retiring]
        )

    def test_exit_codes(self) -> None:
        digest = ms.content_hash("# Model\n")
        tree = UnitTree([http_source(reviewedHash=digest)])
        self.addCleanup(tree.close)
        current = FakeFetcher({"https://docs.example/model.md": page("# Model\n")})
        changed = FakeFetcher({"https://docs.example/model.md": page("# Changed\n")})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(0, ms.main(["check"], fetcher=current, root=tree.root))
            self.assertEqual(1, ms.main(["check"], fetcher=changed, root=tree.root))
            self.assertEqual(2, ms.main(["check", "not-a-unit"], fetcher=current, root=tree.root))
        self.assertIn("pending      families/demo vendor-page", out.getvalue())
        self.assertIn("not a unit name", err.getvalue())


class ExtractTests(unittest.TestCase):
    def test_the_five_keys_and_reviewed_facts_win(self) -> None:
        repo = "vendor/Demo-1"
        evidence = [{"source": "card", "locator": "Specifications"}]
        facts = {
            "schemaVersion": 1,
            "unit": "families/demo",
            "guidance": [],
            "models": {
                "demo-1": {
                    "artifact": repo,
                    "facts": {
                        "contextWindow": {
                            "value": 1048576,
                            "method": "reviewed",
                            "evidence": evidence,
                        }
                    },
                }
            },
        }
        source = {
            "id": "hf-demo",
            "url": f"https://huggingface.co/{repo}",
            "kind": "artifact",
            "pageType": "model",
            "fetch": "huggingface",
            "format": "json",
            "repo": repo,
        }
        tree = UnitTree([source], facts)
        self.addCleanup(tree.close)
        ms.run(
            [tree.unit],
            root=tree.root,
            fetcher=FakeFetcher(hub_pages(repo)),
            today=TODAY,
            write=True,
            watches=False,
        )
        model = tree.facts()["models"]["demo-1"]["facts"]
        self.assertEqual(SHA, model["revision"]["value"])
        self.assertEqual(1000, model["parameters"]["value"])
        self.assertEqual(
            {"name": "vendor-licence", "url": f"{ms.HUB}/{repo}/blob/main/LICENSE"},
            model["license"]["value"],
        )
        self.assertEqual({"temperature": 1.0, "top_p": 0.95}, model["samplingDefaults"]["value"])
        self.assertEqual("extracted", model["revision"]["method"])
        self.assertEqual(
            1048576, model["contextWindow"]["value"], "a reviewed fact is never overwritten"
        )
        self.assertEqual(f"revision:{SHA}", tree.sources()["sources"][0]["observedHash"])
        cached = sorted(
            path.name
            for path in (
                tree.root / "docs/agents/model-facts" / ".cache" / tree.unit / "hf-demo"
            ).iterdir()
        )
        self.assertEqual(
            ["config.json.txt", "generation_config.json.txt", "record.json.txt"], cached
        )

    def test_extraction_keeps_a_reviewers_note_and_other_evidence(self) -> None:
        repo = "vendor/Demo-1"
        disagreeing = {
            "source": "hf-demo",
            "locator": "README.md",
            "states": 0.9,
            "disagrees": True,
        }
        facts = {
            "schemaVersion": 1,
            "unit": "families/demo",
            "guidance": [],
            "models": {
                "demo-1": {
                    "artifact": repo,
                    "facts": {
                        "samplingDefaults": {
                            "value": {"temperature": 1.0},
                            "method": "extracted",
                            "note": "The card says top_p 0.9.",
                            "evidence": [
                                {"source": "hf-demo", "locator": "generation_config.json"},
                                disagreeing,
                            ],
                        }
                    },
                }
            },
        }
        source = {
            "id": "hf-demo",
            "url": f"https://huggingface.co/{repo}",
            "kind": "artifact",
            "pageType": "model",
            "fetch": "huggingface",
            "format": "json",
            "repo": repo,
        }
        tree = UnitTree([source], facts)
        self.addCleanup(tree.close)
        ms.run(
            [tree.unit],
            root=tree.root,
            fetcher=FakeFetcher(hub_pages(repo)),
            today=TODAY,
            write=True,
            watches=False,
        )
        sampling = tree.facts()["models"]["demo-1"]["facts"]["samplingDefaults"]
        self.assertEqual({"temperature": 1.0, "top_p": 0.95}, sampling["value"])
        self.assertEqual("The card says top_p 0.9.", sampling["note"])
        self.assertIn(disagreeing, sampling["evidence"])

    def test_a_missing_file_leaves_the_fact_absent(self) -> None:
        record = {"sha": SHA, "cardData": {"license": "mit"}}
        facts = ms.extract("vendor/Demo-1", {"record.json": json.dumps(record)})
        self.assertEqual({"revision", "license"}, set(facts))
        self.assertEqual({"name": "MIT", "url": None}, facts["license"])


class ReviewTests(unittest.TestCase):
    def test_review_copies_what_was_seen(self) -> None:
        digest = ms.content_hash("# Model\n")
        tree = UnitTree([http_source(observedHash=digest, observedAt="2026-09-27")])
        self.addCleanup(tree.close)
        self.assertEqual(
            ["vendor-page"], ms.review(tree.unit, ["vendor-page"], root=tree.root, today=TODAY)
        )
        source = tree.sources()["sources"][0]
        self.assertEqual((digest, "2026-09-28"), (source["reviewedHash"], source["reviewedAt"]))

    def test_a_manual_source_is_hashed_from_its_saved_text(self) -> None:
        tree = UnitTree([http_source(fetch="manual")])
        self.addCleanup(tree.close)
        with self.assertRaises(ms.FactsError):
            ms.review(tree.unit, ["vendor-page"], root=tree.root, today=TODAY)
        saved = tree.root / "docs/agents/model-facts" / ".cache" / tree.unit / "vendor-page.txt"
        saved.parent.mkdir(parents=True)
        saved.write_text("Models\r\nmimo-v2.6-pro  \n", encoding="utf-8")
        ms.review(tree.unit, ["vendor-page"], root=tree.root, today=TODAY)
        self.assertEqual(
            ms.content_hash("Models\nmimo-v2.6-pro\n"), tree.sources()["sources"][0]["reviewedHash"]
        )

    def test_unfetched_and_unknown_sources_are_refused(self) -> None:
        tree = UnitTree([http_source()])
        self.addCleanup(tree.close)
        for source_ids in (["vendor-page"], ["nope"]):
            with self.subTest(source_ids), self.assertRaises(ms.FactsError):
                ms.review(tree.unit, source_ids, root=tree.root, today=TODAY)


class WatchTests(unittest.TestCase):
    def test_index_pattern_org_listing_and_ignore(self) -> None:
        facts = {
            "schemaVersion": 1,
            "unit": "families/demo",
            "guidance": [],
            "models": {"demo-1": {"facts": {}}},
        }
        watch = [
            {
                "id": "index",
                "kind": "index",
                "url": "https://docs.example/llms.txt",
                "pattern": r"models/(demo-[0-9.]+)\.md",
                "ignore": [],
            },
            {
                "id": "org",
                "kind": "huggingface-org",
                "org": "vendor",
                "pattern": r"Demo-[0-9.]+(-FP8)?",
                "ignore": [r".*-FP8"],
            },
        ]
        tree = UnitTree([], facts, watch)
        self.addCleanup(tree.close)
        listing = [{"id": "vendor/Demo-2"}, {"id": "vendor/Demo-2-FP8"}, {"id": "vendor/Other"}]
        fetcher = FakeFetcher(
            {
                "https://docs.example/llms.txt": page("models/demo-1.md\nmodels/demo-3.md\n"),
                f"{ms.HUB}/api/models?author=vendor&sort=createdAt&direction=-1&limit=100": page(
                    json.dumps(listing)
                ),
            }
        )
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY)
        self.assertEqual(["demo-3", "Demo-2"], [item["name"] for item in report.newModels])

    def test_page_spellings_of_a_known_model_are_not_new(self) -> None:
        watch = [
            {
                "id": "index",
                "kind": "index",
                "url": "https://docs.example/llms.txt",
                "pattern": r"developers/(grok-[0-9][0-9a-z.-]*)\.md",
                "ignore": [],
            }
        ]
        facts = {
            "schemaVersion": 1,
            "unit": "families/demo",
            "guidance": [],
            "models": {"grok-4.7": {"facts": {}}},
        }
        tree = UnitTree([], facts, watch)
        self.addCleanup(tree.close)
        fetcher = FakeFetcher(
            {"https://docs.example/llms.txt": page("https://docs.x.ai/developers/grok-4-7.md")}
        )
        self.assertEqual(
            [], ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY).newModels
        )

    def test_baseline_acknowledges_all_but_the_kept_names(self) -> None:
        watch = [
            {
                "id": "index",
                "kind": "index",
                "url": "https://docs.example/llms.txt",
                "pattern": r"models/(demo-[0-9.]+)\.md",
                "ignore": [],
            }
        ]
        tree = UnitTree(
            [],
            {
                "schemaVersion": 1,
                "unit": "families/demo",
                "guidance": [],
                "models": {"demo-1": {"facts": {}}},
            },
            watch,
        )
        self.addCleanup(tree.close)
        fetcher = FakeFetcher(
            {
                "https://docs.example/llms.txt": page(
                    "models/demo-1.md models/demo-0.9.md models/demo-2.md"
                )
            }
        )
        self.assertEqual(
            ["demo-0.9"], ms.baseline(tree.unit, root=tree.root, fetcher=fetcher, keep=["demo-2"])
        )
        self.assertEqual(["demo-0.9"], tree.sources()["watch"][0]["seen"])
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY)
        self.assertEqual(["demo-2"], [item["name"] for item in report.newModels])
        self.assertEqual(
            [], ms.baseline(tree.unit, root=tree.root, fetcher=fetcher, keep=["demo-2"])
        )

    def test_a_model_on_a_host_before_the_vendor_documents_it_is_new(self) -> None:
        watch = [
            {
                "id": "go",
                "kind": "index",
                "url": "https://host.example/go/",
                "pattern": r"opencode-go/([a-z0-9.-]+)",
                "ignore": [],
            }
        ]
        tree = UnitTree(
            [], {"schemaVersion": 1, "unit": "families/demo", "guidance": [], "models": {}}, watch
        )
        self.addCleanup(tree.close)
        fetcher = FakeFetcher({"https://host.example/go/": page("use opencode-go/demo-9 today")})
        report = ms.run([tree.unit], root=tree.root, fetcher=fetcher, today=TODAY)
        self.assertEqual(["demo-9"], [item["name"] for item in report.newModels])


if __name__ == "__main__":
    unittest.main()
