import argparse
import time

from loaders.load_businesses import load_businesses
from loaders.load_reviews import load_reviews
from loaders.load_checkins import load_checkins
from loaders.load_counties import load_counties

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--business", action="store_true")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--checkin", action="store_true")
    
    parser.add_argument("--all", action="store_true")

    parser.add_argument("--path", default="/data")
    
    parser.add_argument("--counties", action="store_true")


    args = parser.parse_args()

    start = time.time()

    if args.all or args.business:
        load_businesses(f"{args.path}/yelp_academic_dataset_business.json")

    if args.all or args.review:
        load_reviews(f"{args.path}/yelp_academic_dataset_review.json")

    if args.all or args.checkin:
        load_checkins(f"{args.path}/yelp_academic_dataset_checkin.json")

    if args.all or args.counties:
        load_counties(f"{args.path}/tl_2025_us_county/tl_2025_us_county.shp")

if __name__ == "__main__":
    main()
