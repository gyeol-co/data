"""Publish CLI — staging → public.

수동:
    uv run python -m etl.publish              # 한 번 돌고 종료
    uv run python -m etl.publish --watch      # 계속 polling, Ctrl+C 로 종료
"""

from __future__ import annotations

import argparse
import logging
import signal
import time

from etl.publish.pipeline import publish_all, publish_batch


def main():
    parser = argparse.ArgumentParser(description="Publish staging → public")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--watch", action="store_true", help="polling 모드 (cron 대체)")
    parser.add_argument("--interval", type=int, default=300, help="watch 모드 폴링 간격 (초)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.watch:
        print(publish_all(batch_size=args.batch_size))
        return

    # watch mode
    stop = False

    def _on_signal(signum, _frame):
        nonlocal stop
        logging.info("Signal %s — stopping after current batch", signum)
        stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while not stop:
        try:
            publish_all(batch_size=args.batch_size)
        except Exception:
            logging.exception("publish pass failed; continuing")
        if stop:
            break
        for _ in range(args.interval):
            if stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
