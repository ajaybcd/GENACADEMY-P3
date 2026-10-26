CODE REVIEW WORKBENCH

A Week 3 Streamlit app with three LangGraph specialists for correctness,
security and test gaps. Each specialist dynamically chooses read tools.
A deterministic orchestrator validates and ranks findings, then pauses for
human approval before a single GitHub COMMENT review can be posted.

QUICK START
From this folder in PowerShell:
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe -m streamlit run app.py
If the environment is already installed, run .\run.ps1.
Choose Explore demo first. It uses clearly marked fixtures, no model or account.

LIVE OPENAI SETUP
Choose GitHub pull request in the sidebar. In Connection settings, enter:
- A tool-capable model ID available to your OpenAI account.
- API base URL: https://api.openai.com/v1
- Your API key in the password field. Never paste it into chat or commit it.
- An optional GitHub token for public reads; a token is required for posting.
Alternatively copy .streamlit/secrets.toml.example to .streamlit/secrets.toml
and fill it locally, or set MODEL_NAME, MODEL_API_KEY (or OPENAI_API_KEY),
MODEL_BASE_URL and GITHUB_TOKEN as environment variables.
The app does not read .env files automatically. Pinecone is not used. Groq or
another compatible provider can be configured using its model ID and API URL;
provider-specific tool support must be verified in your account.
Use Check model connection to verify that the key is accepted and list available
models before running analysis. Native OpenAI requests use the Responses API;
reasoning models use an explicit low effort setting. Other compatible providers
continue to use Chat Completions. Provider errors show only safe status, code
and known parameter names; a provider failure stops subsequent specialist calls.

The selected model receives repository source and patches. Use a repository
you are authorized to send to that provider. A fine-grained GitHub token needs
Contents: read and Pull requests: read for private-source analysis, and
Pull requests: write to post. Restrict it to the intended repositories.
Password widgets and live client objects exist only in the Streamlit session;
credentials are not added to graph checkpoint values, exports or the post ledger.

HOW IT WORKS
1. Validate a github.com PR URL, read metadata and fetch Python changes at the
   pinned head commit. Check head and base again after loading the diff.
2. Run independent Correctness, Security and Tests specialists in sequence.
   Each has its own stateful model/tool loop. Available tools: list changed
   files, read patch, read source ranges, read pinned context, parse Python AST,
   literal search, save/read notes, and submit structured findings.
3. Consolidate exact duplicates and rank by severity. Evidence must match a
   unique full-line snippet in a reviewed file and contain an added diff line.
   Similar findings with different wording can remain for the user to reconcile.
4. LangGraph interrupt pauses the graph. The user selects finding IDs and
   explicitly approves posting. Rejection never writes to GitHub.
5. Revalidate the selected findings and check both commit SHAs and open status.
   Post a single review pinned to the analyzed commit, with AI disclosure.

The final commit check and GitHub POST are separate requests; GitHub does not
provide an atomic compare-and-post precondition here. If the PR changes in that
small interval, the review remains attached to the analyzed commit. Re-review
new commits when needed.

RELIABILITY AND LIMITS
- Maximum 100 changed files per PR, up to 15 reviewable Python files, 250 KB
  per source file, 40 KB per patch, 240 KB combined source/patch input.
- Unsupported/deleted files and missing/truncated patches are explicitly skipped.
  Missing context and specialist failures are warnings, not claims of coverage.
- At most 8 model turns per specialist, 24 total in this implementation (below
  the project ceiling of 25), and 32 tool calls per specialist. Provider retries
  are disabled so the counter represents actual attempted model calls.
- Each specialist can read up to six extra context files, at most 1 MB per file
  and 2 MB combined. Reads return at most 200 lines or 16,000 characters,
  with total line counts and an explicit has_more indicator. Full-file literal
  search finds relevant tests beyond the first page. Directory discovery helps
  agents locate real paths. Pinned content is cached across specialists within
  one run to reduce duplicate GitHub reads, with a 6 MB cache ceiling.
- No reviewed code, shell command, regex from the repository or AST is executed.
- GitHub reads retry transient failures at most three times. Writes are never
  retried automatically. The SQLite ledger claims a run before a write request.
- An uncertain write blocks another post for the same run. The UI can search
  GitHub reviews for its run marker. A new run has a different ID: inspect GitHub
  before creating a new run for a PR with an uncertain prior post.
- Checkpoints and review state are in memory for the current Streamlit session
  and process. The ledger persists locally in .review_state/posts.sqlite3.
  Restarting the server loses pending reviews. Ephemeral hosting can also lose
  the ledger; persistent hosting is needed for durable multi-user deployment.
- This MVP is for trusted local use. It has no authentication or multi-tenant
  authorization layer. Do not expose a shared-key server publicly as-is.

TESTING
Install requirements-dev.txt, then run:
  .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
Tests use scripted model outputs and a mock GitHub transport. They exercise
real LangGraph loops and interrupts, not a paid model or live GitHub write.
The offline demo is not the required three-real-sample evaluation.
requirements-lock.txt records exact installed versions from this Windows/Python
3.12 build. Use it for reproduction; requirements.txt permits compatible updates.

SUBMISSION WORK STILL REQUIRING YOUR ACCOUNTS
1. Enter your OpenAI API key and model ID. Run three real Python PRs or code
   samples, save their report JSON files and assess findings against the source.
2. Complete docs/evaluation-template.json with quality and runtime observations.
3. Use docs/submission-notes.txt to write the required Google Doc; replace
   pending claims with actual observations and links.
4. Record a demo of five minutes or less, including a live result.
5. Publish the code to your GitHub repository and submit the required links.
Repository: https://github.com/ajaybcd/GENACADEMY-P3. No Google Doc has been
created and no GitHub review has been posted by the build process. The supplied DOCX source files are excluded from Git.

FIRST LIVE REVIEW
The first live analysis used gpt-5.6-terra on psf/requests PR #6767, pinned to
11d68c17bcd66bd3e2c5137b0a7aad7f59967102. All three specialists completed in
17 model calls. The report contains one potential correctness finding and two
test suggestions; no security findings. Extra context reads failed and are
recorded as limitations. See docs/reports/real-1.json. These are proposed findings,
not independently confirmed bugs or measured precision. No GitHub post was made.
Two additional live evaluations are complete: PR #6963 produced two findings in
12 calls with no warnings; PR #7502 produced no findings in 14 calls after a
directory-search repair. Its bounded coverage and context-cap messages are retained.
All three selected reports and the comparison are in docs/three-sample-evaluation.txt
and docs/reports/. Human quality assessment remains pending.

FILES
app.py                    Streamlit interface and connection settings
review_app/domain.py      Diff parsing, evidence anchoring and validation
review_app/github.py      Bounded GitHub reads and one batched review write
review_app/agents.py       Three specialist model/tool loops and prompts
review_app/workflow.py     Orchestration, checkpointing, approval and posting
review_app/ledger.py       Persistent per-run duplicate-write guard
review_app/provider.py     Model connection setup and safe provider diagnostics
review_app/context.py      Paged pinned context, full-file search and read budgets
review_app/demo.py         Clearly labeled illustrative fixtures
tests/                    Behavior and Streamlit interaction tests
docs/                     Submission preparation and evaluation template

REFERENCES CHECKED DURING IMPLEMENTATION
https://docs.langchain.com/oss/python/langgraph/quickstart
https://docs.langchain.com/oss/python/langgraph/interrupts
https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request
https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
