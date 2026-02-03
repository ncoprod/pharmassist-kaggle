# PharmAssist AI — Kaggle MedGemma Impact Challenge

Synthetic-only demo application for the **Kaggle MedGemma Impact Challenge (2026)**.

Key principles:
- Uses **MedGemma / HAI-DEF** models (mandatory for Kaggle).
- Demonstrates an **agentic workflow** (tool-calling + trace).
- **No real patient data**. Synthetic data only.

## Quick Start (WIP)

Prereqs:
- Python 3.10+
- Node 20+ (any recent LTS is fine)

Local dev:

```bash
make setup

# terminal 1
make api-dev

# terminal 2
make web-dev
```

Health check:

```bash
curl http://localhost:8000/healthz
```

## License

This repository is open source (see `LICENSE`).
