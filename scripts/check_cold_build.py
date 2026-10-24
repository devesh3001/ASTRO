"""Measure an empty-cache build using a disposable builder, preserving other caches."""
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    name = "aerosentinel-cold-" + uuid.uuid4().hex[:10]
    folder = ROOT / "logs" / name
    folder.mkdir(parents=True)
    images = [name + "-harness:check", name + "-sandbox:check"]
    result = {"budget_seconds": 180, "passed": False, "builder": name}
    started = time.monotonic()
    try:
        subprocess.run(["docker", "buildx", "create", "--name", name, "--driver", "docker-container"],
                       check=True, timeout=30, capture_output=True)
        with (folder / "build.txt").open("w", encoding="utf-8") as log:
            completed = subprocess.run(["docker", "buildx", "bake", "--builder", name,
                "--file", "docker-compose.yml", "--load", "--progress", "plain",
                "--set", "harness.tags=" + images[0], "--set", "sandbox.tags=" + images[1]],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                timeout=max(0.1, 180 - (time.monotonic() - started)))
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["passed"] = completed.returncode == 0 and result["elapsed_seconds"] <= 180
    except (subprocess.SubprocessError, OSError) as exc:
        result["error"] = type(exc).__name__
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    finally:
        subprocess.run(["docker", "buildx", "rm", "--force", name], capture_output=True, timeout=60)
        for image in images:
            subprocess.run(["docker", "image", "rm", image], capture_output=True, timeout=30)
        (folder / "build-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
