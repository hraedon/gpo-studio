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
`uv` is unavailable locally.
