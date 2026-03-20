#!/usr/bin/env python3

import ast
import json
from pathlib import Path


EXPORT_DIR = Path("/Users/linwang")
INPUT_PATH = EXPORT_DIR / "conversations.jsonl"
OUTPUT_PATH = Path(__file__).resolve().parent / "log_chat_data.js"


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


def extract_metadata(trace):
    metadata_value = parse_structured(trace.get("metadata"))
    return metadata_value if isinstance(metadata_value, dict) else {}


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


def clean_answer(answer):
    if not answer:
        return ""
    chunks = []
    marker = "SSEEvent(event='"
    if marker not in answer:
        return answer
    idx = 0
    while True:
        start = answer.find("data=", idx)
        if start == -1:
            break
        start += 5
        quote = answer[start:start + 1]
        if quote not in ("'", '"'):
            idx = start
            continue
        end = start + 1
        escaped = False
        while end < len(answer):
            char = answer[end]
            if char == "\\" and not escaped:
                escaped = True
                end += 1
                continue
            if char == quote and not escaped:
                break
            escaped = False
            end += 1
        raw = answer[start:end + 1]
        try:
            chunks.append(ast.literal_eval(raw))
        except Exception:
            pass
        idx = end + 1
    return "".join(chunks).strip() or answer


def main():
    conversations = []
    for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        traces = row.get("traces", [])
        turns = []
        total_cost = 0.0
        user_id = "n/a"
        customer_id = "n/a"
        for index, trace in enumerate(traces, start=1):
            metadata = extract_metadata(trace)
            user_id = str(metadata.get("user_id", user_id) or user_id)
            customer_id = str(metadata.get("customer_id", customer_id) or customer_id)
            try:
                total_cost += float(trace.get("total_estimated_cost") or 0)
            except Exception:
                pass
            turns.append(
                {
                    "index": index,
                    "start_time": str(trace.get("start_time", "") or ""),
                    "question": extract_question(trace),
                    "answer": clean_answer(extract_answer(trace)),
                }
            )
        conversations.append(
            {
                "thread_id": row.get("thread_id", ""),
                "user_id": user_id,
                "customer_id": customer_id,
                "turn_count": len(turns),
                "cost": round(total_cost, 4),
                "first_time": turns[0]["start_time"] if turns else "",
                "turns": turns,
            }
        )

    conversations.sort(key=lambda item: item["first_time"], reverse=True)
    payload = {"generated_from": str(INPUT_PATH), "conversations": conversations}
    OUTPUT_PATH.write_text("const LOG_CHAT_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"Generated {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
