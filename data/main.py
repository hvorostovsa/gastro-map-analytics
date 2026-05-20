import argparse
import time

from loaders.load_businesses import load_businesses
from loaders.load_reviews import load_reviews
from loaders.load_checkins import load_checkins
from loaders.load_districts import load_districts

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--business", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--checkin", action="store_true")
    parser.add_argument("--districts", action="store_true")

    parser.add_argument("--all", action="store_true")

    parser.add_argument("--path", default="/data")

    # districts options (keep minimal)
    parser.add_argument("--district-column", default="district")
    parser.add_argument(
        "--districts-overwrite",
        action="store_true",
        help="Overwrite existing district values (default: only fill NULL)",
    )

    args = parser.parse_args()

    start = time.time()

    if args.all or args.business:
        load_businesses(f"{args.path}/yelp_academic_dataset_business.json")

    if args.all or args.review:
        load_reviews(f"{args.path}/yelp_academic_dataset_review.json")

    if args.all or args.checkin:
        load_checkins(f"{args.path}/yelp_academic_dataset_checkin.json")

    if args.all or args.districts:
        load_districts(
            geo_dir=f"{args.path}/geo",
            district_column=args.district_column,
            overwrite=bool(args.districts_overwrite),
        )

if __name__ == "__main__":
    main()
