"""Extract OmniRoute's free-tier catalog into Pitwall seeds and config (§8)."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import re
import sys
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from pitwall.gateway_catalog import extract
from pitwall.gateway_catalog.schema import (
    ONE_TIME,
    RECURRING_CREDIT,
    STEADY_MONTHLY,
    UNCAPPED,
    CatalogRow,
)
from pitwall.routing.cascade_seed import ladder_rank


def default_repo_root() -> Path:
    """Locate the checkout the sync writes into.

    The artifacts (seeds, catalog, lock) live in a Pitwall checkout, never in
    an installed wheel, so look upward from the working directory for a root
    that carries ``seed/gateway-providers.yaml``; fall back to this source
    tree's own root for editable installs. Called when a sync runs, never at import.
    """
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "seed" / "gateway-providers.yaml").is_file():
            return candidate
    return Path(__file__).resolve().parents[3]


# Loopback fork (the Node gateway shim, Task 18) that fronts every budgeted
# pool (Task 19). Keyless rows keep their upstream URL only when the sync is
# run with --direct-keyless.
FORK_BASE_URL = "http://127.0.0.1:20130/v1"
NPM_REGISTRY = "https://registry.npmjs.org"
UPSTREAM_PACKAGE = "omniroute"
_FETCH_TIMEOUT_SECS = 120
_INTEGRITY_ALGORITHMS = ("sha512", "sha384", "sha256")

_VERDICT_LINE_RE = re.compile(r"^- ([A-Za-z0-9._:-]+): (keep|kill)$", re.MULTILINE)

# Providers in the free-tier catalog that the upstream registry either lacks
# entirely or exposes without a baseUrl. Skip them rather than raising so the
# sync stays deterministic; drift on this set is a hard CI failure (§8.2).
KNOWN_UNREACHABLE: frozenset[str] = frozenset(
    {"agy", "arcee-ai", "glm-cn", "opencode-zen", "qwen-web"}
)

_STRIPPABLE_PATH_SUFFIXES = ("/chat/completions", "/responses", "/messages")


def _strip_path_suffix(base_url: str) -> str:
    for suffix in _STRIPPABLE_PATH_SUFFIXES:
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    return base_url.rstrip("/")


@dataclass(frozen=True, slots=True)
class PoolTotals:
    steady_monthly: int
    recurring_credit: int
    one_time: int
    uncapped_providers: tuple[str, ...]
    gated_tokens: int


@dataclass(frozen=True, slots=True)
class CatalogArtifacts:
    capabilities_yaml: str
    providers_yaml: str
    catalog_json: dict[str, Any]
    routes_json: dict[str, Any]


def load_rows(
    budgets: Sequence[Mapping[str, Any]],
    endpoints: Mapping[str, str],
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
    skipped: list[tuple[str, str]] | None = None,
) -> tuple[list[CatalogRow], int]:
    registry_covered: set[str] = set()
    rows: list[CatalogRow] = []
    for raw in budgets:
        if raw.get("enabled") is False:
            continue
        provider = raw["provider"]
        reg_entry = (registry or {}).get(provider, {})
        endpoint = endpoints.get(provider)
        # Registry is authoritative; fall back to PROVIDER_ENDPOINTS for any
        # legacy rows. ``agy`` carries an entry without baseUrl — both fields
        # being falsy triggers the unreachable check below.
        raw_base = reg_entry.get("baseUrl") or endpoint
        if not raw_base:
            if provider in KNOWN_UNREACHABLE:
                if skipped is not None:
                    skipped.append((provider, raw["modelId"]))
                continue
            raise ValueError(
                f"no baseUrl for provider {provider!r} "
                f"(model {raw['modelId']!r}); add it to KNOWN_UNREACHABLE if "
                "the upstream registry will never carry one"
            )
        # Strip whichever chat-style path the upstream baked in; the adapter
        # appends its own path (see sync_catalog.py §3 — openai_gateway).
        base_url = _strip_path_suffix(str(raw_base))
        if reg_entry.get("baseUrl"):
            registry_covered.add(provider)
        rows.append(
            CatalogRow(
                provider=provider,
                model_id=raw["modelId"],
                display_name=raw.get("displayName", raw["modelId"]),
                monthly_tokens=int(raw.get("monthlyTokens", 0)),
                credit_tokens=int(raw.get("creditTokens", 0)),
                free_type=raw["freeType"],
                pool_key=raw.get("poolKey"),
                tos=raw["tos"],
                base_url=base_url,
                upstream_format=str(reg_entry.get("format") or "openai"),
                executor=str(reg_entry.get("executor") or "default"),
                auth_type=str(reg_entry.get("authType") or "apikey"),
                trains_on_prompts=bool(raw.get("trainsOnPrompts", False)),
                hard_stop_guaranteed=bool(raw.get("hardStopGuaranteed", False)),
                eligibility_gate=raw.get("eligibilityGate"),
            )
        )
    rows.sort(key=lambda r: (r.provider, r.model_id))
    return rows, len(registry_covered)


def registry_covered_count(
    budgets: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]] | None,
) -> int:
    """Number of catalog providers that carry a non-empty ``baseUrl`` in the
    upstream registry. Computed independently of ``load_rows`` so the lock file
    can record it without coupling to row construction.
    """
    if not registry:
        return 0
    out = 0
    seen: set[str] = set()
    for raw in budgets:
        if raw.get("enabled") is False:
            continue
        provider = raw["provider"]
        if provider in seen:
            continue
        seen.add(provider)
        if (registry.get(provider) or {}).get("baseUrl"):
            out += 1
    return out


def dedupe_pool_totals(rows: Sequence[CatalogRow]) -> PoolTotals:
    """§8.1 rule 3: shared poolKey counts once; uncapped never summed; gated never admitted."""
    seen_pools: set[str] = set()
    steady = credit = one_time = gated = 0
    uncapped: list[str] = []
    for row in rows:
        gated_row = row.eligibility_gate is not None
        if row.free_type in UNCAPPED:
            if not gated_row and row.provider not in uncapped:
                uncapped.append(row.provider)
            continue
        key = row.pool_key or f"{row.provider}/{row.model_id}"
        if key in seen_pools:
            continue
        seen_pools.add(key)
        if row.free_type in STEADY_MONTHLY:
            if gated_row:
                gated += row.monthly_tokens
            else:
                steady += row.monthly_tokens
        elif row.free_type in RECURRING_CREDIT and not gated_row:
            credit += row.credit_tokens
        elif row.free_type in ONE_TIME and not gated_row:
            one_time += row.credit_tokens
    return PoolTotals(steady, credit, one_time, tuple(uncapped), gated)


def _ladder_rank(row: CatalogRow) -> int:
    # The extractor delegates to the runtime ladder so the seed chain and
    # the runtime fallback order stay byte-identical (Task 8 §9.6).
    return ladder_rank(row)


def route_key_env(row: CatalogRow) -> str | None:
    """The environment variable the gateway reads for this row's pool key.

    Keys never enter the route table; the table names where to find them.
    """
    if row.auth_type == "none":
        return None
    pool = (row.pool_key or row.provider).upper()
    return "PITWALL_GATEWAY_KEY_" + re.sub(r"[^A-Z0-9]", "_", pool)


def _servable(row: CatalogRow) -> bool:
    """Routable and speakable: both the fork and a direct call send OpenAI shape."""
    return row.routable and row.upstream_format == "openai"


def _routed_base_url(row: CatalogRow, *, direct_keyless: bool) -> str:
    """Task 19 routing: non-keyless rows always ride the fork; keyless rows
    keep the upstream base_url only under --direct-keyless."""
    if direct_keyless and row.free_type == "keyless":
        return row.base_url
    return FORK_BASE_URL


def _provider_entry(row: CatalogRow, chain: list[str], *, direct_keyless: bool) -> dict[str, Any]:
    return {
        "name": row.seed_name,
        "capability": "coding.chat",
        "endpoint_id": row.seed_name.removeprefix("gw-")[:64],
        "provider_type": "openai_gateway",
        "adapter": "openai_gateway",
        "region": "GLOBAL",
        "priority": 50 if row.free_type == "keyless" else 60,
        "enabled": _servable(row),
        "cost": {"mode": "zero"},
        "fallback_chain": chain,
        "gateway": {
            "base_url": _routed_base_url(row, direct_keyless=direct_keyless),
            "model_id": row.model_id,
            "catalog": {
                "free_type": row.free_type,
                "tos": row.tos,
                "trains_on_prompts": row.trains_on_prompts,
                "hard_stop_guaranteed": row.hard_stop_guaranteed,
                "pool_key": row.pool_key,
                "monthly_tokens": row.monthly_tokens,
                "credit_tokens": row.credit_tokens,
                "eligibility_gate": row.eligibility_gate,
                "display_name": row.display_name,
                "upstream_format": row.upstream_format,
                "executor": row.executor,
                "auth_type": row.auth_type,
                "direct_ok": row.direct_ok,
            },
        },
    }


def transform(
    rows: Sequence[CatalogRow], *, curated_at: str, direct_keyless: bool = False
) -> CatalogArtifacts:
    servable = [r for r in rows if _servable(r)]
    ladder = sorted(servable, key=lambda r: (_ladder_rank(r), r.provider, r.model_id))
    names = [r.seed_name for r in ladder]
    providers: list[dict[str, Any]] = []
    routes: dict[str, dict[str, Any]] = {}
    for row in rows:
        chain = [n for n in names if n != row.seed_name] if _servable(row) else []
        providers.append(_provider_entry(row, chain, direct_keyless=direct_keyless))
        if _servable(row) and _routed_base_url(row, direct_keyless=direct_keyless) == FORK_BASE_URL:
            routes[row.seed_name] = {
                "base_url": row.base_url,
                "model_id": row.model_id,
                "api_key_env": route_key_env(row),
                "key_required": row.auth_type == "apikey",
            }
    totals = dedupe_pool_totals(rows)
    catalog_json = {
        "schema_version": 1,
        "curated_at": curated_at,
        "totals": {
            "steady_monthly": totals.steady_monthly,
            "recurring_credit": totals.recurring_credit,
            "one_time": totals.one_time,
            "uncapped_providers": list(totals.uncapped_providers),
            "gated_tokens": totals.gated_tokens,
        },
        "avoid_list": sorted({r.provider for r in rows if r.tos == "avoid"}),
        "providers": providers,
    }
    capabilities_yaml = (
        "capabilities:\n"
        "  - name: coding.chat\n"
        "    version: 1.0.0\n"
        "    class: llm\n"
        "    description: Free-tier gateway chat capability (ADR 0007).\n"
        "    cost_mode: zero\n"
        "    input_schema:\n      type: object\n"
        "    output_schema:\n      type: object\n"
    )
    providers_yaml = "providers:\n" + "".join(_yaml_provider(p) for p in providers)
    routes_json = {"schema_version": 1, "routes": dict(sorted(routes.items()))}
    return CatalogArtifacts(capabilities_yaml, providers_yaml, catalog_json, routes_json)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def _yaml_provider(p: Mapping[str, Any]) -> str:
    lines = [f"  - name: {p['name']}"]
    for key in (
        "capability",
        "endpoint_id",
        "provider_type",
        "adapter",
        "region",
        "priority",
        "enabled",
    ):
        lines.append(f"    {key}: {_yaml_scalar(p[key])}")
    lines.append("    cost:\n      mode: zero")
    lines.append("    fallback_chain:" + ("" if p["fallback_chain"] else " []"))
    lines.extend(f"      - {n}" for n in p["fallback_chain"])
    gw = p["gateway"]
    lines.append("    gateway:")
    lines.append(f"      base_url: {_yaml_scalar(gw['base_url'])}")
    lines.append(f"      model_id: {_yaml_scalar(gw['model_id'])}")
    lines.append("      catalog:")
    lines.extend(f"        {k}: {_yaml_scalar(v)}" for k, v in gw["catalog"].items())
    return "\n".join(lines) + "\n"


def parse_verdicts(dossier_text: str) -> dict[str, str]:
    """Read the ``- <provider>: keep|kill`` verdict lines out of a bench dossier."""

    verdicts: dict[str, str] = {}
    for match in _VERDICT_LINE_RE.finditer(dossier_text):
        name, verdict = match.group(1), match.group(2)
        if verdicts.setdefault(name, verdict) != verdict:
            raise ValueError(f"conflicting keep/kill verdicts for provider {name!r}")
    if not verdicts:
        raise ValueError("dossier contains no `- <provider>: keep|kill` verdict lines")
    return verdicts


def apply_verdicts(dossier_text: str, *, seed_path: Path) -> list[str]:
    """Task 17 Step 3: flip ``enabled: false`` for every ``kill`` pool in the seed.

    Returns the sorted names that were flipped. Rows already disabled stay
    untouched, and every dossier provider must exist in the seed — a missing
    name is a dossier/seed drift and fails closed.
    """

    verdicts = parse_verdicts(dossier_text)
    text = seed_path.read_text(encoding="utf-8")
    seed_names = set(re.findall(r"^  - name: (\S+)$", text, flags=re.MULTILINE))
    unknown = sorted(set(verdicts) - seed_names)
    if unknown:
        raise ValueError(
            "dossier verdicts reference providers missing from the seed: " + ", ".join(unknown)
        )
    lines = text.splitlines(keepends=True)
    flipped: list[str] = []
    current: str | None = None
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("  - name: "):
            current = stripped.removeprefix("  - name: ").strip()
        elif stripped == "    enabled: true" and verdicts.get(current or "") == "kill":
            lines[index] = line.replace("enabled: true", "enabled: false", 1)
            flipped.append(str(current))
    if flipped:
        seed_path.write_text("".join(lines), encoding="utf-8")
    return sorted(flipped)


class IntegrityError(RuntimeError):
    """The npm registry metadata or the downloaded tarball failed verification."""


def _fetch(url: str) -> bytes:
    if urlsplit(url).scheme != "https":
        raise IntegrityError(f"refusing non-https download URL {url!r}")
    with urlopen(url, timeout=_FETCH_TIMEOUT_SECS) as response:  # noqa: S310  # reason: https scheme checked above  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        return bytes(response.read())


def verify_integrity(data: bytes, integrity: str) -> None:
    """Check *data* against an npm SRI ``dist.integrity`` value (strongest listed digest wins)."""
    entries = dict(item.split("-", 1) for item in integrity.split() if "-" in item)
    for algorithm in _INTEGRITY_ALGORITHMS:
        expected = entries.get(algorithm)
        if expected is None:
            continue
        actual = base64.b64encode(hashlib.new(algorithm, data).digest()).decode("ascii")
        if not hmac.compare_digest(actual, expected):
            raise IntegrityError(f"tarball integrity mismatch: expected {algorithm}-{expected}")
        return
    raise IntegrityError(f"registry integrity value has no supported digest: {integrity!r}")


def read_tarball_sources(tarball: bytes) -> dict[str, str]:
    """Read the extractor's source files out of an npm tarball held in memory."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith("package/"):
                continue
            rel = member.name.removeprefix("package/")
            if not extract.wanted_paths([rel]):
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[rel] = handle.read().decode("utf-8")
    return files


def extract_from_npm(version: str) -> tuple[dict[str, Any], str]:
    """Download the pinned upstream release over HTTPS, verify it, and extract the catalog.

    Returns the sync payload and the tarball's SHA-256. Nothing touches disk and neither
    ``npm`` nor ``node`` is invoked.
    """
    meta_url = f"{NPM_REGISTRY}/{UPSTREAM_PACKAGE}/{quote(version, safe='')}"
    dist = json.loads(_fetch(meta_url)).get("dist") or {}
    tarball_url = dist.get("tarball")
    integrity = dist.get("integrity")
    if not isinstance(tarball_url, str) or not isinstance(integrity, str):
        raise IntegrityError(
            f"registry metadata for {UPSTREAM_PACKAGE}@{version} lacks dist.integrity"
        )
    tarball = _fetch(tarball_url)
    verify_integrity(tarball, integrity)
    payload = extract.extract_catalog(read_tarball_sources(tarball))
    return payload, hashlib.sha256(tarball).hexdigest()


def write_artifacts(
    artifacts: CatalogArtifacts,
    *,
    version: str,
    tarball_sha256: str,
    registry_covered: int,
    known_unreachable: Sequence[str],
    repo_root: Path | None = None,
) -> None:
    root = default_repo_root() if repo_root is None else repo_root
    (root / "seed" / "gateway-capabilities.yaml").write_text(
        artifacts.capabilities_yaml, encoding="utf-8"
    )
    (root / "seed" / "gateway-providers.yaml").write_text(
        artifacts.providers_yaml, encoding="utf-8"
    )
    (root / "config" / "gateway-catalog.json").write_text(
        json.dumps(artifacts.catalog_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "config" / "gateway-routes.json").write_text(
        json.dumps(artifacts.routes_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lock = {
        "upstream_version": version,
        "tarball_sha256": tarball_sha256,
        "extractor_sha256": hashlib.sha256(Path(extract.__file__).read_bytes()).hexdigest(),
        "steady_monthly": artifacts.catalog_json["totals"]["steady_monthly"],
        "pool_count": len(
            {
                p["gateway"]["catalog"]["pool_key"]
                for p in artifacts.catalog_json["providers"]
                if p["gateway"]["catalog"]["pool_key"]
            }
        ),
        "avoid_list": artifacts.catalog_json["avoid_list"],
        "row_count": len(artifacts.catalog_json["providers"]),
        "registry_covered": registry_covered,
        "known_unreachable": sorted(known_unreachable),
    }
    (root / "config" / "gateway-catalog.lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync the free-tier catalog from a pinned OmniRoute npm release."
    )
    parser.add_argument("--version", required=False, default=None)
    parser.add_argument(
        "--from-json",
        type=Path,
        help="Skip the registry download; read an already-extracted JSON (tests, offline).",
    )
    parser.add_argument(
        "--direct-keyless",
        action="store_true",
        help=(
            "Keep keyless rows on their upstream base_url instead of routing "
            "them through the loopback fork (Task 19)."
        ),
    )
    parser.add_argument(
        "--apply-verdicts",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Skip sync; apply the keep/kill verdicts of a bench dossier (Task 17 Step 3). "
            "kill rows flip enabled: false in the seed."
        ),
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=None,
        help="Providers seed to rewrite (only with --apply-verdicts); default <repo-root>/seed/gateway-providers.yaml.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Checkout to write seeds, catalog, and lock into (default: the enclosing checkout).",
    )
    args = parser.parse_args(argv)
    root = args.repo_root or repo_root or default_repo_root()
    seed_path = args.seed or root / "seed" / "gateway-providers.yaml"
    if args.apply_verdicts is not None:
        if args.version is not None:
            parser.error("--apply-verdicts and --version are mutually exclusive")
        flipped = apply_verdicts(
            args.apply_verdicts.read_text(encoding="utf-8"), seed_path=seed_path
        )
        detail = f": {', '.join(flipped)}" if flipped else " (no disabled row changed)"
        print(f"applied verdicts; flipped {len(flipped)} pool(s) to enabled: false{detail}")
        return 0
    if args.version is None:
        parser.error("one of --version or --apply-verdicts is required")
    if args.from_json is not None:
        try:
            payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"cannot read --from-json {args.from_json}: {exc}", file=sys.stderr)
            return 2
        tarball_sha = hashlib.sha256(args.from_json.read_bytes()).hexdigest()
    else:
        try:
            payload, tarball_sha = extract_from_npm(args.version)
        except (OSError, ValueError, IntegrityError) as exc:
            print(f"cannot sync omniroute@{args.version}: {exc}", file=sys.stderr)
            return 2
    skipped: list[tuple[str, str]] = []
    rows, registry_covered_from_rows = load_rows(
        payload["budgets"],
        payload["endpoints"],
        registry=payload.get("registry"),
        skipped=skipped,
    )
    registry_covered = max(
        registry_covered_from_rows,
        registry_covered_count(payload["budgets"], payload.get("registry")),
    )
    skipped_providers = sorted({p for p, _ in skipped})
    if skipped_providers:
        print(
            f"warning: skipped {len(skipped)} rows from known-unreachable providers "
            f"(providers: {', '.join(skipped_providers)})",
            file=sys.stderr,
        )
    write_artifacts(
        transform(rows, curated_at=payload["curatedAt"], direct_keyless=args.direct_keyless),
        version=args.version,
        tarball_sha256=tarball_sha,
        registry_covered=registry_covered,
        known_unreachable=sorted(KNOWN_UNREACHABLE),
        repo_root=root,
    )
    print(f"synced {len(payload['budgets'])} rows from omniroute@{args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
