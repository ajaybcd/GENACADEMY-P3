# Week 3 Code Review Workbench

A Streamlit app with three LangGraph specialists for correctness, security, and missing tests. Each specialist chooses focused tools, then a deterministic step anchors findings to added lines of a pinned GitHub commit. A human selects findings before any GitHub review can be posted.

## Run locally

Use Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Start with **Explore demo**. For a live review, choose **GitHub pull request**, expand **Connection settings**, and enter your model API key and a PR URL. The OpenAI base URL is `https://api.openai.com/v1`; the initial model is `gpt-5.6-terra`. Public reads can be anonymous; GitHub rate limits may require a token. Private repositories need authorized read access. Posting additionally needs pull-request write permission and explicit approval in the app.

## Deploy on Streamlit Community Cloud

1. Sign in at [Streamlit Community Cloud](https://share.streamlit.io/).
2. Select **Create app**, then deploy an existing GitHub app.
3. Repository: `ajaybcd/GENACADEMY-P3`; branch: `main`; entrypoint: `app.py`.
4. In **Advanced settings**, select Python **3.12**.
5. Leave secrets empty for a public demo where each user supplies their own live-review credentials in the app. Deploy and wait for the build to finish.

For a private deployment where the owner wants server-side credentials, use the Cloud **Secrets** setting, following [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example). Never commit actual credentials. `MODEL_NAME` and `MODEL_BASE_URL` are optional configuration values. Store `MODEL_API_KEY` and `GITHUB_TOKEN` only in a suitable private deployment, or enter them for the current session through the app.

Pending review checkpoints last for the current server process/session. The local post ledger may be lost when Community Cloud restarts or replaces its filesystem. For this course deployment, use exports as the retained evidence; durable multi-user operation would require authentication and persistent storage.

## Three real samples

All three live runs used `gpt-5.6-terra`, completed all specialists, and paused for human review. No review was posted by these evaluation runs.

| PR | Findings | Model calls | Limitations |
| --- | ---: | ---: | --- |
| [Requests #6767](https://github.com/psf/requests/pull/6767) | 3 | 17 | Original before-fix context-read baseline |
| [Requests #6963](https://github.com/psf/requests/pull/6963) | 2 | 12 | None reported |
| [Requests #7502](https://github.com/psf/requests/pull/7502) | 0 | 14 | Directory-search coverage and context limits |

Reports: [sample 1](docs/reports/real-1.json), [sample 2](docs/reports/real-2.json), [sample 3](docs/reports/real-3.json). The [evaluation summary](docs/three-sample-evaluation.txt) preserves exact commits, run IDs, findings and limitations. Anchoring validates source locations; independent assessment of the proposed issues remains pending.

## Verification and implementation

The context reader supports pages, full-file literal search, bounded directory search and caching at pinned commits. The regression suite contains **42 passing tests**. To run it, install `requirements-dev.txt` and execute `python -m pytest -q -p no:cacheprovider`.

- `app.py`: Streamlit interface and approval controls
- `review_app/`: specialist graphs, GitHub access, evidence validation and posting guard
- `tests/`: behavioral, retrieval, provider and Streamlit interaction checks
- `docs/`: evaluation reports, verification notes and submission preparation
- [README.txt](README.txt): full setup details and implementation limits

Direct runtime dependencies are pinned in `requirements.txt`. The Windows/Python 3.12 build's complete package snapshot is retained separately in `requirements-lock.txt` for reference.
