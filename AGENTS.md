# Instructions for AI coding agents

Everything written by or for AI agents (project context, plans of record, decision
logs, as-built notes) lives in `agent-docs/`, numbered in the order the work was done.
Read `agent-docs/README.md` first: it lists the files, their dates and the reading
order. The project context (what the system is, how to build and test it, the hard
rules and frozen interfaces) is `agent-docs/00_PROJECT_CONTEXT.md`; the newest plan is
the highest-numbered file.

Rules that apply to every agent, whatever tool runs it: never `git commit` or `git push`
(the owner commits); never run `deploy/deploy.sh`, `deploy/provision.sh` or the
simulator against production without explicit confirmation; code is ground truth, so
verify against the repo before asserting anything; keep new agent-authored planning
files in `agent-docs/` with the next number.

@agent-docs/00_PROJECT_CONTEXT.md
