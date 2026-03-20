#!/usr/bin/env python3

import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "conversation_experience.csv"
OUTPUT_PATH = BASE_DIR / "top20_bad_cases.csv"


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    def sort_key(row):
        return (
            float(row["overall_experience_score"]),
            -int(row["turn_count"]),
            -int(row["evidence_request_turns"]),
            -int(row["query_payload_request_turns"]),
        )

    filtered = [row for row in rows if row["experience_label"] in {"poor", "mixed"}]
    top20 = sorted(filtered, key=sort_key)[:20]

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(top20)

    print(f"Saved Top 20 cases to {OUTPUT_PATH}")
    print(f"rows: {len(top20)}")


if __name__ == "__main__":
    main()
