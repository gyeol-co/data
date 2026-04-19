"""Gyeol Data CLI."""

import argparse
import logging
import sys


def cmd_silver(args):
    from etl.silver.pipeline import process_all, process_brand, process_platform

    if args.platform and args.brand:
        count = process_brand(
            args.platform, args.brand,
            launch_brand_profile=not args.no_profile,
            launch_category_review=not args.no_review,
        )
        print(f"Silver: {args.platform}/{args.brand} — {count} products")
    elif args.platform:
        results = process_platform(
            args.platform,
            launch_brand_profile=not args.no_profile,
            launch_category_review=not args.no_review,
        )
        for bid, cnt in results.items():
            print(f"  {bid}: {cnt} products")
    else:
        results = process_all(
            launch_brand_profile=not args.no_profile,
            launch_category_review=not args.no_review,
        )
        for plat, brands in results.items():
            for bid, cnt in brands.items():
                print(f"  {plat}/{bid}: {cnt} products")


def cmd_gold(args):
    from etl.gold.pipeline import process_all, process_batch

    if args.batch:
        result = process_batch(
            batch_size=args.batch_size,
            workers=args.workers,
            include_images=not args.no_images,
        )
        print(
            f"Batch: {result['success']} success, "
            f"{result['failed']} failed, {result['remaining']} remaining"
        )
    else:
        result = process_all(
            batch_size=args.batch_size,
            workers=args.workers,
            include_images=not args.no_images,
        )
        print(
            f"Done: {result['total_success']} success, "
            f"{result['total_failed']} failed"
        )


def main():
    parser = argparse.ArgumentParser(description="Gyeol Data CLI")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # silver
    sp_silver = sub.add_parser("silver", help="Bronze → Silver ETL")
    sp_silver.add_argument("--platform", "-p")
    sp_silver.add_argument("--brand", "-b")
    sp_silver.add_argument("--no-profile", action="store_true")
    sp_silver.add_argument("--no-review", action="store_true")

    # gold
    sp_gold = sub.add_parser("gold", help="Gold enrichment (Gemini)")
    sp_gold.add_argument("--batch", action="store_true", help="Run single batch only")
    sp_gold.add_argument("--batch-size", type=int, default=50)
    sp_gold.add_argument("--workers", "-w", type=int, default=5, help="Parallel workers")
    sp_gold.add_argument("--no-images", action="store_true", help="Text-only mode")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "silver":
        cmd_silver(args)
    elif args.command == "gold":
        cmd_gold(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
