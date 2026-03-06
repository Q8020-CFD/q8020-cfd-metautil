"""Minimal solver stub for Slurm Docker smoke testing.

Accepts arbitrary CLI args (like the real solver would via argparse),
prints diagnostics, and exits 0.
"""
import argparse
import socket
import time
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description="Smoke test solver")
    parser.add_argument("-greeting", type=str, default="world")
    parser.add_argument("-outdir", type=str, default=".")
    args, _unknown = parser.parse_known_args()

    print(f"greeting:  {args.greeting}")
    print(f"hostname:  {socket.gethostname()}")
    print(f"timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"outdir:    {args.outdir}")

    # Simulate a short computation
    time.sleep(2)
    print("done")


if __name__ == "__main__":
    main()
