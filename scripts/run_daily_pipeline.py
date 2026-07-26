"""
run_daily_pipeline.py

Entrypoint for Helio's daily automation run.
Wires together config loading, DB setup, LLM client, and the Orchestrator.

Usage:
    python scripts/run_daily_pipeline.py [--dry-run] [--channel CHANNEL_NAME]
"""

import sys
import os
import argparse
import logging

# Allow imports from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from src.config_loader import load_config
from src.db.db import SessionLocal
from src.db.init_db import init_db
from src.llm_client import LLMClient
from src.agents.orchestrator import OrchestratorAgent


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join("logs", "helio.log"),
                encoding="utf-8",
            ),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Helio daily pipeline.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all stages except the final YouTube upload.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force the run, ignoring upload interval checks.",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Restrict run to a specific channel name (default: all).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    args = parser.parse_args()

    os.makedirs("logs", exist_ok=True)
    setup_logging(args.log_level)
    logger = logging.getLogger("helio.runner")

    logger.info("=" * 60)
    logger.info("Helio — Daily Pipeline Starting (dry_run=%s, force=%s)", args.dry_run, args.force)
    logger.info("=" * 60)

    # ── Initialise DB (idempotent) ────────────────────────────────────
    init_db()

    # ── Load config ───────────────────────────────────────────────────
    config = load_config()

    # ── Filter channels if --channel flag passed ──────────────────────
    if args.channel:
        config["channels"] = [
            ch for ch in config.get("channels", [])
            if ch["name"] == args.channel
        ]
        if not config["channels"]:
            logger.error("Channel '%s' not found in config.yaml.", args.channel)
            sys.exit(1)

    # ── Build shared LLM client ───────────────────────────────────────
    llm_cfg = config.get("llm", {})
    llm = LLMClient(
        model=llm_cfg.get("model", "meta/llama-3.1-70b-instruct"),
        vision_model=llm_cfg.get("vision_model", "meta/llama-3.2-11b-vision-instruct"),
        temperature=llm_cfg.get("temperature", 0.8),
    )

    # ── Run Orchestrator ──────────────────────────────────────────────
    db = SessionLocal()
    try:
        orchestrator = OrchestratorAgent(config, db, llm_client=llm)
        summary = orchestrator.run_daily_plan(dry_run=args.dry_run, force=args.force)
        logger.info("Daily plan summary: %s", summary)
    finally:
        db.close()

    logger.info("=" * 60)
    logger.info("Helio — Daily Pipeline Complete.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
