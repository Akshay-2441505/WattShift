"""Stand-in GPU workload fired by the Wattshift scheduler. Placeholders are filled in by KaggleProvider.

This is a short, real GPU burst (matmuls), not the modeled fleet job. While it runs, the kernel samples the GPU's real
power draw from nvidia-smi once a second and prints one summary line (WATTSHIFT_POWER {...}) that the provider reads
back after the run. The job's modeled duration is separate: the measured watts are what get priced.
"""
import json
import subprocess
import threading
import time
from datetime import datetime, timezone

JOB_ID = "__JOB_ID__"
RUN_SECONDS = __RUN_SECONDS__

print("wattshift job", JOB_ID, "started_utc", datetime.now(timezone.utc).isoformat(), flush=True)
print(subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout, flush=True)

QUERY = ["nvidia-smi", "-i", "0", "--query-gpu=power.draw,power.limit,name", "--format=csv,noheader,nounits"]
samples = []  # (epoch seconds, watts)
info = {"gpu": None, "limit": None}
stop = threading.Event()


def read_power():
    try:
        w, limit, name = [x.strip() for x in subprocess.run(QUERY, capture_output=True, text=True).stdout.strip().split(",", 2)]
        info["gpu"], info["limit"] = name, float(limit)
        samples.append((time.time(), float(w)))  # "[N/A]" (no power sensor) raises here and the sample is skipped
    except (ValueError, IndexError):
        pass


def sampler():
    while not stop.is_set():
        t = time.time()
        read_power()
        time.sleep(max(0.0, 1.0 - (time.time() - t)))


import torch

dev = "cuda" if torch.cuda.is_available() else "cpu"
print("device", dev, flush=True)
a = torch.randn(4096, 4096, device=dev)
thread = threading.Thread(target=sampler, daemon=True)
if dev == "cuda":
    thread.start()
t0, n = time.time(), 0
while time.time() - t0 < RUN_SECONDS:
    a = (a @ a).tanh()
    if dev == "cuda":
        torch.cuda.synchronize()  # ops are async; sync so the loop really stops at RUN_SECONDS
    n += 1
stop.set()
print("matmuls", n, "seconds", round(time.time() - t0, 1), flush=True)

if len(samples) >= 2:
    watts = [w for _, w in samples]
    joules = sum((t2 - t1) * (w1 + w2) / 2 for (t1, w1), (t2, w2) in zip(samples, samples[1:]))  # trapezoid rule
    print("WATTSHIFT_POWER " + json.dumps({
        "avg_watts": round(joules / (samples[-1][0] - samples[0][0]), 2), "peak_watts": max(watts), "energy_wh": round(joules / 3600, 4),
        "samples": len(samples), "gpu": info["gpu"], "power_limit_w": info["limit"],
    }), flush=True)
else:
    print("no power reading (samples: %d)" % len(samples), flush=True)
print("wattshift job", JOB_ID, "finished_utc", datetime.now(timezone.utc).isoformat(), flush=True)
