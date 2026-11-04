# Release Checklist — Hardware RAG Agent

> Owner: W5. CI workflow YAML is owned by W1; this checklist only defines the
> release gate and the artifacts W5 must supply.

## 0. Preconditions

- [ ] W1 `ci.yml` is green on the release commit (backend Ubuntu/Windows,
      frontend test/lint/build).
- [ ] W2 server-side message truth and W3 HITL/checkpoint persistence are
      merged; the W5 E2E `xfail`/`skip` markers are removed.
- [ ] W4 audit/security work is merged; deny rows are written with `trace_id`.
- [ ] `git status` is clean; release commit is tagged from `master`.

## 1. Version and license

- [ ] One version source: `backend/app/main.py` and `frontend/package.json`
      agree (currently 0.2.0 vs 0.1.0).
- [ ] `CHANGELOG.md` follows Keep a Changelog + semver; `Unreleased` is empty.
- [ ] License decision recorded: Apache-2.0 + Commons Clause is **not** OSI
      open source. Either remove Commons Clause or label the project
      source-available in README.
- [ ] Tag `v0.3.0` (or the agreed semver) on the release commit.

## 2. Quality gates

- [ ] `python scripts/ci/run_eval_gate.py` passes offline (35+ samples,
      schema/ids/counts).
- [ ] Nightly/release eval reports pass all four metrics:
      `context_recall >= .80`, `faithfulness >= .75`,
      `answer_relevancy >= .80`, `context_precision >= .70`;
      weighted total >= 76.75; `scored_sample_count == sample_count`.
- [ ] `python scripts/ci/run_e2e.py` passes all five flows; no unexpected
      `xfail`/`skip`.
- [ ] `pytest` full backend suite is green on both CI OS targets.
- [ ] `npm ci && npm test && npm run lint && npm run build` is green.
- [ ] Trace contract: every HTTP response carries `x-trace-id`; every SSE
      frame carries `trace_id`/`seq` and `id:`; audit and TokenUsage rows
      carry the same `trace_id`; agent runs write non-null usage/cost.

## 3. Documentation and community

- [ ] `README.md` quickstart is verified on a clean machine (Python 3.11/3.12,
      Node 20/22; no eval dependencies required for the app).
- [ ] `CONTRIBUTING.md` documents test commands, commit style, and the
      W1/W5 CI ownership boundary.
- [ ] `SECURITY.md` documents local-only deployment, key handling, and the
      private vulnerability reporting path.
- [ ] `CODE_OF_CONDUCT.md` and `.github/ISSUE_TEMPLATE/` + PR template exist.
- [ ] `docs/api-contract.md` documents `trace_id`/`seq` in SSE and the new
      TokenUsage fields.
- [ ] Demo GIF/video and screenshots are current; repo description and topics
      are set.

## 4. Artifacts

- [ ] Source archive or installer built from the tagged commit.
- [ ] `backend/requirements.lock` (hashes) and `frontend/package-lock.json`
      are committed; clean-venv install verified.
- [ ] Release notes include upgrade/migration notes (Alembic + idempotent
      `ALTER TABLE` for `token_usage`/`tool_audit`).
- [ ] Rollback point: previous tag and database backup procedure documented.

## 5. Post-release

- [ ] CI badge is green on `master`.
- [ ] Issues/PRs use the templates; SECURITY reports are routed privately.
- [ ] Nightly eval and E2E jobs are scheduled (W1 wiring).
- [ ] Known gaps are listed in `KNOWN_BROKEN.md` (Agent usage extraction,
      W2/W3 persistence, hardware flashing untested).
