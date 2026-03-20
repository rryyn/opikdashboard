#!/usr/bin/env python3

import csv
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "conversation_experience.csv"
OUTPUT_PATH = BASE_DIR / "customer_experience_ranking.csv"
CUSTOMER_MAPPING_PATH = BASE_DIR / "c3mapping.xlsx"


def load_customer_mapping():
    if not CUSTOMER_MAPPING_PATH.exists():
        return {}

    with ZipFile(CUSTOMER_MAPPING_PATH) as archive:
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


def main():
    customer_mapping = load_customer_mapping()
    grouped = defaultdict(list)

    with open(INPUT_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            customer_id = row["customer_id"].strip()
            if not customer_id:
                continue
            grouped[customer_id].append(row)

    output_rows = []
    for customer_id, rows in grouped.items():
        conversation_count = len(rows)
        avg_overall = sum(float(row["overall_experience_score"]) for row in rows) / conversation_count
        avg_task_success = sum(float(row["task_success_score"]) for row in rows) / conversation_count
        avg_efficiency = sum(float(row["efficiency_score"]) for row in rows) / conversation_count
        avg_clarity = sum(float(row["clarity_score"]) for row in rows) / conversation_count
        avg_trust = sum(float(row["trust_score"]) for row in rows) / conversation_count
        avg_friction = sum(float(row["friction_score"]) for row in rows) / conversation_count
        labels = Counter(row["experience_label"] for row in rows)
        output_rows.append(
            {
                "customer_id": customer_id,
                "customer_name": customer_mapping.get(customer_id, customer_id),
                "conversation_count": conversation_count,
                "avg_overall_experience_score": round(avg_overall, 2),
                "avg_task_success_score": round(avg_task_success, 2),
                "avg_efficiency_score": round(avg_efficiency, 2),
                "avg_clarity_score": round(avg_clarity, 2),
                "avg_trust_score": round(avg_trust, 2),
                "avg_friction_score": round(avg_friction, 2),
                "good_count": labels.get("good", 0),
                "mixed_count": labels.get("mixed", 0),
                "poor_count": labels.get("poor", 0),
            }
        )

    output_rows.sort(
        key=lambda row: (
            -row["avg_overall_experience_score"],
            -row["conversation_count"],
        )
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "customer_id",
                "customer_name",
                "conversation_count",
                "avg_overall_experience_score",
                "avg_task_success_score",
                "avg_efficiency_score",
                "avg_clarity_score",
                "avg_trust_score",
                "avg_friction_score",
                "good_count",
                "mixed_count",
                "poor_count",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Saved customer ranking to {OUTPUT_PATH}")
    print(f"customers: {len(output_rows)}")


if __name__ == "__main__":
    main()
