import argparse
import time

from loaders.load_businesses import load_businesses
from loaders.load_reviews import load_reviews
from loaders.load_checkins import load_checkins


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--business", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--checkin", action="store_true")

    parser.add_argument("--all", action="store_true")

    parser.add_argument("--path", default="/data")

    args = parser.parse_args()

    start = time.time()

    if args.all or args.business:
        load_businesses(f"{args.path}/yelp_academic_dataset_business.json")

    if args.all or args.review:
        load_reviews(f"{args.path}/yelp_academic_dataset_review.json")

    if args.all or args.checkin:
        load_checkins(f"{args.path}/yelp_academic_dataset_checkin.json")

    print(f"Done in {time.time() - start:.2f}s")


if __name__ == "__main__":
    main()
