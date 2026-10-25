#!/usr/bin/env bash
# Personal OS — build + validate the backend package locally (M1-D).
# Usage: ./deploy/scripts/build-backend.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "== ruff =="
uv run ruff check src/personal_os/api src/personal_os/db src/personal_os/services src/personal_os/voice src/personal_os/config.py src/personal_os/deploy_worker.py src/personal_os/deploy_voice_worker.py deploy/scripts/worker_heartbeat.py

echo "== mypy =="
uv run mypy src/personal_os/api src/personal_os/db src/personal_os/services src/personal_os/voice src/personal_os/config.py src/personal_os/deploy_worker.py src/personal_os/deploy_voice_worker.py

echo "== tests =="
uv run pytest tests/companion/ tests/protocol/ tests/test_smoke.py tests/test_extraction_resolver.py tests/test_extraction_validator.py tests/api/test_voice_token.py -q

echo "== wheel =="
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
uv build --wheel --out-dir "$BUILD_DIR"
WHEEL="$(find "$BUILD_DIR" -maxdepth 1 -type f -name 'personal_os-*-py3-none-any.whl' -print -quit)"
test -n "$WHEEL"
uv run python - "$WHEEL" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as wheel:
    names = set(wheel.namelist())
    assert "personal_os/deploy_worker.py" in names, "worker entry point missing from wheel"
    assert "personal_os/deploy_voice_worker.py" in names, "voice worker entry point missing from wheel"
PY

mkdir -p dist
BUNDLE_DIR="$BUILD_DIR/bundle"
mkdir -p "$BUNDLE_DIR"
cp "$WHEEL" "$BUNDLE_DIR/app.whl"
cp alembic.ini "$BUNDLE_DIR/alembic.ini"
cp -R alembic "$BUNDLE_DIR/alembic"
mkdir -p "$BUNDLE_DIR/.hermes/skills/personal-os"
cp .hermes/skills/personal-os/SKILL.md "$BUNDLE_DIR/.hermes/skills/personal-os/SKILL.md"
tar -czf dist/personal-os-backend-bundle.tar.gz -C "$BUNDLE_DIR" .
uv run python - dist/personal-os-backend-bundle.tar.gz <<'PY'
import sys
import tarfile
from pathlib import Path

required = {
    "./app.whl",
    "./alembic.ini",
    "./.hermes/skills/personal-os/SKILL.md",
} | {
    f"./{path.as_posix()}" for path in Path("alembic").rglob("*") if path.is_file()
}
with tarfile.open(sys.argv[1], "r:gz") as bundle:
    names = set(bundle.getnames())
    missing = required - names
    if missing:
        raise SystemExit(f"backend bundle missing required files: {sorted(missing)}")
    print("Backend bundle contents verified:", ", ".join(sorted(required)))
PY
ls -la "$WHEEL" dist/personal-os-backend-bundle.tar.gz
