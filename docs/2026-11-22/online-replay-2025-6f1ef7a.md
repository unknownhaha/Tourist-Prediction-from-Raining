# QA retest: `exp/online-replay-2025` @ 6f1ef7a

Tester (QA), 2026-11-22 (Asia/Bangkok, UTC+7). This was a read-only review. Nothing in `/workspace/tpr-test` was modified, committed or pushed. Scratch work is in `/workspace/qa-scratch3/` (`repo`, `old` with the fix hunk reverted, `regen`).

## Verdict
**PASS. The N1, N2 and N4 fixes verify, and 6f1ef7a is ready for Albert's review.** The conditions from the previous report still stand:
- DQ check 3 (0.893) is waiting on the user's decision.
- The ADWIN settings are an open DS/ML decision.

There are no new blocking issues.

## Evidence
- **Head and tree:** HEAD `6f1ef7ae4b99da06b637a98e7114f62ca07e1b01` on `exp/online-replay-2025`. The tree is clean before and after. There is no upstream. 51f48f2 is an ancestor, and the one new commit touches 5 files: README, `drift_demos.py`, `experts.py`, `replay.py` and the e2e test. DQ is untouched. `main` is `2e45adf` before and after, and its reflog shows only the clone.
- **Suite:** `187 passed, 3 skipped` (6m22s), the same 3 MOTS/raw-data skips. The suite left the tree clean.
- **(1) Gate cache on the context:**
  - `_GATE_CACHE` is gone (`hasattr(replay, "_GATE_CACHE")` is False). `_gate` now caches in `ctx.gate_cache[(month, strict)]` (`replay.py:154-160`, `experts.py:52-54`).
  - I reran my id-reuse probe on the new code. On iteration 0 the poisoned context **did reuse** a freed clean context's `id()`, and the gate **still raised** GateError. All 6 iterations raised. On 51f48f2 the same probe let iteration 0 through.
  - **The regression test catches the old bug.** I reverted only the `replay.py` hunk in a scratch copy:
    - As written, the test fails at its structural assert `("2025-08", True) in c.gate_cache`.
    - With the three structural asserts removed, it fails with **`DID NOT RAISE GateError`**, which is the real behavioural failure.
- **(2) e2e test pinned:** it now uses `pytest.raises(GateError, match=r"^2025-08:")`. A `Server.__call__` spy asserts that the served months are exactly 2025-01..07, and the spy is restored in a `finally`. This agrees with my earlier instrumented probe.
- **(3) Demo docs vs code:**
  - `drift_demos.py:132-133` passes `gate=(det_name == "approved_adwin")` and `strict_gate=False`. So the ILLUSTRATIVE runs are ungated, and the approved-ADWIN demo runs are gated non-strictly.
  - Non-strict mode only suppresses feature-range problems. `DataQualityError` still raises `GateError` (`gate.py:46-52`).
  - The README and the docstring now say exactly this. ✓
  - **Low nit:** both texts say "the 2023 demo [is] gated strictly". But the 2023 demo's `A_sensitivity` (ILLUSTRATIVE delta=0.05) track runs with `gate=False` (`replay.py:305`; this predates this commit). Suggested wording: "the 2023 demo's Track A/B are gated strictly; its sensitivity panel is ungated".
- **(4) 2025 outputs:**
  - Every committed result, chart and `config.json` is byte-identical between 51f48f2 and 6f1ef7a (`git diff --quiet` on those paths).
  - A cold-cache `--stage replay2025` rerun on 6f1ef7a gave a byte-identical `replay_2025_events.jsonl`. The log, served and summary files match numerically to ≤ 9.3e-10 (RF float noise) but **not byte-for-byte**.
  - Served WAPE was `0.045226326971522644` on my rerun vs `0.04522632697152263` committed. The figure is reproducible to about 1e-16, not as an exact 17-digit string.
  - No `prepare_retrain`, no promotions and no rollbacks. "Byte-identical" is true of the committed files; a fresh run is identical only within float noise.

## The two WAPE figures: different quantities, both correct
- **0.04522632697152263** is the **new Champion's** served 2025 WAPE in the new replay: an EWA of the C0 logdiff and level experts with eta 5, both refit monthly on data up to t-1. It equals the offline `final_champion_C0_E2_eta5` 2025 row in `results.csv`, and `loop_matches_offline` is true. Nothing is promoted, so the loop serves exactly the offline Champion.
- **0.04788460348726727** is the **old approved psi_adwin primary run**, reproduced as a regression guard on the refactored `policy.replay`. It uses that run's own defaults and `psi_adwin/config.json`: the model served is `logdiff_expanding@2024-12`, a single RF logdiff fit once on 2019-01..2024-12 and frozen for all of 2025, with 0 promotions and 0 rollbacks. It is checked to <1e-12 in the stage and in pytest.
- They differ because they serve different models. For reference, the old RF logdiff *refit monthly* is 0.04829. Both figures are still correct: one shows that the refactor did not change the approved policy's behaviour, and the other is the result of the new Champion.

## New issues
- Low: the 2023-demo gating wording described above.
- Info: "byte-identical outputs" holds for the committed files; cold reruns agree only to about 1e-9 (RF float noise; `config.json` stays byte-stable through rounding).
