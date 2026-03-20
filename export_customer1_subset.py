#!/usr/bin/env python3

import ast
import csv
import json
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TURNS_PATH = BASE_DIR / "turns_flat.csv"
CONVERSATIONS_PATH = BASE_DIR / "conversations.jsonl"
OUTPUT_TURNS_PATH = BASE_DIR / "customer_1_turns.csv"
OUTPUT_CONVERSATIONS_PATH = BASE_DIR / "customer_1_conversations.jsonl"

csv.field_size_limit(sys.maxsize)


def parse_structured(value):
    if isinstance(value, (dict, list)):
        return value
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except Exception:
            continue
    return {}


def extract_question(input_value):
    if not isinstance(input_value, dict):
        return ""
    data = input_value.get("data", {})
    for key in ("user_question", "question", "input", "query", "message", "prompt"):
        if isinstance(data, dict) and data.get(key):
            return str(data.get(key))
        if input_value.get(key):
            return str(input_value.get(key))
    return ""


def main():
    customer_threads = set()

    with open(OUTPUT_TURNS_PATH, "w", encoding="utf-8", newline="") as turns_file:
        writer = csv.DictWriter(
            turns_file,
            fieldnames=["thread_id", "trace_id", "user_id", "customer_id", "start_time", "question"],
        )
        writer.writeheader()

        with open(TURNS_PATH, "r", encoding="utf-8") as source_file:
            for row in csv.DictReader(source_file):
                metadata = parse_structured(row["metadata"])
                input_value = parse_structured(row["input"])
                customer_id = str(metadata.get("customer_id", "")) if isinstance(metadata, dict) else ""
                if customer_id != "1":
                    continue
                customer_threads.add(row["thread_id"])
                writer.writerow(
                    {
                        "thread_id": row["thread_id"],
                        "trace_id": row["trace_id"],
                        "user_id": str(metadata.get("user_id", "")),
                        "customer_id": customer_id,
                        "start_time": row["start_time"],
                        "question": extract_question(input_value),
                    }
                )

    with open(OUTPUT_CONVERSATIONS_PATH, "w", encoding="utf-8") as output_file:
        with open(CONVERSATIONS_PATH, "r", encoding="utf-8") as source_file:
            for line in source_file:
                conversation = json.loads(line)
                if conversation.get("thread_id") in customer_threads:
                    output_file.write(json.dumps(conversation, ensure_ascii=False) + "\n")

    print(f"Saved turns to {OUTPUT_TURNS_PATH}")
    print(f"Saved conversations to {OUTPUT_CONVERSATIONS_PATH}")
    print(f"threads: {len(customer_threads)}")


if __name__ == "__main__":
    main()
