"""Stands in for `squeue` when testing SubprocessRunner: echoes its arguments and the two environment variables."""
import json
import os
import sys

argv = sys.argv[1:]
if "boom" in argv:
    print("boom failed", file=sys.stderr)
    sys.exit(3)
print(json.dumps({"argv": argv, "TZ": os.environ.get("TZ"), "fmt": os.environ.get("SLURM_TIME_FORMAT")}))
