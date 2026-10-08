#!/usr/bin/env python3
"""CI entrypoint for checking committed generated assets."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.agents.sync_model_facts import (  # noqa: E402  # reason: sys.path bootstrap
    main as model_facts_main,
)
from tools.agents.sync_routes import (  # noqa: E402  # reason: sys.path bootstrap
    main as routes_main,
)
from tools.agents.validate_model_facts import (  # noqa: E402  # reason: sys.path bootstrap
    main as validate_model_facts_main,
)

if __name__ == "__main__":
    # sync_routes and sync_model_facts print their own regeneration command when stale.
    codes = [routes_main(["--check"]), model_facts_main(["--check"]), validate_model_facts_main([])]
    if codes[2]:
        print(
            "model facts validation failed: fix the reported facts or sources "
            "(docs/agents/CONTRIBUTING.md, model facts); `make regen` only fixes stale outputs",
            file=sys.stderr,
        )
    raise SystemExit(max(codes))
