#!/usr/bin/env python3

import ast
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


BASE_DIR = Path(__file__).resolve().parent
TURNS_PATH = BASE_DIR / "turns_flat.csv"
MAPPING_PATH = BASE_DIR / "c3mapping.xlsx"
OUTPUT_PATH = BASE_DIR / "customer_personas.csv"

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


def load_customer_mapping():
    if not MAPPING_PATH.exists():
        return {}

    with ZipFile(MAPPING_PATH) as archive:
        namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared_strings = []
        for item in shared_root.findall("a:si", namespace):
            texts = [node.text or "" for node in item.findall(".//a:t", namespace)]
            shared_strings.append("".join(texts))

        sheet_root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for row in sheet_root.findall(".//a:sheetData/a:row", namespace):
            values = []
            for cell in row.findall("a:c", namespace):
                cell_type = cell.get("t")
                raw_value = cell.find("a:v", namespace)
                value = ""
                if raw_value is not None:
                    if cell_type == "s":
                        value = shared_strings[int(raw_value.text)]
                    else:
                        value = raw_value.text or ""
                values.append(value)
            rows.append(values)

    mapping = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0] and row[1]:
            mapping[str(row[0]).strip()] = str(row[1]).strip()
    return mapping


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


def categorize_question(question):
    question_lower = question.lower()
    rules = [
        ("journey_analysis", ["journey", "journeys", "path", "paths", "timeline", "pattern", "patterns", "funnel", "flow"]),
        ("user_segmentation", ["top users", "user ids", "users", "audience", "segment", "cohort"]),
        ("sql_query", ["sql", "query", "queries", "definition", "definitions", "metric", "metrics", "payload"]),
        ("payment_orders", ["payment", "payments", "purchase", "order", "checkout", "confirmation"]),
        ("insight_why", ["insight", "insights", "why", "reason", "root cause", "summary", "summarize"]),
        ("comparison_trend", ["compare", "comparison", "trend", "changes", "increase", "decrease", "vs ", "versus"]),
        ("tool_usage", ["use ", "using ", "tool", "top_path_query", "query_user_event_timeline"]),
        ("time_bound", ["today", "yesterday", "feb", "march", "week", "month", "24th", "25th", "26th"]),
    ]
    labels = [label for label, keywords in rules if any(keyword in question_lower for keyword in keywords)]
    return labels or ["other"]


def infer_persona(thread_count, topic_counts, avg_question_len, chinese_ratio, payload_requests, evidence_requests):
    if thread_count >= 50 and avg_question_len > 400:
        return "Pattern/SQL Designer"
    if thread_count >= 50 and topic_counts["time_bound"] + topic_counts["user_segmentation"] >= topic_counts["journey_analysis"]:
        return "Batch Analysis Agent"
    if payload_requests >= 5 or evidence_requests >= 5:
        return "Validation-Focused Analyst"
    if chinese_ratio >= 0.5:
        return "Chinese Exploratory User"
    if avg_question_len < 120 and thread_count >= 10:
        return "Business PM / Analyst"
    return "General Explorer"


def main():
    customer_mapping = load_customer_mapping()
    per_user = defaultdict(
        lambda: {
            "turns": 0,
            "threads": set(),
            "customers": Counter(),
            "topics": Counter(),
            "question_length_total": 0,
            "chinese_turns": 0,
            "payload_requests": 0,
            "evidence_requests": 0,
        }
    )

    with open(TURNS_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            input_value = parse_structured(row["input"])
            metadata_value = parse_structured(row["metadata"])
            question = extract_question(input_value)
            user_id = str(metadata_value.get("user_id", "")) if isinstance(metadata_value, dict) else ""
            customer_id = str(metadata_value.get("customer_id", "")) if isinstance(metadata_value, dict) else ""
            if not user_id:
                continue

            user_row = per_user[user_id]
            user_row["turns"] += 1
            user_row["threads"].add(row["thread_id"])
            if customer_id:
                user_row["customers"][customer_id] += 1
            for topic in categorize_question(question):
                user_row["topics"][topic] += 1
            user_row["question_length_total"] += len(question)
            if re.search(r"[\u4e00-\u9fff]", question):
                user_row["chinese_turns"] += 1
            question_lower = question.lower()
            if any(keyword in question_lower for keyword in ("payload", "sql", "query details", "metric defintion", "metric definition")):
                user_row["payload_requests"] += 1
            if any(keyword in question_lower for keyword in ("raw data", "raw event", "evidence", "prove", "details")):
                user_row["evidence_requests"] += 1

    customer_rows = defaultdict(lambda: {"users": Counter(), "topics": Counter(), "user_count": 0, "turns": 0})

    for user_id, user_data in per_user.items():
        if not user_data["customers"]:
            continue
        primary_customer = user_data["customers"].most_common(1)[0][0]
        thread_count = len(user_data["threads"])
        avg_question_len = user_data["question_length_total"] / user_data["turns"] if user_data["turns"] else 0
        chinese_ratio = user_data["chinese_turns"] / user_data["turns"] if user_data["turns"] else 0
        persona = infer_persona(
            thread_count=thread_count,
            topic_counts=user_data["topics"],
            avg_question_len=avg_question_len,
            chinese_ratio=chinese_ratio,
            payload_requests=user_data["payload_requests"],
            evidence_requests=user_data["evidence_requests"],
        )

        customer_row = customer_rows[primary_customer]
        customer_row["users"][persona] += 1
        customer_row["user_count"] += 1
        customer_row["turns"] += user_data["turns"]
        for topic, count in user_data["topics"].items():
            customer_row["topics"][topic] += count

    output_rows = []
    for customer_id, customer_data in customer_rows.items():
        output_rows.append(
            {
                "customer_id": customer_id,
                "customer_name": customer_mapping.get(customer_id, customer_id),
                "user_count": customer_data["user_count"],
                "turn_count": customer_data["turns"],
                "top_persona_1": customer_data["users"].most_common(1)[0][0] if customer_data["users"] else "",
                "top_persona_1_count": customer_data["users"].most_common(1)[0][1] if customer_data["users"] else 0,
                "top_persona_2": customer_data["users"].most_common(2)[1][0] if len(customer_data["users"]) >= 2 else "",
                "top_persona_2_count": customer_data["users"].most_common(2)[1][1] if len(customer_data["users"]) >= 2 else 0,
                "top_topics": ",".join(topic for topic, _ in customer_data["topics"].most_common(5)),
            }
        )

    output_rows.sort(key=lambda row: row["turn_count"], reverse=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "customer_id",
                "customer_name",
                "user_count",
                "turn_count",
                "top_persona_1",
                "top_persona_1_count",
                "top_persona_2",
                "top_persona_2_count",
                "top_topics",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Saved customer personas to {OUTPUT_PATH}")
    print(f"customers: {len(output_rows)}")


if __name__ == "__main__":
    main()
