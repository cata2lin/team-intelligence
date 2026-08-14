#!/usr/bin/env bash
cd /root/Scripturi
exec /root/Scripturi/.venv/bin/python -m uvicorn parcel_density_app:app --host 127.0.0.1 --port 8091
