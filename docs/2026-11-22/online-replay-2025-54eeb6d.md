# QA report: 2025 online replay, `exp/online-replay-2025` @ 54eeb6d

Tester (QA), 2026-11-22 (Asia/Bangkok, UTC+7). This was a read-only review. Nothing in `/workspace/tpr-test` was modified, committed or pushed. All runs used scratch copies under `/workspace/qa-scratch/`. Probe scripts and their outputs are in `/workspace/qa-scratch/probes/`.

## Build identity
- `/workspace/tpr-test`: branch `exp/online-replay-2025`, HEAD `54eeb6df179526cbc4f77254e475ff585e7864aa`. 6cc2a63 is an ancestor. There is no upstream (`fatal: no upstream configured`). The working tree was clean at 19:38 when I copied it.
- **Note:** by 19:58 Webber's working tree had **uncommitted WIP**: 7 modified files (`replay.py`, `gate.py`, `run.py`, `charts.py`, `psi_adwin/adwin.py`, `psi_adwin/policy.py`, `tests/test_online_replay_drift.py`), a new `drift_demos.py`, and new `replay_2020_*` / `replay_2023_normalseed_*` outputs. **This report covers the committed 54eeb6d only.** My scratch code is byte-identical to 54eeb6d (`git diff HEAD -- '*.py' tests` is empty). The WIP was not reviewed.

## Results by check

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 1 | Suite | **PASS** | HEAD: `170 passed, 3 skipped`. Base 6cc2a63 (worktree, same venv): `145 passed, 3 skipped`. The +25 tests are exactly the 25 collected in the 4 new test files. The same 3 MOTS/raw-data skips appear on both. |
| 2 | Reproduction | **PASS** | Ran the `reproduce` stage inside `--stage all` from a deleted cache. `all_match: true`. Online_trials references match at 4 dp of the fraction, and also at 4 dp of the percent, e.g. rf_logdiff 19.9670/5.9361/4.8290, EWA 7.1312/5.6371/4.5550, naive 32.7155/11.1521/5.4804. Champion 6.5021/5.5909/4.5366 vs 6.50/5.59/4.54 (within 0.01). The level port equals `run_year` to ≤ 9.3e-10. The references come from `online_trials/results.csv`, not from literals. |
| 3 | DQ | **FAIL** (as disclosed) | v1 = `0.5392316017316017` ✓. **v2 normal bucket = 0.8927 < 0.93** ✗. Total arrivals + rain only = 0.9517. COVID 0.0592 and COVID-base 0.3084 are reported ✓. My probe on the real panel (`probe_dq.py`): doubling the rain in a dry month (3 provinces × 3 months) was flagged 0/9 times ✓, and a 10× guest spike was flagged 9/9 times ✓. A duplicate key raises `DataQualityError: uniqueness` ✓. Separately, `dq_config.json:16` sets `accuracy_min_normal: 0.90`, which is below the plan's 0.93, and `below_normal_floor` is only a warning. |
| 4 | Leakage | **PARTIAL**: no leak found, but tests are missing | The existing tests pass: features, both experts, event_lag1, EWA. **My probes on the parts Webber's tests don't cover found no leakage.** Details below. Pytest coverage of the replay loop, the PSI reference and the ADWIN seed is still missing. |
| 5 | Selection hygiene | **PASS (substance)**: the e2e test is missing | A spy on `selection._only_2024` during a real `stage_select` saw only `{'2024'}` (6 calls). `frozen_choice` refuses without the stamp (test). E2e probe: 2025 guest labels ×3, province 10 set to 1e9 and 2025 rain ×5, then `stage_select` into a temp config. The resulting selection equals the committed one (max numeric diff 3.5e-17, no non-numeric diffs): C0 / E2 / eta 5. Caveat: a clean rerun also rewrites `config.json` with ~1e-15 float noise, so the file is not byte-stable. |
| 6 | Drift | **PARTIAL** | The old-2025 reproduction is asserted in the stage: served WAPE `0.04788460348726727`, match `<1e-12`, 0 promotions, 0 rollbacks ✓ (not in pytest). PSI-only → `watch` ✓ (test, plus my probe with 3 PSI months → 3 `watch`, no trigger). `adwin_direction: up` ignores a downward cut ✓. The step-change test passes **only because the step is planted in the seed** (`tests/test_online_replay_drift.py:36`, `seed=[0]*16+[1]*8`). My probe: with a flat seed of 0.05 and WAPE jumping to 0.2, 0.5, 0.9 or **1.0 from 2025-01**, with PSI on every month, ADWIN **never cuts and nothing triggers**. The configured trigger cannot detect drift that starts inside the replay year. Also, the test checks `trigger`/`challengers`, not the `prepare_retrain` action string written by `replay.run_track`. |
| 7 | Artifacts | **PASS** | All 31 tracked CSV/JSON/PNG files were deleted in scratch (cache included) and recreated by `--stage all`. The run took 12m55s under contention with a parallel pytest; Webber's figure is ~10 min. Every file has a fresh mtime and is non-empty. All 10 PNGs are byte-identical to the committed ones. CSV/JSON match the committed versions to ≤ 9.3e-10 (RF float noise; timings excluded). The README headline test passes against the regenerated `results.csv`. A spot-check of the other README numbers also matched: C3/C4/E3 tables, gap vs naive −26.4/−5.6/−0.96, 75/62/53 provinces, served 4.5226 %. |
| 8 | Git hygiene | **PARTIAL** | The branch is correct and has no upstream. Local `main` = `2e45adf` before and after, and its reflog has only the clone entry. GitHub `origin/main` = `5191c99`, and local main is an ancestor of it (stale, not modified). Nothing was pushed. The commits are **not one per step**: 6263b4d = steps 0+2, 6808008 = steps 3+4, rain (step 7) was committed before the drift log/demo (steps 5/6), and 182418e rewrites earlier outputs (105 deletions). |

## My leakage probes (gaps Webber disclosed under check 4 / 5)
Labels poisoned = `guest_arrivals_total/thai/foreign` set to 1e12 (or NaN) and `rain_avg_mm` set to 1e6 (or NaN) for every month ≥ T. Each poisoned context was rebuilt from scratch with no prediction cache.

1. **PSI (current month and Seasonal-2Y reference)**, `replay.psi_values`, 8 cases: {1e12, NaN} × {T=2024-06/ref 2024-05, 2025-03/ref 2024-12, 2025-07/ref 2024-12, 2023-04/ref 2022-12}. All **identical** to clean.
2. **ADWIN seed** (Champion one-step OOS monthly WAPE, 2023-01..2024-12), T=2024-07: the first 18 values (2023-01..2024-06) match clean to ≤ 1.9e-16. Values from 2024-07 on change, which is expected because each one is that month's observed error.
3. **Full 2025 replay loop** (built exactly like `stage_replay2025`: champion EWA + `run_track` + `policy.replay`), T=2025-07:
   - Seed: max diff 1.2e-16.
   - Served forecasts for 2025-01..07: max relative diff ≤ 5.1e-16.
   - Every replay-log column for months < T matches within 1e-9: served/naive/expert WAPE, ADWIN window and mean, PSI, trigger, action, decision, EWA weights.
   - The decision-time columns at T match: PSI, EWA weights, served model.
   - The events before T are identical.
   - **No leakage in the loop.**
   - Side finding: with the gate on, the poisoned run raised `GateError: 2025-08: consistency_thai_foreign`, but only **after** the policy loop had already served and decided all 12 months. See finding M1.
4. **Check 5 e2e**: see the table above. No change to the selection.

## Code review (diff 6cc2a63..54eeb6d), ranked
**High**
- H1 `tests/test_online_replay_drift.py:36` together with `experiments/psi_adwin/policy.py` (ADWIN on monthly WAPE, delta 0.002, 24-pt seed). The drift trigger cannot fire for a shift that starts in the replay year: a 5% → 100% WAPE jump from January never cuts (probe). The only "fires" test plants the step inside the seed. `replay.py:255` says "the first month that can fire is month 8", which reads as a capability, but it only holds if the shift began 8+ months before the year. The "PSI and ADWIN → prepare_retrain" story is therefore untestable on real data. This needs a DS/ML decision (input = province errors, a smaller min window, or river ADWIN) and an honest test of a step that starts in the replay year.
- H2 DQ v2 normal-bucket 0.893 fails the 0.93 criterion. `dq_config.json:16` sets the floor at 0.90, below the plan target, and it is only a warning. `dq.py:94` also changes what feeds the hard-fail threshold for v2: `flagged_row_frac` becomes 1 − normal-bucket accuracy, so COVID-era flags no longer count. This needs an explicit sign-off rather than a quiet change.

**Medium**
- M1 `replay.py:102` (`run_track`): the "input gate" (`check_inputs`) runs **after** `policy_replay` has finished, while the log rows are built. The module docstring (`replay.py:3`) and the README say the gate comes before serving, but a bad month is still served and drives ADWIN/PSI decisions before the gate raises. My probe confirmed this: the GateError fired post hoc.
- M2 `experts.py:109,122-140`: the prediction cache `results/predictions.parquet` is keyed by (target, variant, drop, seed, month) only, with no data, code or events fingerprint. After any change to code, `events.json` or data, `--stage all` silently reuses stale predictions. "Regenerates everything" is only true from a cold cache. Add a hash to the key, or delete the cache at the start of `all`.
- M3 Missing tests that the plan requires: label-poison for the replay loop, the PSI reference and the ADWIN seed; a 2025-label → `config.json` e2e test; the old-2025 reproduction in pytest; and an assertion on the `prepare_retrain` action string. My probes show the behaviour is correct, so these are regression gaps, not bugs.

**Low**
- L1 `run.py:163` `S.freeze`: every `--stage all` rewrites the committed `config.json` with ~1e-15 float noise, leaving the tree dirty after a clean rerun. Round the scores before freezing, or skip the write when the decision fields are unchanged.
- L2 `events.json`: the event spans were hand-picked with hindsight and include 2025-11/12. They only affect C3, which was not selected, but they should be confirmed and dated as of when they would have been known.
- L3 `features.py:99-100`: `groupby(...).transform("sum")` silently skips NaN lags, so a missing province gives a partial national total with no warning.
- L4 `tests/test_dq_accuracy_v2.py:73` hard-codes `== 114` rain flags (a brittle magic number). The README test checks committed artifacts, so it only proves consistency with whatever was last committed.
- L5 `policy.py:77-89`: in `province_abs` mode, the cut direction is computed once per month even though ADWIN may cut several times within that month's updates, so the direction is approximate. It is not used by the config (`monthly_wape`).
- L6 `replay.py:212-215` asserts promotions == 0 but not rollbacks == 0. The rollback check is implied by zero promotions; make it explicit.

I found no hard-coded numbers that fake passing. The references come from files or the plan, and the asserts compare computed values.

## Verdict
**Needs fixes before Albert's review.** There is no data leakage in anything I could probe, and reproduction and artifacts are solid. Two plan criteria fail outright or are not meaningfully met: check 3 (DQ 0.893) and check 6 (the ADWIN trigger cannot fire in-year). The gate ordering contradicts the documentation, and the required leakage/e2e tests are missing.

## Required fixes for Webber
1. DQ: reach normal-bucket ≥ 0.93 (e.g. a recovery bucket for 2023-24 foreign arrivals), or get an explicit Desin/DS-ML waiver for scoring on total arrivals + rain (0.952). Align `accuracy_min_normal` with the plan and document the change to `flagged_row_frac`.
2. Drift: add a test where the step starts inside the replay year with a normal seed, and either make ADWIN able to fire (DS/ML choice of input/window) or state plainly that it cannot. Fix the "month 8" wording. Assert the `prepare_retrain` action string.
3. Move `check_inputs` before serving/deciding each month inside the loop, or reword the documentation.
4. Add pytest label-poison tests for the replay loop, PSI reference and ADWIN seed (my `probes/probe_loop.py` / `probe_leak.py` can be adapted), a 2025-label → selection e2e test (`probes/probe_select.py`), and the old-2025 reproduction as a test (it can be marked slow).
5. Invalidate the prediction cache on code/data/events changes, and keep `config.json` byte-stable on reruns.
6. Git: split, or note in the README, the combined steps (0+2, 3+4) per Desin's one-commit-per-step rule. Commit or stash the current uncommitted WIP before handing a new head to QA.
