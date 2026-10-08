# Dependency workflow example

Replace `replace-with/provider-model` with an OpenCode provider/model available in your installation and
`replace-with-route-name` with its existing named route profile. The profile's resolved provider and
model must match the task's `provider` and `model`; remove the optional `name` field when using direct
provider dispatch. Then run from this directory:

```bash
pitwall agents workflow run workflow.json --host copilot
```

The example is read-only. The second task receives only the first task's bounded stdout selection.
