# Attribution: `helios_venus_sample.csv`

This file is derived from the **Helios traces** published by SenseTime and the S-Lab System Group, licensed
**CC-BY-4.0** (https://creativecommons.org/licenses/by/4.0/).

- Source: https://github.com/S-Lab-System-Group/HeliosData (cluster "Venus")
- Paper: Q. Hu, P. Sun, S. Yan, Y. Wen, T. Zhang. *Characterization and Prediction of Deep Learning Workloads in
  Large-Scale GPU Datacenters.* SC '21. https://doi.org/10.1145/3458817.3476223

## What was changed (this is a modified version)

- Kept only jobs that used at least one GPU and ran for more than zero seconds, from the 28 days starting 2020-06-01.
- **Re-timed by exactly 324 weeks** onto Mon 2026-08-17 .. Sun 2026-09-13, so weekdays and times of day are preserved
  but the dates are not the real ones. Timestamps are read as local (IST) clock time.
- Renamed columns to `job_id, submit_time, duration_minutes, gpus`; job ids are prefixed `venus-`; duration converted
  from seconds to minutes. Users and virtual-cluster names were dropped.
- 14,627 jobs. The Venus fleet was about 968 GPUs in that period (from the trace's own `cluster_gpu_number.csv`).

## What this data is not

It is a real job log from a large AI research cluster in 2020, **not** from an Indian GPU-cloud operator, and it does
not say which jobs could have waited. It is used to test the backtest and to show the shape of a real workload, never
as evidence of what any particular company would save.

`iex_2026-08-17_2026-09-13.csv` is real IEX day-ahead price data (public), fetched with `scripts/fetch_iex_history.py`.
