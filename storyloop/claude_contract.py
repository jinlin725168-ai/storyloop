"""Prompt contracts shared by the Claude Code example adapter."""
SCHEMA_HINTS = {
    "product": (
        "Exact shape: {version:1, goal:string, non_goals:[string], capability?:slug, "
        "clarifications?:[{session,question,answer}], questions:[{id,text,options:[string],"
        "recommended?:string,why?:string}], stories:[{id,title,priority:'P1'|'P2'|'P3',"
        "independent_test:string,as_a,want,so_that,rules:[string],out_of_scope:[string],"
        "depends_on:[string],acceptance:[{id,given,when,then,kind:'automated'|'manual'}]}]}. "
        "IDs like S1/A1/Q1 must be unique across the whole product. No extra fields anywhere."
    ),
    "acceptance": (
        "Exact shape: {files:[{path,content}], manifest:{version:1, tests:[{id, criteria:[string], "
        "command:[string], timeout_seconds:int}]}}. The suite root is payload.suite_root; `path` is "
        "relative to it. `command` runs from the repository root with a minimal environment, so "
        "reference files as <suite_root>/<file> and use \"{python}\" for the interpreter. "
        "Every automated criterion id must appear in some test's criteria. No extra fields."
    ),
    "implement": ("Exact shape: {summary:string, questions:[string], design:string}; design is Markdown with "
                  "## Context, ## Goals, ## Tests First (each test: observed failure, then green), "
                  "## Decisions (Choice/Rationale/Alternatives), ## Risks And Trade-Offs, ## Files."),
    "characterize": "Exact shape: {summary:string, touched:[string], tests:[string], questions:[]}; no extra fields.",
    "design": ("Exact shape: {summary:string, touched:[string], design:string, questions:[string]}; design is Markdown with "
               "## Context, ## Goals, ## Decisions, ## Files, ## Data And Interfaces, ## Risks And Trade-Offs. No extra fields."),
    "review": "Exact shape: {verdict:'pass'|'fail'|'needs_input', summary:string, findings:[object], questions:[string]}.",
    "survey": ("Exact shape: {documents:[{path,content}]}; path relative to payload.foundation_root, Markdown only, "
               "every name in payload.required_documents present. No extra fields."),
}
VERIFY_HINT = (
    "Verify only the supplied findings in this single session using the engine-selected strong model. "
    "Answer each finding id exactly once. Return ONLY {results:[{id:string,confirmed:boolean,reason:string}]}. "
    "Do not produce a new review verdict or fan out to perspective reviewers."
)
RULES = (
    "Final answer must be ONLY one JSON object, no markdown fences, no prose.\n"
    "Only read-only git (status/diff/log/show); never add, commit, stash or switch branches. "
    "Do not create Git-ignored files (build output, caches, logs) except under the project's "
    "declared scratch paths: StoryLoop snapshots the whole tree and fails the run otherwise.\n"
    "Shell commands are pre-approved only as plain single commands: no `export`, `&&`, `;` or "
    "pipes. Nobody can answer permission prompts, so never ask for approval; if a command is "
    "denied, rerun it as one plain command.\n"
    "Questions are only for genuine product ambiguities. Never ask about tooling, permissions or "
    "the environment: skip the affected check, say so in the summary, and let the external "
    "acceptance run verify the result.\n"
    "payload.answers lists human decisions on questions raised in earlier attempts "
    "({attempt, question, answer}); follow them and never ask an answered question again.\n"
)
RESUME_PROMPT = (
    "继续完成上一条请求中尚未完成的工作。已经写入磁盘的文件都还在，不要重做已完成的部分。"
    "完成后仍然只输出一个 JSON 对象。"
)
REPAIR_PROMPT = (
    "上一条回复不是一个 JSON 对象。工作已经完成的话不要重做，现在只输出那个 JSON 对象："
    "不要 markdown 围栏，不要任何解释文字。"
)
REPAIR_LIMIT = 2  # a finished session that ended in prose is asked for its JSON, like the Codex adapter

# Review fans out to focused perspectives (parallel sessions, same evidence) and a lead merges them.
# The engine protocol is untouched: it still receives one {verdict, summary, findings, questions}.
REVIEW_PERSPECTIVES = {
    "correctness": (
        "Focus only on correctness: walk every automated and manual acceptance criterion of the contract "
        "against the code and the layer evidence; check edge cases, error handling, data consistency between "
        "list/detail/export paths, and that nothing outside the stories was changed."
    ),
    "security": (
        "Focus only on security: injection (SQL built from strings, unbound parameters), authorization "
        "(permission strings on every endpoint, ownership checks), sensitive data in logs or responses, "
        "input validation, secrets or credentials in code or config. Cite file and line for every finding."
    ),
    "architecture": (
        "Focus only on conformance to the base and the plan: payload.design_plan (if present), the foundation "
        "documents extension.md, building-blocks.md and modules.md. Re-implemented building blocks, files in "
        "the wrong layer, forbidden dependency directions, or deviations from the plan without a stated reason "
        "are findings."
    ),
    "oracle-strength": "Focus on weak assertions, deleted tests and mocks replacing integration criteria.",
    "tests": (
        "Focus only on test quality: the implementer's design record ## Tests First must match real test "
        "files that exercise the claimed behaviours; characterization tests must cover every touched path; "
        "assertions must be meaningful (no tautologies, no tests that pass without the change)."
    ),
}
# Implement can split a full-stack change into per-layer lanes: a lead session plans the lanes from the
# architect's design_plan, lane sessions work in parallel on disjoint path prefixes, and the same lead
# session (resumed, so it keeps the plan in context) integrates, runs the checks and writes the record.
LANE_PLAN_MARK = "[implement lead: plan lanes]"
LANE_MARK = "[implement lane: {name}]"
LANE_INTEGRATE_MARK = "[implement lead: integrate]"
LANE_PLAN_RULES = (
    "You are the lead implementer. Do NOT write any file in this step. Read payload.design_plan and decide "
    "which of the configured lanes have work in this change; each lane is one layer owned by one "
    "parallel implementer. Return ONLY {lanes:[{name:string, task:string}]} using only configured lane "
    "names; `task` is a precise brief for that lane (files to create or modify, interfaces to expose or "
    "consume, tests to write first). Lanes that have nothing to do are omitted."
)
LANE_RULES = (
    "You are one lane of a parallel implementation. Only create or modify files under the lane's path "
    "prefixes listed below; other lanes are editing other paths right now, so never touch them and never "
    "run project-wide formatters, git commands that stage or the full regression. Work test-first within "
    "your layer. Return ONLY {summary:string, files:[string], questions:[]}: `files` lists every path you "
    "created or modified."
)
LANE_INTEGRATE_RULES = (
    "Every lane has finished; their reports follow. Integrate: reconcile interfaces between layers, run the "
    "project's build and unit/integration commands, fix whatever breaks (in any lane's files), and then "
    "return the final implement result exactly as {summary:string, questions:[string], design:string}; "
    "the design's ## Tests First must cover the tests of every lane."
)
