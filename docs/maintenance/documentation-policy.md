# Documentation Policy

Audience: AI agents (orchestrator, backend, frontend, QA, DevOps) and the human maintainer.
Status: living constitution. Change it deliberately; treat divergence between parallel language versions as a bug.

## 0. The only reason documentation exists

Every document in this repository must help answer at least one of four questions:

1. What is this project?
2. What architectural decisions were made, and why?
3. How do we develop and improve it further?
4. How do we maintain what already exists?

If a piece of text answers none of these, it is noise. Do not write it. Do not keep it.

## 1. Result over process

We document outcomes and decisions, not the journey. The five tasks once spent on a TLS certificate are not preserved as five tasks in the docs; they collapse into a single line in the TLS document that states the decision and the mechanism. Process artefacts (per-task reports, debug logs) live under reports/ as local traceability and are explicitly not documentation. A reader who wants to understand the system should never have to read how we struggled to build it.

## 2. Code is the source of truth for "what"

Documentation does not restate code. It explains why the code is shaped this way and how a piece fits the whole. If a fact can be read directly from a file, the doc points to that file (path + symbol) and adds the rationale; it does not copy the implementation. Copied implementation rots the moment the code changes and then lies.

## 3. The trigger to update docs is a commit to main

Docs are reviewed and updated when code lands in main. A pull-request merge is one such event, not the only one: manual edits that bypass a formal PR (common in this project's frontend work) land in main too and must trigger the same review. Waiting only for formal merges leaves documentation silently behind real changes.

## 4. Two layers

Stable frame: what the project is, the stack, the data model, the module map, and recorded decisions with their rationale. This answers questions 1 and 2 and changes rarely.

Runbook and roadmap: how to run the system, how to extend it, how to fix the usual breakage, and what comes next. This answers questions 3 and 4 and changes on feature work.

Keep the layers separate so the frame does not churn with every task and the runbook does not get buried under philosophy.

## 5. Freshness over completeness

After a commit to main, determine which documentation sections the changed files affect and mark only those sections stale, then rewrite them. Never regenerate the whole corpus to fix one corner.

The file-to-section mapping is the ownership map. Until it is extracted into its own file (a candidate path is docs/maintenance/docs-ownership.md), the mapping is done by judgement: the agent or human reads the changed paths, identifies the affected doc sections, and tags them. A document that drifts silently is worse than a document carrying a visible stale marker, because the silent one trains readers to distrust everything.

## 6. What we do not document

The process of struggling; temporary hacks that are about to be deleted; secrets, tokens, passwords and certificate contents; raw generated snapshots (those belong in reports/ locally, not in docs and not in git as bulk blobs).

## 7. Agents: structure is automatic, meaning is authored

A deterministic scanner may produce structure: file lists, AST facts (classes, functions, imports), schema snapshots, git deltas. That part is safe to automate. But the judgement "what does this change mean for the four questions" is authored work, done by a human or by an LLM acting as an author under human review. Fully auto-generated prose that no one reviewed is a lying risk and is not accepted as documentation. Automation proposes the diff and the draft; a mind accepts the meaning.

## 8. Language and parallel versions

English is the primary language for agent-facing documents. Russian parallels exist for documents that are also a human-facing surface (project context, roadmap, handover). When both versions of a document exist, they are maintained in parallel and the English version is the reference for agents while the Russian version is the reference for the human. Divergence between them is a bug to fix, not a feature.

## 9. Agent context is a derivative, not a dump

The context handed to an agent is a compact extract of the frame plus a recent changelog plus a schema summary. It is never the full project snapshot. Concretely:

- scripts/ are tracked in git (they are the executable history of decisions) but enter agent context as a name index only, never as full bodies.
- reports/ raw files stay local; a small task-history extract may be tracked if useful.
- Full file bodies are pulled on demand for the specific component under discussion, not preemptively for everything.

This keeps the context window honest and cheap.

## 10. Do not automate prematurely

Background daemons and autonomous watcher-agents are not justified for local single-developer work, especially on Windows, where they are fragile and opaque to debug. Deterministic scripts plus git hooks plus human review are the preferred mechanism until a CI server or a real multi-contributor setup exists. Automate the boring deterministic part now; automate the autonomous part only when there is infrastructure to observe it.

## 11. Ownership note

Each tracked document has a clear primary audience (agents, human, or both) recorded in its header. The ownership map (file to doc section) is the single place that decides what a code change forces us to revisit. When that map is absent, fall back to judgement and err on the side of marking a section stale rather than leaving it quietly wrong.
