#!/usr/bin/env python3

import ast
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "conversations.jsonl"
OUTPUT_PATH = BASE_DIR / "conversation_experience.csv"

csv.field_size_limit(sys.maxsize)


def parse_structured(value):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(value)
        except Exception:
            continue
    return {}


def extract_question(trace):
    input_value = parse_structured(trace.get("input"))
    if not isinstance(input_value, dict):
        return ""
    data = input_value.get("data", {})
    for key in ("user_question", "question", "input", "query", "message", "prompt"):
        if isinstance(data, dict) and data.get(key):
            return str(data.get(key))
        if input_value.get(key):
            return str(input_value.get(key))
    return ""


def extract_answer(trace):
    output_value = parse_structured(trace.get("output"))
    if isinstance(output_value, dict):
        return str(output_value.get("output") or output_value.get("response") or output_value.get("answer") or "")
    return str(output_value or "")


def extract_metadata(trace):
    metadata_value = parse_structured(trace.get("metadata"))
    return metadata_value if isinstance(metadata_value, dict) else {}


def score_task_success(metrics):
    score = 5
    if metrics["tool_failure_turns"] > 0:
        score -= 2
    if metrics["access_limitation_turns"] > 0:
        score -= 1
    if metrics["evidence_request_turns"] > 0 and metrics["evidence_signal_turns"] == 0:
        score -= 1
    if metrics["export_request_turns"] > 0 and metrics["artifact_delivery_signals"] == 0:
        score -= 1
    if metrics["turn_count"] >= 8 and metrics["repeat_question_turns"] >= 2:
        score -= 1
    return max(1, min(5, score))


def score_efficiency(metrics):
    score = 5
    if metrics["turn_count"] >= 3:
        score -= 1
    if metrics["turn_count"] >= 6:
        score -= 1
    if metrics["clarification_turns"] >= 2:
        score -= 1
    if metrics["repeat_question_turns"] >= 2:
        score -= 1
    if metrics["total_duration_minutes"] > 60:
        score -= 1
    return max(1, min(5, score))


def score_clarity(metrics):
    score = 5
    if metrics["clarification_turns"] >= 1:
        score -= 1
    if metrics["clarification_turns"] >= 3:
        score -= 1
    if metrics["query_payload_request_turns"] >= 1 and metrics["query_payload_signal_turns"] == 0:
        score -= 1
    if metrics["repeat_question_turns"] >= 2:
        score -= 1
    return max(1, min(5, score))


def score_trust(metrics):
    score = 5
    if metrics["evidence_request_turns"] >= 1:
        score -= 1
    if metrics["evidence_request_turns"] >= 2:
        score -= 1
    if metrics["tool_failure_turns"] > 0:
        score -= 1
    if metrics["access_limitation_turns"] > 0:
        score -= 1
    return max(1, min(5, score))


def score_friction(metrics):
    score = 5
    friction_points = (
        metrics["tool_failure_turns"]
        + metrics["access_limitation_turns"]
        + metrics["clarification_turns"]
        + metrics["repeat_question_turns"]
    )
    if friction_points >= 2:
        score -= 1
    if friction_points >= 4:
        score -= 1
    if friction_points >= 6:
        score -= 1
    if metrics["turn_count"] >= 8:
        score -= 1
    if metrics["export_request_turns"] > 0 and metrics["artifact_delivery_signals"] == 0:
        score -= 1
    return max(1, min(5, score))


def label_overall(score):
    if score >= 4.2:
        return "good"
    if score >= 3.0:
        return "mixed"
    return "poor"


def summarize_reasons(metrics):
    reasons = []
    if metrics["tool_failure_turns"] > 0:
        reasons.append("tool_failure")
    if metrics["access_limitation_turns"] > 0:
        reasons.append("access_limitation")
    if metrics["clarification_turns"] >= 2:
        reasons.append("heavy_clarification")
    if metrics["repeat_question_turns"] >= 2:
        reasons.append("repeat_reframing")
    if metrics["evidence_request_turns"] > 0:
        reasons.append("evidence_needed")
    if metrics["query_payload_request_turns"] > 0:
        reasons.append("query_transparency_needed")
    if not reasons:
        reasons.append("smooth_path")
    return ",".join(reasons)


def main():
    rows = []
    with open(INPUT_PATH, "r", encoding="utf-8") as file:
        for line in file:
            conversation = json.loads(line)
            traces = conversation.get("traces", [])
            if not traces:
                continue

            questions = []
            normalized_questions = []
            metadata_list = []
            total_duration_seconds = 0.0
            clarification_turns = 0
            tool_failure_turns = 0
            access_limitation_turns = 0
            evidence_signal_turns = 0
            query_payload_signal_turns = 0
            artifact_delivery_signals = 0
            evidence_request_turns = 0
            query_payload_request_turns = 0
            export_request_turns = 0

            for trace in traces:
                question = extract_question(trace)
                answer = extract_answer(trace).lower()
                metadata = extract_metadata(trace)
                questions.append(question)
                normalized_questions.append(question.strip().lower())
                metadata_list.append(metadata)

                start_time = trace.get("start_time")
                end_time = trace.get("end_time")
                if start_time and end_time:
                    total_duration_seconds += (
                        datetime.fromisoformat(str(end_time)) - datetime.fromisoformat(str(start_time))
                    ).total_seconds()

                if any(keyword in answer for keyword in ("clarifying", "ambiguity", "ambiguous", "what qualifies", "interpreting")):
                    clarification_turns += 1
                if any(keyword in answer for keyword in ("failing", "internal problem", "error message", "system-level issue", "empty response")):
                    tool_failure_turns += 1
                if any(keyword in answer for keyword in ("can't directly", "cannot directly", "limitations of my access", "access limitations")):
                    access_limitation_turns += 1
                if any(keyword in answer for keyword in ("raw data", "raw event", "evidence", "prove", "details")):
                    evidence_signal_turns += 1
                if any(keyword in answer for keyword in ("payload", "sql definitions", "metric defintion", "metric definition", "query details")):
                    query_payload_signal_turns += 1
                if any(keyword in answer for keyword in ("csv", "table", "downloadable", "saved", "exported")):
                    artifact_delivery_signals += 1

                question_lower = question.lower()
                if any(keyword in question_lower for keyword in ("raw data", "raw event", "evidence", "prove", "details")):
                    evidence_request_turns += 1
                if any(keyword in question_lower for keyword in ("payload", "sql", "query details", "metric defintion", "metric definition")):
                    query_payload_request_turns += 1
                if any(keyword in question_lower for keyword in ("csv", "save it into one csv file", "table", "export")):
                    export_request_turns += 1

            question_counts = Counter(q for q in normalized_questions if q)
            repeat_question_turns = sum(count - 1 for count in question_counts.values() if count > 1)

            primary_metadata = next((item for item in metadata_list if item), {})
            user_id = str(primary_metadata.get("user_id", "")) if primary_metadata else ""
            customer_id = str(primary_metadata.get("customer_id", "")) if primary_metadata else ""

            metrics = {
                "thread_id": conversation.get("thread_id", ""),
                "turn_count": len(traces),
                "total_duration_minutes": round(total_duration_seconds / 60, 2),
                "clarification_turns": clarification_turns,
                "tool_failure_turns": tool_failure_turns,
                "access_limitation_turns": access_limitation_turns,
                "evidence_signal_turns": evidence_signal_turns,
                "query_payload_signal_turns": query_payload_signal_turns,
                "artifact_delivery_signals": artifact_delivery_signals,
                "evidence_request_turns": evidence_request_turns,
                "query_payload_request_turns": query_payload_request_turns,
                "export_request_turns": export_request_turns,
                "repeat_question_turns": repeat_question_turns,
            }

            task_success = score_task_success(metrics)
            efficiency = score_efficiency(metrics)
            clarity = score_clarity(metrics)
            trust = score_trust(metrics)
            friction = score_friction(metrics)
            overall_score = round((task_success + efficiency + clarity + trust + friction) / 5, 2)

            rows.append(
                {
                    "thread_id": metrics["thread_id"],
                    "user_id": user_id,
                    "customer_id": customer_id,
                    "turn_count": metrics["turn_count"],
                    "total_duration_minutes": metrics["total_duration_minutes"],
                    "task_success_score": task_success,
                    "efficiency_score": efficiency,
                    "clarity_score": clarity,
                    "trust_score": trust,
                    "friction_score": friction,
                    "overall_experience_score": overall_score,
                    "experience_label": label_overall(overall_score),
                    "clarification_turns": clarification_turns,
                    "tool_failure_turns": tool_failure_turns,
                    "access_limitation_turns": access_limitation_turns,
                    "evidence_request_turns": evidence_request_turns,
                    "query_payload_request_turns": query_payload_request_turns,
                    "export_request_turns": export_request_turns,
                    "repeat_question_turns": repeat_question_turns,
                    "reason_flags": summarize_reasons(metrics),
                    "first_question": questions[0][:300] if questions else "",
                }
            )

    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "thread_id",
                "user_id",
                "customer_id",
                "turn_count",
                "total_duration_minutes",
                "task_success_score",
                "efficiency_score",
                "clarity_score",
                "trust_score",
                "friction_score",
                "overall_experience_score",
                "experience_label",
                "clarification_turns",
                "tool_failure_turns",
                "access_limitation_turns",
                "evidence_request_turns",
                "query_payload_request_turns",
                "export_request_turns",
                "repeat_question_turns",
                "reason_flags",
                "first_question",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    labels = Counter(row["experience_label"] for row in rows)
    print(f"Saved experience scores to {OUTPUT_PATH}")
    print(f"total conversations: {len(rows)}")
    print(f"good: {labels.get('good', 0)}")
    print(f"mixed: {labels.get('mixed', 0)}")
    print(f"poor: {labels.get('poor', 0)}")


if __name__ == "__main__":
    main()
