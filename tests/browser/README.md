# Browser tests

Install the pinned Node dependencies and browser binaries, then run the checks:

```bash
npm ci
npx playwright install chromium firefox
npm run check
npm run test:browser
```

Playwright starts GPO Studio through its CLI with a temporary SQLite database.
Fixtures are synthetic and are created through the public HTTP API. Chromium is
the primary browser baseline; the tagged smoke journey also runs on Firefox.

Set `GPO_STUDIO_TEST_PYTHON` to a Python executable with GPO Studio installed if
`uv` is unavailable locally. For example:

```bash
GPO_STUDIO_TEST_PYTHON=.venv/bin/python npm run test:browser
```

When unset, the server harness uses `uv` locally and the active `python`
executable in CI.

`catalogue-context.spec.mjs` uses independent browser contexts and real synthetic
ADMX folders with the same policy ID but different registry mappings. It checks
stale saves and a reload between bulk batches without relying on cross-tab hints.
It restores the harness's default source after each test.

`shared-read-scopes.spec.mjs` exercises current and retired read/preview/artifact
outcomes through actual UI controls. The maintained semantic caller inventory,
ownership rules and syntax-check limitations are documented in
[UI read ownership](../../docs/ui-read-ownership.md). New request consumers need
both a registry policy and meaningful behavioral coverage; an annotation alone
is not evidence that a callback still owns its UI.
