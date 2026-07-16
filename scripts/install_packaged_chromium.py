#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


LOCATION_LABEL = "Install location:"


def parse_install_locations(output):
    locations = []
    for line in output.splitlines():
        if LOCATION_LABEL not in line:
            continue
        path = Path(line.split(LOCATION_LABEL, 1)[1].strip()).expanduser()
        if path not in locations:
            locations.append(path)
    return locations


def copy_installations(locations, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in locations:
        source = Path(source)
        if not source.is_dir() or not (source / "INSTALLATION_COMPLETE").is_file():
            raise RuntimeError(f"Playwright browser cache is incomplete: {source}")
        destination = target / source.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=os.name != "nt")
        copied.append(destination)
    return copied


def install_packaged_chromium(target, attempts=5):
    env = os.environ.copy()
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    env.setdefault("PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT", "120000")

    install_command = [sys.executable, "-m", "playwright", "install", "chromium"]
    for attempt in range(1, attempts + 1):
        result = subprocess.run(install_command, env=env, check=False)
        if result.returncode == 0:
            break
        if attempt == attempts:
            raise RuntimeError(
                f"Playwright browser download failed after {attempts} attempts"
            )
        delay = attempt * 10
        print(f"Playwright browser download failed, retrying in {delay} seconds...")
        time.sleep(delay)

    dry_run = subprocess.run(
        install_command + ["--dry-run"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = dry_run.stdout.decode("utf-8", errors="replace")
    if dry_run.returncode != 0:
        raise RuntimeError(f"Unable to inspect Playwright browser cache:\n{output}")
    locations = parse_install_locations(output)
    if not locations:
        raise RuntimeError("Playwright did not report any browser install locations")
    copied = copy_installations(locations, target)
    for path in copied:
        print(f"Packaged Playwright browser: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Install Chromium in the shared cache and copy current revisions into a desktop package."
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--attempts", type=int, default=5)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    install_packaged_chromium(args.target, attempts=args.attempts)


if __name__ == "__main__":
    main()
