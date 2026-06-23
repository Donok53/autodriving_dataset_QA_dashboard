from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.model_registry import ModelRegistryError, latest_model_version, list_model_versions, rollback_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Rollback models/current to a previously archived model.")
    parser.add_argument("--list", action="store_true", help="List rollback candidates.")
    parser.add_argument("--version-dir", default=None, help="Archived model directory. Defaults to the latest archive.")
    parser.add_argument("--no-archive-current", action="store_true", help="Do not archive the replaced current model.")
    args = parser.parse_args()

    if args.list:
        versions = list_model_versions()
        print(json.dumps([str(path.relative_to(PROJECT_ROOT)) for path in versions], ensure_ascii=False, indent=2))
        return

    try:
        version_dir = Path(args.version_dir).expanduser().resolve() if args.version_dir else latest_model_version()
        result = rollback_model(version_dir, archive_current=not args.no_archive_current)
    except ModelRegistryError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
