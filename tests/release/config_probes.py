"""Effect probes for J37: each helper drives a real consumer of one configuration key.

Imported by the probe subprocesses ``test_config_keys_journey`` starts with the key set, so
module-level configuration reads happen fresh in every probe.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json

m = importlib.import_module


def attempt(probe):
    """Run a probe; a consumer that refuses the configuration reports its error type."""
    try:
        return probe()
    except (Exception, SystemExit) as exc:  # reason: a refusal is a pinned outcome
        return {"refused": type(exc).__name__}


def uvicorn_kwargs(module):
    import uvicorn

    captured = {}
    uvicorn.run = lambda *args, **kwargs: captured.update(kwargs)
    # The probe reads where configuration lands, not whether a database answers; the startup
    # database check has its own tests (tests/test_entrypoint_bind_hosts.py).
    m("pitwall.cli.runtime_errors").database_preflight = lambda *args, **kwargs: None
    m(module + ".__main__").main([])
    return captured


class _Conn:
    async def fetch(self, *args):
        return []

    async def fetchrow(self, *args):
        return None

    async def fetchval(self, *args):
        return None

    async def execute(self, *args):
        return "OK"

    def transaction(self):
        return _Ctx(None)


class _Ctx:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def acquire(self):
        return _Ctx(_Conn())


def retention_call():
    retention = m("pitwall.retention")
    captured = {}

    async def fake(pool, output_path, **kwargs):
        captured.update(kwargs, output_path=str(output_path))

    retention.archive_workloads_to_jsonl = fake
    asyncio.run(m("pitwall.reconciler")._archive_old_workloads({"db_pool": _Pool()}))
    return captured


def archive_key_version():
    import tempfile
    from pathlib import Path

    archive = m("pitwall.retention.archive")

    # A run writes nothing when no row is eligible, so give it one row and stub only the database
    # reads and the purge transaction; the key-version consumer (`_encrypt_archive`) runs for real.
    async def one_row(*args, **kwargs):
        return [{"id": "wl-j37"}], 0

    async def commit(*args, **kwargs):
        return 0, []

    archive._select_batch = one_row
    archive._commit_run = commit
    result = asyncio.run(archive.archive_workloads_to_jsonl(_Pool(), Path(tempfile.mkdtemp())))
    manifest = Path(result["manifest_path"]) if "manifest_path" in result else None
    return (
        json.loads(manifest.read_text())["key_version"] if manifest else result.get("key_version")
    )


def backup_drill_dsn():
    drill = m("pitwall.ops.backup_drill")
    seen = []

    async def create(source_db_url, temp_db_name):
        seen.append(source_db_url)
        raise RuntimeError("probe stops before any database work")

    drill._create_temp_database = create

    async def versions_ok(pool):
        return None

    drill._check_pg_tool_versions = versions_ok
    with contextlib.suppress(Exception):
        asyncio.run(drill.run_pit_restore_drill({"db_pool": object()}))
    return seen[0] if seen else None


def insecure_bind_allowed():
    try:
        m("pitwall.config").require_credentials_for_bind("api", "0.0.0.0", ("PITWALL_API_TOKEN",))
    except SystemExit:
        return "refused"
    return "allowed"


def workbench_launch_env(name, restricted=False, api_key_env=None):
    """The value the launcher gives Pi for ``name`` (None when it does not pass one)."""
    import pathlib
    import types

    launcher = m("pitwall.workbench.launcher")
    profile = types.SimpleNamespace(
        profile={"apiKeyEnv": api_key_env} if api_key_env else {}, agent_dir=pathlib.Path("/agent")
    )
    options = launcher.PiLaunchOptions(
        cwd=pathlib.Path("."), profile=profile, restricted=restricted
    )
    return launcher.launch_environment(options).get(name)


def workbench_admission_dir():
    return str(m("pitwall.workbench.admission").SharedRequestAdmission("group").directory)


def workbench_account_budget_dir():
    budget = m("pitwall.workbench.account_budget")
    policy = budget.AccountBudgetPolicy(
        account_group="group", max_concurrent=1, in_flight_token_budget=1, unknown_usage="hold"
    )
    return str(budget.AccountBudgetAdmission(policy).directory)


def workbench_agent_dir():
    """The Pi state directory ``pitwall workbench launch`` chooses (the per-project hash is dropped)."""
    import pathlib

    cli = m("pitwall.workbench.cli")

    class Chosen(Exception):
        pass

    def capture(profile_name, profile, agent_dir):
        raise Chosen(agent_dir)

    cli._check_toolchain = lambda **kwargs: None
    cli.profile_from_config = lambda config, name: {}
    cli.compile_profile = capture
    pathlib.Path("profile.json").write_text("{}")
    try:
        cli._COMMANDS["launch"](["profile.json", "p", "."])
    except Chosen as chosen:
        agent_dir = chosen.args[0]
        if agent_dir.parent.parent.name == "agent":
            return {"root": str(agent_dir.parent.parent.parent), "mode": "agent"}
        return str(agent_dir)
    return None


def workbench_tintin_path():
    session = m("pitwall.workbench.comparison.session")
    found = session.pinned_node_module(session.TINTIN_PACKAGE, session.TINTIN_ENTRY)
    return None if found is None else str(found)


def workbench_pi_bin():
    return str(m("pitwall.workbench.launcher").default_pi_bin())


def workbench_runtime_base():
    import types

    launcher = m("pitwall.workbench.launcher")
    return str(launcher._runtime_base(types.SimpleNamespace(runtime_dir=None)))


def workbench_candidate_b():
    import pathlib

    runner = m("pitwall.workbench.comparison.runner")
    profile = {"apiKeyEnv": "J37_PROFILE_KEY"}  # pragma: allowlist secret
    runner.profile_from_config = lambda config, name: profile
    m("os").environ["J37_PROFILE_KEY"] = "placeholder"
    pathlib.Path("profile.json").write_text("{}")
    options = runner.parse_comparison_options("profile.json", "out", "2")
    candidates = {c.id: c for c in runner.ComparisonRunner(options).candidates}
    return str(candidates[runner.NICOBAILON].extension)


def workbench_hosted_completion_cap():
    import io
    import pathlib

    acceptance = m("pitwall.workbench.hosted.acceptance")
    seen = []
    acceptance.profile_from_config = lambda config, name: {
        "accountRef": "a",
        "apiKeyEnv": acceptance.CREDENTIAL_ENV,
    }
    acceptance.credential_value = lambda auth, ref: "placeholder"

    def run_profile(name, profile, key, run_root, override):
        seen.append(override)
        return {"status": "passed"}

    acceptance._run_profile = run_profile
    pathlib.Path("config.json").write_text("{}")
    pathlib.Path("auth.json").write_text("{}")
    with contextlib.redirect_stdout(io.StringIO()):
        acceptance.run_hosted_acceptance("config.json", "auth.json", "out", ["p"])
    return seen[0]


def gateway_supervisor_start():
    """``GatewaySupervisor.start`` refuses without the token; with it, it reaches the port check."""
    supervisor_module = m("pitwall.personal.gateway")

    class Store:
        def read_document(self, name):
            return None

    supervisor = supervisor_module.GatewaySupervisor(
        Store(), port_holder=lambda bind, port: "another process"
    )
    supervisor.start()
