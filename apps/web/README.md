# PharmAssist Kaggle Web (Vite)

This is the demo UI for the Kaggle submission.

Local dev:

```bash
make setup
make web-dev
```

End-to-end test (starts API + web and runs Playwright):

```bash
make e2e
```

Notes:
- The UI is intentionally minimal for the hackathon demo: start a synthetic run,
  stream progress events (SSE), and display placeholder artifacts.
