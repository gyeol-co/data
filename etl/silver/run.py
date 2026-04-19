"""Silver ETL CLI.

사용:
    uv run python -m etl.silver.run                          # 전체
    uv run python -m etl.silver.run --platform etcseoul      # 플랫폼 전체
    uv run python -m etl.silver.run --brand artifacts        # 브랜드 하나
"""

import argparse
import logging
import sys

from etl.silver.pipeline import process_all, process_brand, process_platform
from etl.silver.reader import list_brands


def main():
    parser = argparse.ArgumentParser(description="Gyeol Silver ETL")
    parser.add_argument("--platform", help="특정 플랫폼만")
    parser.add_argument("--brand", help="특정 브랜드만")
    parser.add_argument(
        "--no-brand-profile",
        action="store_true",
        help="브랜드 적재 후 background brand profile autostart 비활성화",
    )
    parser.add_argument(
        "--no-category-review",
        action="store_true",
        help="브랜드 적재 후 background category review autostart 비활성화",
    )
    parser.add_argument(
        "--no-training-data",
        action="store_true",
        help="브랜드 적재 후 background training data autostart 비활성화",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    launch_brand_profile = not args.no_brand_profile
    launch_training_data = not args.no_training_data
    launch_category_review = not args.no_category_review

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.brand:
        # --brand: Bronze 목록에서 platform 자동 탐색
        for plat, bid in list_brands():
            if bid == args.brand:
                count = process_brand(
                    plat,
                    bid,
                    launch_brand_profile=launch_brand_profile,
                    launch_base_name_review=launch_training_data,
                    launch_category_review=launch_category_review,
                )
                print(f"{plat}/{bid}: {count} products")
                return
        print(f"Brand '{args.brand}' not found in Bronze")
        sys.exit(1)

    elif args.platform:
        results = process_platform(
            args.platform,
            launch_brand_profile=launch_brand_profile,
            launch_base_name_review=launch_training_data,
            launch_category_review=launch_category_review,
        )
        total = sum(results.values())
        for bid, count in results.items():
            print(f"  {args.platform}/{bid}: {count}")
        print(f"Total: {total} products")

    else:
        results = process_all(
            launch_brand_profile=launch_brand_profile,
            launch_base_name_review=launch_training_data,
            launch_category_review=launch_category_review,
        )
        total = 0
        for plat, brands in results.items():
            for bid, count in brands.items():
                print(f"  {plat}/{bid}: {count}")
                total += count
        print(f"Total: {total} products")


if __name__ == "__main__":
    main()
