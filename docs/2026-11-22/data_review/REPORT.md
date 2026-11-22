# Data review: Tourist-Prediction-from-Raining (exp/online-model-trials @ 6cc2a63, read-only)

Scripts: eda1.py, eda2.py (DQ, recovery, outliers, seasonality), eda3.py (error decomposition), rain.py (rain QA and signal), ab.py (transform A/B harness), ewa_and_seeds.py.
Results: transform_results.csv / .png. Charts: recovery_index.png, error_share_2023.png, error_share_2025.png, rain_signal.png.

Key finding: the raw data is clean. The problem is the **lag-12 base plus a binary COVID flag**. Adding one leak-free feature, exp_lr_recent = log(lag1 * S[m]/S[m-1]) - log(lag12), cuts 2023 from 25.8% to 9.6-10.2% (frozen) and from 20.0% to 8.6% (monthly). S is each province's monthly share in its last two complete normal years before the target year. 2025 stays flat or improves a little.
