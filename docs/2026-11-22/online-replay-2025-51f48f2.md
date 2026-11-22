# QA retest: 2025 online replay, `exp/online-replay-2025` @ 51f48f2

Tester (QA), 2026-11-22 (Asia/Bangkok, UTC+7). This was a read-only review. Nothing in `/workspace/tpr-test` was modified, committed or pushed. The work used fresh copies in `/workspace/qa-scratch2/`: `repo` for the two `--stage all` runs, `repo_pytest` for the suite, and `repo_probe` (cold cache) for the probes. The previous report was `online-replay-2025-54eeb6d.md`.

## Build identity
- HEAD is `51f48f27c563d23ae604a07e51487d8566f28144` on `exp/online-replay-2025`, and the tree is clean (0 entries in `git status --short`).
- There is no upstream. 54eeb6d is an ancestor, so no history was rewritten.
- There are 3 new commits: 3ef11fc (drift demos, gate in loop, H1 tests), 5cf203d (M3/L1 e2e tests, rounded config, cold-cache outputs) and 51f48f2 (README).
- `main` is `2e45adf` before and after; its reflog shows only the clone. GitHub main `5191c99` has `2e45adf` as an ancestor.

## Checks 1-8

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | Suite | **PASS** | `186 passed, 3 skipped` in 9m23s, slowed by a parallel `--stage all`. That is base 145 + 41 new tests. The same 3 MOTS/raw-data skips appear. The 10 `slow` tests run by default (`-m slow` selects 10/189). Running the suite left the copy's tree clean, so no test writes tracked files. |
| 2 | Reproduction | **PASS** | `reproduce.json` `all_match: true`, regenerated twice. Champion 6.5021 / 5.5909 / 4.5366. Online-trials values match at 4 dp. |
| 3 | DQ | **FAIL, waived-pending** | Unchanged and pending the user's decision. The normal bucket is 0.8927 against a target of 0.93; total arrivals + rain only is 0.9517. v1 = 0.5392316017. |
| 4 | Leakage | **PASS** | The new e2e poison tests pass. My probes against the new code found no leakage (see below). |
| 5 | Selection hygiene | **PASS** | The e2e test `test_2025_labels_cannot_change_the_2024_selection` passes with a byte-identical `config.json`. My earlier spy saw only 2024 rows, and the selection code is unchanged apart from the rounding. |
| 6 | Drift | **PASS (limitation documented)** | `test_approved_adwin_cannot_fire_on_a_step_that_starts_inside_the_replay_year` passes. My in-year probe agrees: a flat 0.05 seed stepped to 0.2, 0.5, 0.9 or 1.0 from 2025-01 with PSI on every month gives no cut and no trigger. `month_action` asserts `prepare_retrain`. The old-2025 reproduction is now in pytest and in the stage output: served `0.04788460348726727`, 0 promotions, **0 rollbacks**. PSI-only gives `watch`, and `adwin_direction: up` still ignores a downward cut. |
| 7 | Artifacts | **PASS** | Before run 1 I deliberately corrupted `predictions.parquet` (every prediction ×2). Run 1 output still matched the committed files to ≤ 1.2e-9, so the cache was cleared. Run 2 deleted all 38 tracked CSV/JSON/JSONL/PNG files, and they all came back non-empty. All 12 PNGs are byte-identical to the committed ones. `config.json` has sha256 `0330d57c…` in the committed tree, after run 1 and after run 2: **byte-stable**. Run 1 and run 2 differ by ≤ 9.1e-11 (RF float noise). The README headline test passes on the regenerated `results.csv`. |
| 8 | Git hygiene | **PASS (with documented exception)** | `main` is untouched, there is no upstream and nothing was pushed. The README's "How the work was committed" section discloses the combined steps (0+2, 3+4) and the order of the rain commit, and the QA follow-ups are separate commits. Strictly, the plan's one-commit-per-step rule is still not met; it is disclosed rather than fixed. |

## My probes against 51f48f2
- **In-year ADWIN step** (`probe_drift.py`): no cut and no trigger for steps 0.05 → 0.2 / 0.5 / 0.9 / 1.0 from January. With the step planted in the seed, it cuts in 2025-08, but with no PSI there is no trigger. With 3 PSI months and no error change, the result is 3× `watch`.
- **Gate placement** (`probe_gate.py`): a stream poisoned from 2025-07 with the gate on, instrumenting `Server.__call__`, `before_month` and the log callback:
  - The gate ran for 2025-01..2025-08 and raised `GateError: 2025-08: data-quality thresholds exceeded: consistency_thai_foreign`.
  - Served months were 2025-01..2025-07 only. Decided/logged months were 2025-01..2025-07 only.
  - **Nothing in 2025-08 or later was served or decided.** M1 is confirmed fixed.
- **Full loop poison** (`probe_loop.py`), T = 2025-07 with gate off:
  - The seed differs by at most 9.7e-17.
  - Served forecasts for 2025-01..07 differ by at most 5.2e-16 relative.
  - No log column before T differs beyond 1e-9.
  - No decision column at T (PSI, EWA weights, served model) differs.
  - The events before T are identical.
- **PSI**: 8/8 identical (1e12 and NaN poisoning × T = 2024-06, 2025-03, 2025-07, 2023-04).
- **ADWIN seed**, T = 2024-07: the first 18 values differ by at most 9.7e-17. 2024-07 itself changes (0.058 → 1.0), as expected.
- **Gate cache** (`probe_gatecache.py`): see N1. **A poisoned context passed the gate silently** when it reused the `id()` of a freed clean context.

## Status of earlier findings

| ID | Status | Notes |
|---|---|---|
| H1 ADWIN can't fire in-year | **Fixed as documentation and tests; underlying limitation remains** | The README and `replay.py` `adwin_note` state it plainly, and the month-8 wording is corrected. An honest in-year test now exists. See the judgement below. |
| H2 DQ 0.893 < 0.93, floor 0.90, hard-fail semantics | **Waived-pending (user decision)** | No change. `dq_config.json:16` is still 0.90, and the change to `flagged_row_frac` at `dq.py:94` still needs explicit sign-off as part of that decision. |
| M1 Gate after loop | **Fixed** | `policy.replay(before_month=…)` is used at `replay.py:113-116`, with a unit test and my probe confirming it. |
| M2 Stale cache | **Fixed for `--stage all`** | `run.py:233-235` unlinks the cache, and my corrupted-cache test proves it. Single stages still reuse an unfingerprinted cache; the README says to delete it by hand. That is acceptable. |
| M3 Missing tests | **Fixed** | Poison tests for PSI, seed, full loop and selection e2e, plus the old-2025 reproduction, are all in `tests/test_online_replay_e2e.py`. Some test-strength caveats are in N2. |
| L1 config.json not byte-stable | **Fixed** | `selection._rounded` to 10 dp; sha256 identical over 3 versions. |
| L2 hindsight `events.json` | Not fixed (skipped) | Affects only C3, which was not selected. |
| L3 NaN-skipping national sums | Not fixed (skipped) | |
| L4 hard-coded `== 114` | Not fixed (skipped) | |
| L5 `province_abs` cut direction | Not fixed (skipped) | Not used by the config. |
| L6 rollbacks not asserted | **Fixed** | `replay.py:225-229` checks it, and so does the e2e test. |

## New findings
**Medium**
- **N1 `replay.py:154-161` `_GATE_CACHE` keyed by `id(ctx)`.** The cache holds no reference to the context, so after a context is freed CPython can hand its `id` to a new one. The new context then gets the old context's gate result. In my probe, a poisoned context reused a freed clean context's id, and `_gate(..., "2025-08")` **returned the clean result instead of raising GateError**. On the production path (one `Context` per `run.py` process) this does not trigger. It can silently bypass the gate in tests, notebooks or any multi-context use. Fix: key by `(ctx.panel` identity held alive) or use a `weakref.WeakKeyDictionary`, store the result on `ctx` itself (e.g. `ctx.cache["gate", month, strict]`), or drop the cache. The cost of dropping it is a few seconds.

**Low**
- **N2 `tests/test_online_replay_e2e.py:97-98`** uses `pytest.raises(GateError)` without `match="2025-08"` and without asserting which months were served. It would also pass if the gate failed in 2025-01 for an unrelated reason. My probe confirms the real behaviour, but the test should pin it: `match="^2025-08"`, plus a spy on served months as in `probe_gate.py`. Also, `test_adwin_seed_ignores_labels_from_t_on` covers only 2024-01..06, not the full 24-point seed. That is acceptable, because the full-loop test asserts the whole seed.
- **N3 `selection.py:76-84`**: rounding to 10 dp makes the config byte-stable only while the ~1e-15 noise never straddles a rounding boundary. The chance is tiny, but nonzero. Acceptable, and a one-line comment would cover it.
- **N4 `drift_demos.py:128-130`**: the ILLUSTRATIVE detector runs use `gate=False`, so they have no gate at all, not just a non-strict one, and only `approved_adwin` runs are gated. That is fine for illustrative runs, but the README says "the demo tracks use a non-strict gate", which slightly overstates it.

## Non-strict gate containment (drift demos)
- `strict` defaults to True in `gate.check_inputs` (`gate.py:41`) and in `run_track` (`replay.py:95`).
- The only `strict_gate=False` in the repo is `drift_demos.py:130`. `stage_replay2025` and `stage_demo2023` call `run_track` without it, so they are strict.
- `_GATE_CACHE` includes `strict` in its key, so a non-strict result can't be reused for a strict call on the same context.
- Non-strict only suppresses feature-range problems. DQ hard failures still raise.
- `PRESET_OVERRIDE` builds a new dict instead of mutating `cfg`.
- After the drift demos ran in the same process, the 2025 outputs are unchanged.

**The non-strict mode does not leak into the main path or the defaults.**

## Judgement: "ADWIN can't fire in-year"
As a documented limitation of this experiment branch it is **acceptable**. It is stated plainly in the README and the summary, tested honestly, backed by demos, and comes with a concrete recommendation: a 16/8 window on relative WAPE, or province-level errors. It should, however, be **flagged to Albert as an open DS/ML decision before anyone presents the system as "detects drift and retrains"**. With the approved settings, the `psi_and_adwin → prepare_retrain` path can't fire on real monthly data within a year, so in practice only PSI watches happen. In production terms the retrain path is dead code until DS/ML change the detector settings. That makes it a go-live blocker, not a blocker for this review.

## Verdict
**Ready for Albert's review, with conditions.** All of Webber's claimed fixes verify, except that H2 is waived and pending. There is no leakage in any probe, the outputs regenerate cold and are byte-stable where claimed, and `main` is untouched.

To go with the review:
1. Check 3 / H2 (DQ 0.893 against 0.93, the 0.90 floor, and the change to hard-fail semantics) needs the user's explicit decision.
2. The ADWIN detector settings are an open DS/ML decision, as described above.
3. N1 (gate cache keyed on `id()`) is a small fix Webber should make soon. It does not affect the current production replay path.

Optional for Webber: N2 (pin the GateError month and the served months in the e2e test) and N4 (README wording).
