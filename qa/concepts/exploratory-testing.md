# Exploratory testing

## In one sentence

Exploratory testing is testing without a script, guided by a charter, taking notes as you go.

## Why it matters when testing Pitwall

Scripted journeys cover the known paths. Exploration finds the unknown ones, the bugs the
journeys did not think to look for. A charter keeps the session focused without scripting it.

## Try it

```bash
sed -n '/^## Pitwall charter list/,$p' qa/handbook/exploratory-testing.md | grep -c '^[0-9]*\. Explore'
```

Expected: `14`, one for each charter in the Pitwall charter list. The list is one place to start,
and writing your own charter is fine.

## Common confusions

- Exploration is not random clicking. It has a charter, a time box, and notes.
- The never-run list still applies (coach rule R2). Exploring means trying unusual inputs and
  orders on the local test stack, never reaching for real credentials or paid services.
- "What worked" is a finding too. It tells the maintainer an area is in better shape than the
  docs say.
- One session, one charter. New idea, new session.

## Check yourself

1. What are the three parts of a charter?

<details><summary>Answer</summary>

The area, the resources or technique, and the kind of problem to discover.

</details>

## Go deeper

- [Exploratory testing handbook](../handbook/exploratory-testing.md)
- [Running a session](../handbook/exploratory-testing.md#running-a-session)
