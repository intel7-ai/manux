# Root cleanup audit

2026-11-05; preview `589cc9a8d338e40df8edc005825caf0c9a6329fc`.
Proposal accompanying the [model/acceleration investigation](../apps/manux/docs/acceleration-and-model-investigation.md).
No files were deleted or moved. The active worker checkout was inspected read-only.

## Findings and disposition

The clutter is mostly **three application generations sharing entry points**, not
unreferenced documents. `docs/README.md` already marks historical documents and
explicitly preserves their paths for Jira/PR/presentation links. Deleting or moving
them without redirects would lose useful provenance and break those links.

| Root item | Evidence | Recommended disposition |
| --- | --- | --- |
| `README.md`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` | Repository entry points; CLAUDE delegates to AGENTS | Keep; make current v0.4 entry points unambiguous |
| `apps/manux/` | Current v0.4 application | Keep; default development destination |
| `hand-mouse/` | `.github/workflows/hand-mouse-release.yml` still serves v0.3 release | Keep until verified v0.4 release workflow replaces it |
| `package.json`, lockfile, `scripts/`, `tests/`, `harness/` | `ci.yml` runs npm verify; reporting uses task-report.mjs | Keep shared tooling; disentangle it from old app dependencies before retiring npm app code |
| `.node-version`, `.nvmrc` | setup-node uses `.node-version`; prepare-release.mjs includes both | Keep until callers and supported local tooling are migrated |
| `src/`, `public/`, `desktop/`, `server.js` | npm scripts, server tests, browser probes and Electron packaging refer to them | Retire v0.2 coherently in a dedicated PR; moving just server.js breaks imports |
| `electron-builder.yml` | desktop-build.mjs, test-installed-update.mjs and desktop-package-config.test.js use it | Retire with Electron tooling, not as an isolated file deletion |
| Four `MANUX *.cmd` launch/check scripts | README/onboarding reference them; commands run npm or old start-manux.ps1 | Strongest root confusion: move legacy app launchers together after updating references; retain shared verification via an explicitly named command |
| `start-manux.ps1`, `start-manux-lan.ps1`, `stop-manux-lan.ps1` | MANUX 실행.cmd, LAN guide, prepare-handoff.ps1 reference these paths | Archive with v0.2 launchers and update path assumptions together |
| `docs/` | Current branch/harness/evaluation material mixed with historical v0.2/plans | Keep current index; add archival notices or forwarding pages for moves |
| `.local/`, `dist/`, `test-results/`, `node_modules/` in shared checkout | Local reports/build outputs/dependencies; ignored by existing rules | Generated does not mean disposable during worker 1's run; leave intact |
| `.worktrees/` in shared checkout | This task's separate Git worktree | Never delete as clutter; lifecycle through git worktree commands after task completion |

The new worktree contains no extra standalone root research files such as
`solution.md`. An AGENTS reference to that filename is not evidence that such a
tracked root file currently exists; inventory exact files instead of guessing.

## Concrete cleanup sequence

1. **Navigation first.** Update CONTRIBUTING's opening paragraph: it currently routes
   all readers to old `docs/design`, `workstreams` and `docs/contracts` before its
   v0.4 notice. Add current uv run/pytest instructions before the legacy npm section.
   Root AGENTS has the same mixed-generation reading list after its v0.4 banner.
   Preserve branch/report rules and link historical contracts only for historical code.
2. **Remove contradictory status prose.** At this baseline, `apps/manux/AGENTS.md`
   first describes S6 packaging and later says packaging does not exist. `next-steps.md`
   says features/cache/hot reload are missing though #69 supplies them. Mark those
   paragraphs as dated history and retain one current-state source. Rebase this cleanup
   after worker 1's #72 because that PR edits these same current-status documents.
3. **Archive v0.2 as a unit.** Decide whether to retain executable source under
   `archive/web-v02/` or use the last reachable Git commit/tag as its archive. Inventory
   consumers with `rg -n 'server\.js|electron-builder|start-manux|stop-manux'` across
   workflows, scripts, tests and docs. Separate harness/report checks from legacy app
   checks first. Update prepare-release.mjs and prepare-handoff.ps1, not just launchers.
   Test from a clean clone. No blanket removal of checks to make the move pass.
4. **Preserve links.** Move obsolete plans under `docs/archive/2026-10/` only if the
   benefit exceeds redirect maintenance. Leave a small dated forwarding page at old
   paths and update relative links. Keep active M1 evaluation materials accessible.
5. **Retire v0.3 later.** Only after the actual v0.4 installer, updates, real Windows
   control and rollback path pass, switch the release workflow and archive v0.3.
6. **Local output cleanup separately.** Inspect explicit paths, running processes and
   worktree ownership; preserve reports/data/models. Do not run git clean -fdx or
   recursively delete shared build folders while another worker is active.

## Suggested final root

`apps/`, `docs/`, `.github/`, agent/contributor/readme entry points and the minimum
shared harness/tooling configuration. Legacy executable trees/launchers can leave
the root once all live consumers are migrated. Keep hidden tool configuration where
the tool requires it rather than moving it for appearance.

## Verification for a future cleanup PR

Use `git ls-files`, reference searches including hidden `.github`/`.claude` files,
relative Markdown link checks, clean-clone npm installation/verify, v0.4 pytest,
packaging workflow checks, launcher smoke checks and report rendering. Test any
retained legacy build path. Unknown external links are a reason to leave forwarders.
This audit establishes candidates/dependencies; it does not claim those future
migration checks or a physical cleanup have already run.
