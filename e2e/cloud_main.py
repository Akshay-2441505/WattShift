"""Start the real cloud for the end-to-end proof, with the per-job start-time spread (0-14 minutes, an anti-herd
feature that is unit-tested elsewhere) switched off IN THIS PROCESS ONLY, so a deferred job's planned start is exactly the
top of the hour and the proof does not wait up to 15 extra minutes. Production code is not changed."""
import sys

import uvicorn

from app import allocator

allocator.jitter_minutes = lambda job_id: 0  # allocate() looks this up at call time, so the patch takes effect

if __name__ == "__main__":
    uvicorn.run("app.main:app", port=int(sys.argv[1]))
