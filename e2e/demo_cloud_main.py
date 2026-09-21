"""Start the real cloud for the demo with the time-lapse tariff applied in this process only.  python demo_cloud_main.py <port>"""
import sys

import uvicorn

import timelapse

timelapse.apply()

if __name__ == "__main__":
    uvicorn.run("app.main:app", port=int(sys.argv[1]))
