# Change: Deterministic two-adapter demo
## Why
Prove both command adapters use the same bounded Stage workflow and metrics.
## Spec Delta
Create one deterministic result file inside the configured business boundary.
## Acceptance Criteria
- AC-1: When the demo Stage runs, the system shall create the expected result
## Scope
- StoryLoop v2 demo only
## Test Mapping
| AC | Test | Level |
|----|------|-------|
| AC-1 | judge.py#implemented | L1 + L2 |
## Out of Scope
- Network, deployment, push, and production data
