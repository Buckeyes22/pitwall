# JSON and APIs

## In one sentence

JSON is the text format the API uses for requests and responses: objects in `{}`, lists in `[]`,
text in double quotes.

## Why it matters when testing Pitwall

Every request body and response is JSON. Malformed JSON should get a `422`, never a `500`. A 500
on a body you can fix is a server-side bug.

## Try it

```bash
curl -s http://127.0.0.1:8080/healthz
```

Expected: `{"ok":true,"backend":"runpod"}`. Then:

```bash
curl -s http://127.0.0.1:8080/healthz | uv run python -m json.tool
```

Expected: the same object, pretty-printed across several lines. Start the API first
(`uv run pitwall-api` with the README `export` lines); `/healthz` needs no token.

## Common confusions

- Single quotes are not valid JSON. Use double quotes for both keys and string values.
- Trailing commas are not valid JSON. The last item in an object or list has no comma after
  it.
- JSON writes `true` and `false`, not `True` and `False`. Python's `json.tool` follows JSON,
  not Python.
- A missing key is not the same as `null`. Know which the API sends before you assume.

## Check yourself

1. Is `{'a': 1}` valid JSON?

<details><summary>Answer</summary>

No. JSON keys and string values need double quotes, and the example uses single quotes.

</details>

## Go deeper

- [Route inventory](../../docs/sdlc/02-api-rest.md#3-route-inventory)
