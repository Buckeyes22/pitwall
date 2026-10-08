"""Choose where lease state lives: ``[personal] backend`` in ``pitwall.toml``."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from pitwall.config import ConfigFileError, resolve_config_file

BackendName = Literal["personal", "registry"]


def _configured_backend(environ: Mapping[str, str] | None) -> BackendName:
    """The `[personal] backend` choice in pitwall.toml; `personal` when the file omits it."""
    path: Path | None = resolve_config_file(dict(os.environ if environ is None else environ))
    if path is None or not path.is_file():
        return "personal"
    try:
        with path.open("rb") as handle:
            table = tomllib.load(handle).get("personal", {})
    except tomllib.TOMLDecodeError as exc:
        # tomllib's own text can quote keys from the file; report the position only.
        raise ConfigFileError(
            f"could not read Pitwall config file {path}: "
            f"invalid TOML at line {exc.lineno}, column {exc.colno}"
        ) from None
    except OSError as exc:
        raise ConfigFileError(f"could not read Pitwall config file {path}: {exc}") from exc
    backend = table.get("backend", "personal") if isinstance(table, dict) else None
    if backend not in ("personal", "registry"):
        raise ConfigFileError(f'[personal] backend in {path} must be "personal" or "registry"')
    return cast(BackendName, backend)


def select_backend(environ: Mapping[str, str] | None = None) -> BackendName:
    """The configured backend; ``personal`` unless pitwall.toml says ``registry``.

    ``DATABASE_URL`` alone never switches it: the registry backend is an explicit choice.
    """
    return _configured_backend(environ)


def backend_status_line(backend: BackendName) -> str:
    """The one line ``pitwall status`` prints so the active backend is never implicit."""
    where = "local state file" if backend == "personal" else "Postgres registry"
    return f"Backend: {backend} ({where}; set [personal] backend in pitwall.toml)"


class RegistryBackend:
    """The hosted path: delegates to existing registry-backed CLI commands unchanged."""

    def serve(self, argv: list[str]) -> int:
        from pitwall.cli.serve_model import cmd_serve_model

        return cmd_serve_model(argv)

    def status(self, *, json_output: bool = False) -> int:
        from pitwall.cli.leases import cmd_leases

        return cmd_leases(["list", *(["--json"] if json_output else [])])

    def stop(self, lease_id: str, *, json_output: bool = False) -> int:
        from pitwall.cli.leases import cmd_leases

        return cmd_leases(["stop", lease_id, *(["--json"] if json_output else [])])
