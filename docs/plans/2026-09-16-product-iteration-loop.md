# Product Iteration Loop Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn StoryLoop into a generic, repo-local product iteration harness that locks a logical foundation, co-creates and approves OpenSpec changes, generates independent smoke acceptance tests, runs implementation automatically, and produces traceable evidence before a safe local merge.

**Architecture:** Keep the existing v1/v2 engines compatible and add a focused project façade. A baseline service models the single repository as protected foundation paths, feature module roots, and audited integration points. A change service owns OpenSpec contracts and approval digests. An external acceptance orchestrator owns generated smoke tests, implementation retries, evidence, Git commits, and local no-fast-forward merging.

**Tech Stack:** Python 3.11+ standard library, argparse, subprocess argv execution, dataclasses/JSON, unittest/pytest-compatible tests, Git CLI.

**Important:** The StoryLoop development workspace currently has no `.git` metadata. Run all code and test steps here, but skip StoryLoop repository commits until Git metadata is available. Git behavior itself is verified with temporary Git repositories.

---

### Task 1: Define the repo-local configuration and project detector

**Files:**
- Create: `storyloop/project_profile.py`
- Create: `tests/test_project_profile.py`
- Modify: `storyloop/models.py`

**Step 1: Write failing tests**

Cover detection for Maven, Gradle, npm/pnpm, and Python manifests; verify existing CI files are recorded as protected and never selected as generated outputs. Cover explicit command override and rejection of shell strings where an argv array is required.

**Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_project_profile.py -q`

**Step 3: Implement the minimal detector and validated configuration model**

Return a language-neutral profile containing project test commands, likely module roots, integration files, and CI protection patterns. Keep commands as `list[str]`.

**Step 4: Run focused tests and regression tests**

Run: `python -m pytest tests/test_project_profile.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when repository metadata is available**

`git commit -m "feat(project): add generic repository profiling"`

### Task 2: Bootstrap and lock a single-repository baseline

**Files:**
- Create: `storyloop/baseline.py`
- Create: `tests/test_baseline.py`
- Modify: `storyloop/cli.py`

**Step 1: Write failing tests**

Create temporary clean Git repositories and assert bootstrap records HEAD, original branch, baseline version, tracked-file SHA-256 values, foundation paths, module roots, integration paths, and protected CI files. Assert dirty and non-Git repositories fail clearly. Assert initialization never changes CI files.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_baseline.py -q`

**Step 3: Implement baseline capture**

Add a `storyloop init <existing-repo>` path that detects an existing Git repository and writes `.storyloop/config.json` plus `.storyloop/baseline.json`. Preserve legacy initialization for a new/non-existing StoryLoop workspace.

**Step 4: Verify focused and regression tests**

Run: `python -m pytest tests/test_baseline.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(baseline): lock logical foundation in one repository"`

### Task 3: Add the OpenSpec product change contract

**Files:**
- Create: `storyloop/changes.py`
- Create: `tests/test_changes.py`
- Modify: `storyloop/agents.py`

**Step 1: Write failing tests**

Test creation from a short product idea, deterministic change IDs under an injected clock, structured stories and acceptance criteria, unresolved questions, revision history, and concise OpenSpec files. Test strict validation of unique IDs and criterion-to-story references.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_changes.py -q`

**Step 3: Implement draft creation and revision**

Reuse the product agent schema where practical. Persist `proposal.md`, `stories.json`, `acceptance.json`, and `traceability.json` beneath `openspec/changes/<change-id>`. Do not require technical task documents from users.

**Step 4: Verify**

Run: `python -m pytest tests/test_changes.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(openspec): add product-level change contracts"`

### Task 4: Add approval locking and invalidation

**Files:**
- Modify: `storyloop/changes.py`
- Create: `tests/test_change_approval.py`
- Modify: `storyloop/cli.py`

**Step 1: Write failing tests**

Assert approval is refused while questions remain or data is invalid. Assert approval records the user-facing contract digest. Assert any subsequent change to stories or acceptance criteria invalidates approval. Assert technical/generated files are excluded from the product approval digest.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_change_approval.py -q`

**Step 3: Implement approval lifecycle**

Add `storyloop change` for draft/revision and an approval command accepting the selected change ID. Make the approved contract immutable to the automatic implementation stage.

**Step 4: Verify**

Run: `python -m pytest tests/test_change_approval.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(openspec): lock user-approved product contracts"`

### Task 5: Generate and protect independent smoke tests

**Files:**
- Create: `storyloop/smoke.py`
- Create: `tests/test_smoke.py`
- Modify: `storyloop/agents.py`

**Step 1: Write failing tests**

Test smoke manifest validation, one-or-more test references per acceptance criterion, argv-only commands, timeouts, output paths, protected digests, and rejection of any output under CI configuration paths. Test that generated tests must demonstrate RED before implementation begins.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_smoke.py -q`

**Step 3: Implement smoke generation protocol**

Persist tests under `acceptance/smoke/<change-id>`. Add an agent contract for generating test files and `manifest.json`, then validate all paths and hashes before execution.

**Step 4: Verify**

Run: `python -m pytest tests/test_smoke.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(acceptance): generate protected core-flow smoke tests"`

### Task 6: Provide a CI-neutral smoke runner

**Files:**
- Modify: `storyloop/smoke.py`
- Create: `tests/test_ci_runner.py`
- Modify: `storyloop/cli.py`

**Step 1: Write failing tests**

Assert `storyloop ci <project> --change <id>` runs exactly the manifest commands, enforces timeouts, preserves stdout/stderr evidence safely, and returns non-zero on failure. Snapshot all known CI files before and after and assert byte equality.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_ci_runner.py -q`

**Step 3: Implement runner**

Use the existing safe process runner or a small argv-only equivalent. Store evidence outside generated test source and redact environment-sensitive values from summaries.

**Step 4: Verify**

Run: `python -m pytest tests/test_ci_runner.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(ci): add independent smoke-test entry point"`

### Task 7: Enforce feature, integration, and foundation boundaries

**Files:**
- Create: `storyloop/boundaries.py`
- Create: `tests/test_boundaries.py`
- Modify: `storyloop/baseline.py`

**Step 1: Write failing tests**

Cover allowed new feature modules, limited integration file edits, forbidden foundation edits, protected smoke/OpenSpec edits by implementation agents, file renames, deletions, symlinks, path traversal, and untracked files. Assert a foundation violation produces a structured `BASE-*` proposal instead of silently widening permissions.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_boundaries.py -q`

**Step 3: Implement boundary audit**

Compare Git status and SHA-256 baseline data using normalized repository-relative paths. Represent violations as typed results consumable by the orchestrator and report generator.

**Step 4: Verify**

Run: `python -m pytest tests/test_boundaries.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(governance): enforce logical foundation boundaries"`

### Task 8: Add safe local Git lifecycle

**Files:**
- Modify: `storyloop/git_workflow.py`
- Create: `tests/test_project_git_lifecycle.py`

**Step 1: Write failing integration tests**

In temporary repositories assert clean-tree preflight, creation of `feature/<change-id>`, automatic commits with hooks disabled, preservation of the source branch, successful `--no-ff` merge, no configured remote interaction, and branch preservation on failure.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_project_git_lifecycle.py -q`

**Step 3: Implement lifecycle operations**

Extend the existing Git adapter with explicit start, commit, merge, and inspect operations. Never add push, reset-hard, forced checkout, or branch deletion.

**Step 4: Verify**

Run: `python -m pytest tests/test_project_git_lifecycle.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(git): automate safe local feature lifecycle"`

### Task 9: Build the external autonomous acceptance loop

**Files:**
- Create: `storyloop/project_loop.py`
- Create: `tests/test_project_loop.py`
- Modify: `storyloop/agents.py`

**Step 1: Write failing orchestration tests**

Use fake agents and commands to test the full state machine: approved contract, smoke generation, RED proof, branch creation, implementation attempt, regression, smoke execution, review, retry, final commit and merge. Cover pause states for base impact, contradictions, unsafe writes, budget exhaustion, and manual acceptance.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_project_loop.py -q`

**Step 3: Implement the orchestrator**

The Loop, not the implementation agent, owns state transitions and pass/fail decisions. Persist state atomically after each transition so interrupted runs can resume. Feed ordinary test failures back to the agent automatically within the configured attempt budget.

**Step 4: Verify**

Run: `python -m pytest tests/test_project_loop.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(loop): automate approved product iterations"`

### Task 10: Generate requirement-linked evidence reports

**Files:**
- Create: `storyloop/change_report.py`
- Create: `tests/test_change_report.py`
- Modify: `storyloop/project_loop.py`

**Step 1: Write failing tests**

Assert every acceptance criterion maps to story, smoke test, command, outcome, evidence hash, and implementation commit. Assert the report cannot be marked passed with missing links, stale evidence, failed commands, modified protected tests, or incomplete criteria.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_change_report.py -q`

**Step 3: Implement JSON and Markdown reports**

Write reports to `reports/storyloop/<change-id>/`. Keep machine-readable truth in JSON and render a concise Markdown audit view. Record the merge commit after successful local merge without changing the approved contract.

**Step 4: Verify**

Run: `python -m pytest tests/test_change_report.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "feat(report): add end-to-end acceptance traceability"`

### Task 11: Complete the product-facing CLI and documentation

**Files:**
- Modify: `storyloop/cli.py`
- Modify: `README.md`
- Create: `docs/product-iteration-guide.md`
- Create: `tests/test_product_cli.py`

**Step 1: Write failing CLI tests**

Cover concise help and the intended journey: `init`, `change`, `approve`, automatic `run`/resume, `status`, `ci`, and `report`. Assert technical v2 commands remain available but are not required by the product flow.

**Step 2: Verify RED**

Run: `python -m pytest tests/test_product_cli.py -q`

**Step 3: Implement the façade and user guide**

Make the normal workflow discoverable from `storyloop --help`. Include PowerShell examples, state diagrams in Mermaid, failure recovery, explicit statement that no CI file is changed, and an explanation of the foundation-change workflow.

**Step 4: Verify**

Run: `python -m pytest tests/test_product_cli.py -q`  
Run: `python -m pytest -q`

**Step 5: Commit when possible**

`git commit -m "docs(cli): document the product iteration workflow"`

### Task 12: End-to-end demonstration and release verification

**Files:**
- Create: `examples/generic-project/README.md`
- Create: `tests/test_product_e2e.py`
- Modify: `CHANGELOG.md` if present

**Step 1: Add an end-to-end temporary-repository test**

Bootstrap a small generic project, create and approve a fixture change, generate a deliberately failing smoke test, let a deterministic fake implementation satisfy it, verify report traceability and local no-fast-forward merge, and prove existing CI fixtures remain unchanged.

**Step 2: Run security and quality checks**

Run: `python -m pytest tests/test_product_e2e.py -q`  
Run: `python -m pytest -q`  
Run the repository's configured lint/type checks when present.

**Step 3: Inspect safety invariants**

Search for shell execution, push/reset/delete Git operations, secret-looking fixtures, unbounded paths, and files over the project size guideline. Resolve all actionable findings.

**Step 4: Perform independent implementation review**

Request a focused review of behavior, boundary enforcement, Git safety, traceability completeness, and regression compatibility. Fix all high-confidence findings and rerun the full suite.

**Step 5: Produce the final report**

Summarize implemented commands, architecture, tests executed, limitations, and a copy-paste demonstration. Clearly note that real AI-backed runs require a configured adapter and that pushing remains the user's decision.
