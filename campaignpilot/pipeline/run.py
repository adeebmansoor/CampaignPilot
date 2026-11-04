"""Pipeline entry point.

    python -m campaignpilot.pipeline.run            # generate data + ETL + index knowledge base
    python -m campaignpilot.pipeline.run --every 3600   # same, on a schedule (seconds)
    python -m campaignpilot.pipeline.run --skip-generate  # ETL from existing CSVs only
"""
import argparse
import json
import logging
import time

from campaignpilot.config import get_settings
from campaignpilot.pipeline.etl import run_etl
from campaignpilot.pipeline.generate_data import generate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pipeline")


def run_once(skip_generate: bool = False, skip_index: bool = False, seed: int = 42) -> dict:
    s = get_settings()
    result = {}
    if not skip_generate:
        result["generated"] = generate(s.raw_data_dir, seed=seed)
    result["etl"] = run_etl(s.raw_data_dir)
    if not skip_index:
        from campaignpilot.rag.store import index_knowledge_base
        result["indexed_chunks"] = index_knowledge_base(s.knowledge_dir)
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="CampaignPilot data pipeline")
    p.add_argument("--every", type=int, default=0, help="re-run every N seconds (0 = run once)")
    p.add_argument("--skip-generate", action="store_true")
    p.add_argument("--skip-index", action="store_true")
    args = p.parse_args()

    run = 0
    while True:
        run += 1
        started = time.time()
        try:
            # new seed per scheduled run simulates fresh incoming data
            result = run_once(args.skip_generate, args.skip_index, seed=42 + run - 1)
            log.info("run %d finished in %.1fs: %s", run, time.time() - started, json.dumps(result))
        except Exception:
            log.exception("run %d failed", run)  # keep the scheduler alive; next run retries
            if not args.every:
                raise
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
