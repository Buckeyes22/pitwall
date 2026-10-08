# HTTP and status codes

## In one sentence

Programs talk to the API over HTTP, and every response carries a status code that says how it
went.

## Why it matters when testing Pitwall

API testing is mostly checking that the right code comes back. The code is the first signal;
the body is the second. The table below is the short list every Pitwall tester should know.

| Code | Meaning |
| --- | --- |
| 200 | OK |
| 201 | Created |
| 401 | Missing or wrong credential |
| 402 | Refused by the budget gate |
| 403 | Valid credential without permission |
| 404 | Not found |
| 422 | Request body invalid |
| 429 | Too many requests (see `Retry-After`) |
| 500 | Server error, always a bug |

## Try it

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz
```

Expected: `200`. Start the API first (`uv run pitwall-api` in a terminal with the README
`export` lines). `/healthz` is public, so it needs no token.

## Common confusions

- 401 means the credential is missing or wrong. 403 means the credential is valid but does not
  have permission for that route.
- 4xx means a request problem. 5xx means a server problem. The latter is always a bug.
- A 200 with an error inside the body is still worth reporting. The code is one signal; the
  body is another.
- 429 carries a `Retry-After` header. Wait at least that long before retrying.

## Check yourself

1. Which code proves the budget gate worked?

<details><summary>Answer</summary>

`402`. The request was refused by the budget gate before any provider work.

</details>

## Go deeper

- [Failure modes & error types](../../docs/sdlc/02-api-rest.md#6-failure-modes--error-types)
- [Journeys J07, J08, J13, J14, J15](../../docs/operator/user-journey-catalog.md)
