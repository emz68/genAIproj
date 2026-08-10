"""§6 platform entrypoint: python -m src.platform.run --pipeline full --config src/platform/config.yaml

Extra (platform-only) flags beyond the frozen contract, all optional:
  --use-stubs        run A6 stubs instead of the real §6 module paths
  --no-resume        ignore the resumability manifest and re-run every stage
  --raw-dir/--artifacts-dir   path overrides (used by tests and the harness)
"""

from __future__ import annotations

import argparse
import traceback

from src.platform import protocol
from src.platform.config import DEFAULT_CONFIG_PATH, load_config
from src.platform.orchestrator import RunOptions, run_pipeline

STAGE = "platform"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.platform.run",
                                     description="EV charger data pipeline orchestrator (P5/A1)")
    parser.add_argument("--pipeline", choices=["full"], default="full")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--use-stubs", action="store_true",
                        help="run src/platform/stubs/* instead of the real stage modules")
    parser.add_argument("--no-resume", action="store_true",
                        help="ignore the artifact manifest and re-run every stage")
    parser.add_argument("--raw-dir", default=None, help="override paths.raw_dir")
    parser.add_argument("--artifacts-dir", default=None, help="override paths.artifacts_dir")
    protocol.add_common_flags(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        opts = RunOptions(
            use_stubs=args.use_stubs,
            no_llm=args.no_llm,
            limit=args.limit,
            log_json=args.log_json,
            resume=not args.no_resume,
            raw_dir=args.raw_dir,
            artifacts_dir=args.artifacts_dir,
        )
        return run_pipeline(cfg, opts)
    except SystemExit:
        raise  # protocol.fail already emitted the §6 error line
    except Exception as e:  # noqa: BLE001 — orchestrator boundary: anything → §6 error object
        protocol.fail(STAGE, f"{type(e).__name__}: {e}",
                      detail={"traceback": traceback.format_exc().splitlines()[-6:]})
        return 1  # unreachable; fail() raises SystemExit


if __name__ == "__main__":
    raise SystemExit(main())
