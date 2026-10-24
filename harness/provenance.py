"""Stable source and expected-build provenance; never includes environment secrets.

The runtime contract is baked into the harness image. It records the build inputs
the image was made for, rather than claiming to measure a running Docker daemon.
"""
import hashlib
from pathlib import Path

CONTRACT_VERSION = 3
CONTRACT_FILES = (
    ".dockerignore",
    "Dockerfile.harness",
    "Dockerfile.sandbox",
    "docker-compose.yml",
    "requirements.lock",
    "requirements.txt",
)


def _normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def fingerprints(root=None):
    """Existing (harness, sandbox) pair, kept compatible for source consumers."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    harness = hashlib.sha256(b"".join(
        path.name.encode() + _normalized(path)
        for path in sorted((root / "harness").glob("*.py"))
    )).hexdigest()
    sandbox = hashlib.sha256(b"".join(
        _normalized(root / "sandbox" / name) for name in ("server.py", "runtime.py")
    )).hexdigest()
    return harness, sandbox


def harness_fingerprint(root=None):
    """Harness digest without requiring sandbox sources in the harness image."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return hashlib.sha256(b"".join(
        path.name.encode() + _normalized(path)
        for path in sorted((root / "harness").glob("*.py"))
    )).hexdigest()


def contract_fingerprint(root=None):
    """Hash only an explicit allowlist of runtime-critical, non-secret inputs.

    On the host, files are read from the repository. In the built image their
    exact build-context copies live in build-contract/. LF normalization makes
    both locations agree for Windows and Linux checkouts. Names and lengths are
    framed so adjacent files cannot create ambiguous concatenations.
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    contract = root / "build-contract" if (root / "build-contract").is_dir() else root
    digest = hashlib.sha256(b"aerosentinel-runtime-contract-v3\0")
    for name in CONTRACT_FILES:
        encoded = name.encode("utf-8")
        data = _normalized(contract / name)
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()
