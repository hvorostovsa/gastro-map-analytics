import json
from db import execute_query

def load_initial_data():
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        execute_query(
            "INSERT INTO restaurants (name, cuisine, rating) VALUES (%s, %s, %s);",
            (item["name"], item["cuisine"], item["rating"])
        )

    print("JSON data imported successfully.")
