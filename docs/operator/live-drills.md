# Live drills

The manual smoke and benchmark scripts that used to sit in `tools/` are gone. The live checks are the
tests marked `live` under `tests/`. They are not part of any automated test tier (`addopts` in
`pyproject.toml` deselects them), they never run in CI, and most of them **spend money**: they call
real RunPod or provider endpoints. Run them deliberately, against an account you own, with a budget in
place.

| Test | What it checks | Needs | Spends |
| --- | --- | --- | --- |
| `tests/live/test_runpod_template_contract.py` | A RunPod template round-trips (create, get, delete) against the real REST API. | `RUNPOD_API_KEY` and a live opt-in (`RUNPOD_LIVE=1`, `PITWALL_RUN_LIVE=1`, or `PITWALL_RUNPOD_LIVE=1`) | Creates and deletes one template; no compute. |
| `tests/live/test_personal_serve_live.py` | Personal `pitwall serve` launches a pod, answers through the route, and the pod self-terminates at its deadline. | the RunPod credential, the live opt-in above; `PITWALL_LIVE_TTL_MINUTES` (default 35) | One pod for the TTL. |
| `tests/live/test_selfhosted_endpoint.py` | A self-hosted OpenAI-compatible endpoint answers, and its evidence is recorded. | `PITWALL_SELFHOSTED_BASE_URL`, `PITWALL_SELFHOSTED_API_KEY_ENV` (the name of the variable holding the key) | Requests to your endpoint. |
| `tests/live/test_model_studio_live.py` | A Model Studio endpoint streams usage and reports subscription stats or their documented absence. | `PITWALL_MODEL_STUDIO_LIVE=1` and the Model Studio credentials the test names | A few requests. |
| `tests/live/test_catalogue_weights_resolve.py` | Every catalogue weight file named in the model catalogue resolves on Hugging Face. | `PITWALL_HF_CATALOGUE_CHECK` set, and network access | None. |
| `tests/api/test_e2e_sync_inference.py` | A synchronous inference goes through the API to a live provider. | the live opt-in, `RUNPOD_API_KEY`, `PITWALL_TEST_DATABASE_URL`, and `PITWALL_LIVE_LB_ENDPOINT_ID` | One inference. |

Run one with an explicit selector, for example:

```bash
RUNPOD_LIVE=1 uv run pytest -m live tests/live/test_runpod_template_contract.py -q | tail -40
```

A live test skips itself when its opt-in or credential is missing, and prints the reason. The kill
switch has a hermetic proof (`tests/admin/test_kill_switch.py`, `tests/admin/test_kill_switch_route.py`);
engaging it against a real account is an incident action, not a drill, and there is no resume.
