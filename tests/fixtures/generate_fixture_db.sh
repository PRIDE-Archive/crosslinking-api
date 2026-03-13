#!/usr/bin/env bash
# One-time script to regenerate tests/fixtures/test_db.sql.
# Run from the repo root when the mzidentml-reader schema or test fixtures change.
#
# Usage:
#   cd /home/cc/work/crosslinking-api
#   pipenv run python tests/fixtures/generate_fixture_db.py
set -e
pipenv run python tests/fixtures/generate_fixture_db.py
echo "Done. Commit tests/fixtures/test_db.sql."
