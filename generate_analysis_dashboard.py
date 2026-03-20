#!/usr/bin/env python3

import ast
import csv
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = Path("/Users/linwang")
ENV_DATA_DIR = BASE_DIR / "env_data"
CONVERSATIONS_PATH = EXPORT_DIR / "conversations.jsonl"
SUMMARY_PATH = EXPORT_DIR / "conversations_summary.csv"
TURNS_PATH = EXPORT_DIR / "turns_flat.csv"
OUTPUT_PATH = BASE_DIR / "analysis_dashboard.html"
CUSTOMER_MAPPING_PATH = BASE_DIR / "c3mapping.xlsx"
EXPERIENCE_PATH = BASE_DIR / "conversation_experience.csv"
CUSTOMER_EXPERIENCE_PATH = BASE_DIR / "customer_experience_ranking.csv"
DETAILS_DIR = BASE_DIR / "bad_case_details"
CONVERSATION_DETAILS_DIR = BASE_DIR / "conversation_details"
OPIK_CONFIG_PATH = BASE_DIR / "opik_config.json"

csv.field_size_limit(sys.maxsize)


def parse_structured(text):
    if isinstance(text, (dict, list)):
        return text
    if not text:
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(text)
        except Exception:
            continue
    return {}


def load_opik_config():
    if not OPIK_CONFIG_PATH.exists():
        return {}
    with open(OPIK_CONFIG_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


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


def extract_answer(trace):
    output_value = parse_structured(trace.get("output"))
    if isinstance(output_value, dict):
        return str(output_value.get("output") or output_value.get("response") or output_value.get("answer") or "")
    return str(output_value or "")


def extract_metadata(trace):
    metadata_value = parse_structured(trace.get("metadata"))
    return metadata_value if isinstance(metadata_value, dict) else {}


def categorize_question(question):
    question_lower = question.lower()
    rules = [
        ("Journey Analysis", ["journey", "journeys", "path", "paths", "timeline", "pattern", "patterns", "funnel", "flow"]),
        ("User Segmentation", ["top users", "user ids", "users", "audience", "segment", "cohort"]),
        ("SQL / Query Design", ["sql", "query", "queries", "definition", "definitions", "metric", "metrics", "payload"]),
        ("Payment / Orders", ["payment", "payments", "purchase", "order", "checkout", "confirmation"]),
        ("Insight / Why", ["insight", "insights", "why", "reason", "root cause", "summary", "summarize"]),
        ("Comparison / Trend", ["compare", "comparison", "trend", "changes", "increase", "decrease", "vs ", "versus"]),
        ("Tool Usage", ["use ", "using ", "tool", "top_path_query", "query_user_event_timeline"]),
        ("Time-bound Analysis", ["today", "yesterday", "feb", "march", "week", "month", "24th", "25th", "26th"]),
    ]
    labels = [label for label, keywords in rules if any(keyword in question_lower for keyword in keywords)]
    return labels or ["Other"]


def detect_quality_signals(answer_text):
    answer_lower = answer_text.lower()
    signals = {
        "Clarification / ambiguity": ["clarifying", "ambiguity", "ambiguous", "what qualifies", "interpret"],
        "Tool or system failure": ["failing", "internal problem", "error message", "system-level issue", "empty response"],
        "Evidence / raw data emphasis": ["raw data", "raw event", "evidence", "prove", "details"],
        "Payload / query transparency": ["payload", "sql definitions", "metric defintion", "metric definition", "query details"],
        "Access limitation": ["can't directly", "limitations of my access", "cannot directly", "access limitations"],
    }
    return [name for name, keywords in signals.items() if any(keyword in answer_lower for keyword in keywords)]


CLARIFICATION_PATTERNS = [
    r"\bnot right\b",
    r"\bto be clear\b",
    r"\bi mean\b",
    r"\bwhat i mean\b",
    r"\bthat's not\b",
    r"\bthat is not\b",
    r"\bdidn't answer\b",
    r"\bdid not answer\b",
    r"\bnot what i\b",
    r"\byou missed\b",
    r"\bi asked\b",
    r"\bhow did you define\b",
    r"\bdid you read our previous conversations\b",
    r"\bstill using\b",
    r"\brun this same analysis\b",
    r"\bshould be\b",
    r"\bexclude bot traffic\b",
    r"\bactually\b",
    r"\blet me rephrase\b",
    r"\brephrase\b",
    r"\breframe\b",
    r"\bexact system labels\b",
    r"\braw event conditions\b",
    r"\bwith evidence\b",
    r"你没回答",
    r"不是这个意思",
    r"不是这个问题",
    r"我问的是",
    r"你返回的.*跟.*不一致",
    r"你返回的.*跟.*对不上",
    r"和.*不一致",
    r"跟.*不一致",
]
CLARIFICATION_REGEX = re.compile("|".join(f"(?:{pattern})" for pattern in CLARIFICATION_PATTERNS), re.IGNORECASE)
CLARIFICATION_EXCLUDE_REGEX = re.compile(
    r"(?:\bnetwork error\b|\b499 error\b|\bkeep going\b|\bpick it up where you left off\b|\bnow i want to understand\b|\busing data from\b|\bconduct a systematic\b|\bcan you tell how quickly\b|\bfor devices that reached\b|\btop path query\b|\btop 15\b)",
    re.IGNORECASE,
)
SMALLTALK_PREFIXES = (
    "hi",
    "hello",
    "are you working",
    "are you there",
    "what is the latest date timestamp you have right now",
)


def is_clarification_followup(question):
    question = question or ""
    return bool(CLARIFICATION_REGEX.search(question)) and not CLARIFICATION_EXCLUDE_REGEX.search(question)


def is_smalltalk_question(question):
    normalized = (question or "").strip().lower()
    return any(normalized.startswith(prefix) for prefix in SMALLTALK_PREFIXES) or len(normalized) < 12


def classify_clarification_root_cause(first_question, followup_question):
    follow = (followup_question or "").lower()
    first = (first_question or "").lower()

    evidence_signals = [
        "exact system labels",
        "raw event conditions",
        "raw",
        "with evidence",
        "payload",
        "sql",
        "query details",
        "metric definition",
    ]
    definition_signals = [
        "not right",
        "to be clear",
        "i mean",
        "what i mean",
        "let me rephrase",
        "rephrase",
        "reframe",
        "how did you define",
        "did you read our previous conversations",
        "still using",
        "run this same analysis",
        "should be",
        "exclude bot traffic",
    ]
    scope_change_signals = [
        "now i want to understand",
        "using data from",
        "conduct a systematic",
        "can you tell how quickly",
        "for devices that reached",
    ]
    failure_signals = [
        "network error",
        "499 error",
        "pick it up where you left off",
        "keep going",
    ]

    if any(signal in follow for signal in failure_signals):
        return "Failure / resume artifact"
    if any(signal in follow for signal in evidence_signals):
        return "Need raw evidence / exact conditions"
    if any(signal in follow for signal in definition_signals):
        return "Definition mismatch"
    if any(signal in follow for signal in scope_change_signals):
        return "User changed scope"
    if len(follow) > len(first) * 1.2:
        return "Answer too broad"
    return "Answer too broad"


def score_current_export_conversation(turns, clarification_turns):
    signal_set = {signal for turn in turns for signal in turn.get("signals", [])}
    score = 5.0
    if clarification_turns:
        score -= 1.0 + min(clarification_turns - 1, 2) * 0.4
    if "Tool or system failure" in signal_set:
        score -= 0.7
    if "Access limitation" in signal_set:
        score -= 0.4
    if "Evidence / raw data emphasis" in signal_set:
        score -= 0.4
    if "Payload / query transparency" in signal_set:
        score -= 0.2
    if len(turns) >= 8:
        score -= 0.4
    elif len(turns) >= 5:
        score -= 0.2
    return max(1.0, round(score, 1))


def label_score(score):
    if score <= 2.8:
        return "poor"
    if score <= 3.8:
        return "mixed"
    return "good"


SSE_EVENT_REGEX = re.compile(r"SSEEvent\(event='([^']+)', data=(\".*?\"|'.*?')\)")


def parse_sse_answer(answer_text):
    if not answer_text or "SSEEvent(" not in answer_text:
        return None
    events = []
    for event_name, raw_data in SSE_EVENT_REGEX.findall(answer_text):
        try:
            data = ast.literal_eval(raw_data)
        except Exception:
            data = raw_data.strip("'\"")
        events.append({"event": event_name, "data": str(data)})
    if not events:
        return None
    thoughts = [event["data"] for event in events if event["event"] == "thought" and event["data"].strip()]
    deltas = [event["data"] for event in events if event["event"] == "delta"]
    final_answer = "".join(deltas).strip()
    return {
        "thoughts": thoughts,
        "final_answer": final_answer,
        "events": events,
    }


def format_inline_markdown(text):
    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def render_rich_text_html(text):
    if not text:
        return '<p class="empty">(empty)</p>'
    lines = text.replace("\r\n", "\n").split("\n")
    parts = []
    paragraph = []
    list_items = []

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            joined = " ".join(chunk.strip() for chunk in paragraph if chunk.strip())
            if joined:
                parts.append(f"<p>{format_inline_markdown(joined)}</p>")
            paragraph = []

    def flush_list():
        nonlocal list_items
        if list_items:
            items = "".join(f"<li>{format_inline_markdown(item)}</li>" for item in list_items)
            parts.append(f"<ul>{items}</ul>")
            list_items = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h4>{format_inline_markdown(line[4:].strip())}</h4>")
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h3>{format_inline_markdown(line[3:].strip())}</h3>")
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_list()
            parts.append(f"<h2>{format_inline_markdown(line[2:].strip())}</h2>")
            continue
        if re.fullmatch(r"---+", line):
            flush_paragraph()
            flush_list()
            parts.append("<hr />")
            continue
        if line.startswith("- ") or line.startswith("* "):
            flush_paragraph()
            list_items.append(line[2:].strip())
            continue
        flush_list()
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    return "".join(parts) or '<p class="empty">(empty)</p>'


def render_answer_html(answer_text):
    parsed = parse_sse_answer(answer_text)
    if not parsed:
        return render_rich_text_html(answer_text)

    sections = []
    if parsed["final_answer"]:
        sections.append(
            f"""
            <section class="answer-section">
              <h3>Final Answer</h3>
              <div class="rich-text">{render_rich_text_html(parsed["final_answer"])}</div>
            </section>
            """
        )
    if parsed["thoughts"]:
        thought_blocks = "".join(
            f'<div class="thought-block">{render_rich_text_html(thought)}</div>' for thought in parsed["thoughts"]
        )
        sections.append(
            f"""
            <details class="thoughts">
              <summary>Reasoning Steps ({len(parsed["thoughts"])})</summary>
              <div class="thoughts-body">{thought_blocks}</div>
            </details>
            """
        )
    if not sections:
        sections.append(f'<div class="rich-text">{render_rich_text_html(answer_text)}</div>')
    return "".join(sections)


def percentile(values, ratio):
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * ratio) - 1))
    return ordered[index]


def format_duration_minutes(minutes):
    total_minutes = max(0, int(round(minutes or 0)))
    hours, mins = divmod(total_minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def format_duration_seconds(seconds):
    total_seconds = max(0, int(round(seconds or 0)))
    minutes, secs = divmod(total_seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def detail_page_styles():
    return """
    :root {
      --background: #fcfcfd;
      --panel: #ffffff;
      --panel-soft: #f8f8f8;
      --ink: #222629;
      --muted: #707372;
      --muted-2: #9b9d9c;
      --accent: #5a9b00;
      --accent-strong: #4a8500;
      --accent-soft: #e8f5d0;
      --line: #e2e3e3;
      --line-soft: #f1f1f1;
      --shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--background);
      color: var(--ink);
    }
    .wrap {
      max-width: 1120px;
      margin: 0 auto;
      padding: 24px 18px 40px;
    }
    .hero, .turn {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 18px;
      margin-bottom: 16px;
      min-width: 0;
      overflow: hidden;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 28px;
      line-height: 1.1;
      letter-spacing: -0.02em;
      overflow-wrap: anywhere;
    }
    p {
      overflow-wrap: anywhere;
    }
    .summary {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
      min-width: 0;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-soft);
      color: #424548;
      font-size: 12px;
      max-width: 100%;
      min-width: 0;
      overflow-wrap: normal;
      word-break: normal;
    }
    .turn-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 10px;
      font-size: 14px;
      color: var(--muted);
      min-width: 0;
    }
    .turn-head span:last-child {
      text-align: right;
      overflow-wrap: normal;
    }
    .turn-index {
      font-weight: 700;
      color: var(--accent);
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 14px;
      min-width: 0;
    }
    .meta span {
      min-width: 0;
      overflow-wrap: normal;
    }
    h3 {
      margin: 14px 0 6px;
      font-size: 16px;
    }
    h4 {
      margin: 12px 0 6px;
      font-size: 14px;
    }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
      background: #fcfcfc;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      padding: 12px;
      margin: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 13px;
      line-height: 1.55;
      max-width: 100%;
      overflow-x: auto;
    }
    a {
      color: var(--accent-strong);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    code {
      background: var(--line-soft);
      border-radius: 6px;
      padding: 1px 5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.95em;
      overflow-wrap: anywhere;
    }
    hr {
      border: 0;
      border-top: 1px solid var(--line);
      margin: 14px 0;
    }
    .rich-text, .answer-section, .thoughts-body, .thought-block {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .rich-text p {
      margin: 0 0 10px;
      line-height: 1.65;
    }
    .rich-text ul {
      margin: 0 0 12px 18px;
      padding: 0;
    }
    .rich-text li {
      margin: 0 0 6px;
      line-height: 1.6;
    }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
    .answer-section {
      margin-top: 10px;
    }
    .thoughts {
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fcfcfc;
      padding: 12px 14px;
      min-width: 0;
      overflow: hidden;
    }
    .thoughts summary {
      cursor: pointer;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .thoughts-body {
      margin-top: 12px;
      display: grid;
      gap: 12px;
    }
    .thought-block {
      border-top: 1px solid var(--line-soft);
      padding-top: 12px;
    }
    .thought-block:first-child {
      border-top: 0;
      padding-top: 0;
    }
    @media (max-width: 720px) {
      .wrap {
        padding: 18px 14px 32px;
      }
      .hero, .turn {
        padding: 16px;
      }
      .turn-head {
        flex-direction: column;
        align-items: flex-start;
      }
    }
    """


def load_customer_mapping():
    if not CUSTOMER_MAPPING_PATH.exists():
        return {}

    with ZipFile(CUSTOMER_MAPPING_PATH) as archive:
        namespace = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

        shared_strings = []
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
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
        if len(row) >= 2:
            customer_id = str(row[0]).strip()
            customer_name = str(row[1]).strip()
            if customer_id and customer_name:
                mapping[customer_id] = customer_name
    return mapping


def parse_summary():
    rows = []
    with open(SUMMARY_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            turn_count = int(row["turn_count"])
            first = datetime.fromisoformat(row["first_start_time"])
            last = datetime.fromisoformat(row["last_start_time"])
            rows.append(
                {
                    "thread_id": row["thread_id"],
                    "turn_count": turn_count,
                    "first_start_time": first,
                    "last_start_time": last,
                    "conversation_minutes": (last - first).total_seconds() / 60,
                }
            )
    return rows


def parse_trace_costs():
    costs = []
    with open(CONVERSATIONS_PATH, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            conversation = json.loads(line)
            for trace in conversation.get("traces", []):
                metadata = trace.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                raw_cost = trace.get("total_estimated_cost", 0)
                try:
                    cost = float(raw_cost or 0)
                except (TypeError, ValueError):
                    cost = 0.0
                costs.append(
                    {
                        "thread_id": conversation.get("thread_id", ""),
                        "trace_id": trace.get("id", ""),
                        "user_id": str(metadata.get("user_id", "") or ""),
                        "cost": cost,
                    }
                )
    return costs


def parse_turns():
    turns = []
    with open(TURNS_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            input_value = parse_structured(row["input"])
            output_value = parse_structured(row["output"])
            metadata_value = parse_structured(row["metadata"])
            start_time = datetime.fromisoformat(row["start_time"]) if row["start_time"] else None
            end_time = datetime.fromisoformat(row["end_time"]) if row["end_time"] else None
            duration_seconds = (end_time - start_time).total_seconds() if start_time and end_time else 0
            question = extract_question(input_value)
            answer_text = ""
            if isinstance(output_value, dict):
                answer_text = str(output_value.get("output") or output_value.get("response") or output_value.get("answer") or "")
            turns.append(
                {
                    "thread_id": row["thread_id"],
                    "trace_id": row["trace_id"],
                    "start_time": start_time,
                    "question": question,
                    "question_preview": question[:180],
                    "answer_preview": answer_text[:260],
                    "duration_seconds": duration_seconds,
                    "user_id": str(metadata_value.get("user_id", "")) if isinstance(metadata_value, dict) else "",
                    "customer_id": str(metadata_value.get("customer_id", "")) if isinstance(metadata_value, dict) else "",
                    "categories": categorize_question(question),
                    "signals": detect_quality_signals(answer_text),
                }
            )
    return turns


def parse_experience_rows():
    if not EXPERIENCE_PATH.exists():
        return []
    rows = []
    with open(EXPERIENCE_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            normalized = dict(row)
            for key in (
                "turn_count",
                "total_duration_minutes",
                "task_success_score",
                "efficiency_score",
                "clarity_score",
                "trust_score",
                "friction_score",
                "overall_experience_score",
                "clarification_turns",
                "tool_failure_turns",
                "access_limitation_turns",
                "evidence_request_turns",
                "query_payload_request_turns",
                "export_request_turns",
                "repeat_question_turns",
            ):
                value = normalized.get(key, "")
                normalized[key] = float(value) if "." in value else int(value or 0)
            rows.append(normalized)
    return rows


def parse_customer_experience_rows():
    if not CUSTOMER_EXPERIENCE_PATH.exists():
        return []
    rows = []
    with open(CUSTOMER_EXPERIENCE_PATH, "r", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            normalized = dict(row)
            for key in (
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
            ):
                value = normalized.get(key, "")
                normalized[key] = float(value) if "." in value else int(value or 0)
            rows.append(normalized)
    return rows


def detail_filename(thread_id):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", thread_id).strip("_")
    if not safe:
        safe = "thread"
    return f"{safe}.html"


def conversation_detail_link(thread_id):
    return f"{CONVERSATION_DETAILS_DIR.name}/{detail_filename(thread_id)}"


def user_anchor(user_id):
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", user_id).strip("-").lower()
    if not safe:
        safe = "unknown-user"
    return f"user-{safe}"


def clarification_user_anchor(user_id):
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", user_id).strip("-").lower()
    if not safe:
        safe = "unknown-user"
    return f"clarification-user-{safe}"


def load_conversation_lookup(thread_ids):
    remaining = set(thread_ids)
    lookup = {}
    if not remaining or not CONVERSATIONS_PATH.exists():
        return lookup
    with open(CONVERSATIONS_PATH, "r", encoding="utf-8") as file:
        for line in file:
            conversation = json.loads(line)
            thread_id = conversation.get("thread_id", "")
            if thread_id in remaining:
                lookup[thread_id] = conversation
                remaining.remove(thread_id)
                if not remaining:
                    break
    return lookup


def render_detail_page(case_row, conversation):
    traces = conversation.get("traces", [])
    turn_sections = []
    for index, trace in enumerate(traces, start=1):
        question = extract_question(parse_structured(trace.get("input")))
        answer = extract_answer(trace)
        metadata = extract_metadata(trace)
        user_id = str(metadata.get("user_id", "")) if metadata else ""
        customer_id = str(metadata.get("customer_id", "")) if metadata else ""
        turn_sections.append(
            f"""
            <section class="turn">
              <div class="turn-head">
                <span class="turn-index">Turn {index}</span>
                <span>{html.escape(str(trace.get("start_time", "")))}</span>
              </div>
              <div class="meta">
                <span>User: {html.escape(user_id or "n/a")}</span>
                <span>Customer ID: {html.escape(customer_id or "n/a")}</span>
                <span>Trace ID: {html.escape(str(trace.get("id", "")))}</span>
              </div>
              <h3>Question</h3>
              <pre>{html.escape(question or "(empty)")}</pre>
              <h3>Answer</h3>
              {render_answer_html(answer or "")}
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(case_row["thread_id"])}</title>
  <style>
    {detail_page_styles()}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero" id="top">
      <a href="../analysis_dashboard.html">Back to dashboard</a>
      <h1>{html.escape(case_row["thread_id"])}</h1>
      <p>{html.escape(case_row["question"])}</p>
      <div class="summary">
        <span class="pill">Score {case_row["score"]:.1f}</span>
        <span class="pill">{html.escape(case_row["label"])}</span>
        <span class="pill">{html.escape(case_row["customer"])}</span>
        <span class="pill">{html.escape(case_row["user_id"])}</span>
        <span class="pill">{html.escape(case_row["reasons"])}</span>
      </div>
    </section>
    {''.join(turn_sections)}
  </div>
</body>
</html>
"""


def render_conversation_page(summary_row, conversation):
    traces = conversation.get("traces", [])
    turn_sections = []
    for index, trace in enumerate(traces, start=1):
        question = extract_question(parse_structured(trace.get("input")))
        answer = extract_answer(trace)
        metadata = extract_metadata(trace)
        user_id = str(metadata.get("user_id", "")) if metadata else ""
        customer_id = str(metadata.get("customer_id", "")) if metadata else ""
        turn_sections.append(
            f"""
            <section class="turn">
              <div class="turn-head">
                <span class="turn-index">Turn {index}</span>
                <span>{html.escape(str(trace.get("start_time", "")))}</span>
              </div>
              <div class="meta">
                <span>User: {html.escape(user_id or "n/a")}</span>
                <span>Customer ID: {html.escape(customer_id or "n/a")}</span>
                <span>Trace ID: {html.escape(str(trace.get("id", "")))}</span>
              </div>
              <h3>Question</h3>
              <pre>{html.escape(question or "(empty)")}</pre>
              <h3>Answer</h3>
              {render_answer_html(answer or "")}
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(summary_row["thread_id"])}</title>
  <style>
    {detail_page_styles()}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero" id="top">
      <a href="../analysis_dashboard.html">Back to dashboard</a>
      <h1>{html.escape(summary_row["thread_id"])}</h1>
      <p>{html.escape(summary_row.get("question", ""))}</p>
      <div class="summary">
        <span class="pill">{html.escape(summary_row.get("user_id", "n/a"))}</span>
        <span class="pill">{html.escape(summary_row.get("customer", "n/a"))}</span>
        <span class="pill">Turns {html.escape(str(summary_row.get("turn_count", "n/a")))}</span>
        <span class="pill">Duration {html.escape(str(summary_row.get("minutes", "n/a")))}</span>
      </div>
    </section>
    {''.join(turn_sections)}
  </div>
</body>
</html>
"""


def render_missing_conversation_page(summary_row):
    pills = []
    for value in (
        summary_row.get("user_id", ""),
        summary_row.get("customer", ""),
        f"Turns {summary_row['turn_count']}" if summary_row.get("turn_count") not in ("", None) else "",
        f"Duration {summary_row['minutes']}" if summary_row.get("minutes") else "",
        f"Score {summary_row['score']:.1f}" if isinstance(summary_row.get("score"), (int, float)) else "",
        str(summary_row.get("label", "")).title() if summary_row.get("label") else "",
    ):
        if value:
            pills.append(f'<span class="pill">{html.escape(str(value))}</span>')

    sections = [
        """
        <section class="turn">
          <h3>Availability</h3>
          <p>The raw turn-by-turn conversation is not present in the current export, so this page shows the best available metadata for the thread.</p>
        </section>
        """
    ]

    if summary_row.get("question"):
        sections.append(
            f"""
            <section class="turn">
              <h3>Known First Question</h3>
              <pre>{html.escape(summary_row.get("question") or "(empty)")}</pre>
            </section>
            """
        )
    if summary_row.get("reason"):
        sections.append(
            f"""
            <section class="turn">
              <h3>Assessment Note</h3>
              <div class="rich-text"><p>{html.escape(summary_row.get("reason") or "")}</p></div>
            </section>
            """
        )
    if summary_row.get("reasons"):
        sections.append(
            f"""
            <section class="turn">
              <h3>Friction Reasons</h3>
              <div class="rich-text"><p>{html.escape(summary_row.get("reasons") or "")}</p></div>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(summary_row["thread_id"])}</title>
  <style>
    {detail_page_styles()}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero" id="top">
      <a href="../analysis_dashboard.html">Back to dashboard</a>
      <h1>{html.escape(summary_row["thread_id"])}</h1>
      <p>{html.escape(summary_row.get("question", "") or "Thread reference page")}</p>
      <div class="summary">
        {''.join(pills)}
      </div>
    </section>
    {''.join(sections)}
  </div>
</body>
</html>
"""


def write_detail_pages(worst_cases, conversation_lookup):
    DETAILS_DIR.mkdir(exist_ok=True)
    for path in DETAILS_DIR.glob("*.html"):
        path.unlink()
    for case_row in worst_cases:
        conversation = conversation_lookup.get(case_row["thread_id"])
        if not conversation:
            continue
        output_path = DETAILS_DIR / detail_filename(case_row["thread_id"])
        with open(output_path, "w", encoding="utf-8") as file:
            file.write(render_detail_page(case_row, conversation))


def write_conversation_detail_pages(conversation_rows, conversation_lookup):
    CONVERSATION_DETAILS_DIR.mkdir(exist_ok=True)
    for path in CONVERSATION_DETAILS_DIR.glob("*.html"):
        path.unlink()
    for row in conversation_rows:
        conversation = conversation_lookup.get(row["thread_id"])
        output_path = CONVERSATION_DETAILS_DIR / detail_filename(row["thread_id"])
        with open(output_path, "w", encoding="utf-8") as file:
            if conversation:
                file.write(render_conversation_page(row, conversation))
            else:
                file.write(render_missing_conversation_page(row))


def set_runtime_context(export_dir, env_key, output_name):
    global CONVERSATIONS_PATH, SUMMARY_PATH, TURNS_PATH, OPIK_CONFIG_PATH, OUTPUT_PATH, CONVERSATION_DETAILS_DIR, DETAILS_DIR
    CONVERSATIONS_PATH = export_dir / "conversations.jsonl"
    SUMMARY_PATH = export_dir / "conversations_summary.csv"
    TURNS_PATH = export_dir / "turns_flat.csv"
    OPIK_CONFIG_PATH = export_dir / "config.json"
    OUTPUT_PATH = BASE_DIR / output_name
    CONVERSATION_DETAILS_DIR = BASE_DIR / f"conversation_details_{env_key}"
    DETAILS_DIR = BASE_DIR / f"bad_case_details_{env_key}"


def build_dashboard_data():
    summary_rows = parse_summary()
    turns = parse_turns()
    trace_costs = parse_trace_costs()
    customer_mapping = load_customer_mapping()
    experience_rows = parse_experience_rows()
    customer_experience_rows = parse_customer_experience_rows()
    opik_config = load_opik_config()

    turn_counts = [row["turn_count"] for row in summary_rows]
    conversation_minutes = [row["conversation_minutes"] for row in summary_rows]
    turn_durations = [row["duration_seconds"] for row in turns]
    conversations_by_day = Counter(row["first_start_time"].date().isoformat() for row in summary_rows)

    category_turn_counts = Counter()
    category_conversation_counts = Counter()
    questions_by_thread = defaultdict(list)
    for turn in turns:
        for category in turn["categories"]:
            category_turn_counts[category] += 1
        questions_by_thread[turn["thread_id"]].extend(turn["categories"])
    for categories in questions_by_thread.values():
        for category in sorted(set(categories)):
            category_conversation_counts[category] += 1

    quality_signal_counts = Counter()
    for turn in turns:
        for signal in turn["signals"]:
            quality_signal_counts[signal] += 1

    top_users = Counter(turn["user_id"] for turn in turns if turn["user_id"]).most_common(8)
    top_users_by_cost_counter = defaultdict(float)
    for trace in trace_costs:
        if trace["user_id"] and trace["cost"] > 0:
            top_users_by_cost_counter[trace["user_id"]] += trace["cost"]
    top_users_by_cost = sorted(
        top_users_by_cost_counter.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:8]
    top_customers = Counter(turn["customer_id"] for turn in turns if turn["customer_id"]).most_common(6)
    def display_customer(customer_id):
        return customer_mapping.get(customer_id, customer_id)

    summary_by_thread = {row["thread_id"]: row for row in summary_rows}
    turns_by_thread = defaultdict(list)
    for turn in turns:
        turns_by_thread[turn["thread_id"]].append(turn)
    conversation_rows = []
    for row in summary_rows:
        thread_turns = sorted(turns_by_thread.get(row["thread_id"], []), key=lambda item: item.get("start_time") or datetime.min)
        first_turn = thread_turns[0] if thread_turns else {}
        conversation_rows.append(
            {
                "thread_id": row["thread_id"],
                "user_id": first_turn.get("user_id") or "n/a",
                "customer": display_customer(first_turn.get("customer_id", "")) if first_turn.get("customer_id") else "n/a",
                "turn_count": row["turn_count"],
                "minutes": format_duration_minutes(row["conversation_minutes"]),
                "sort_minutes": round(row["conversation_minutes"], 2),
                "question": first_turn.get("question_preview", ""),
                "detail_link": conversation_detail_link(row["thread_id"]),
            }
        )
    conversation_row_by_thread = {row["thread_id"]: row for row in conversation_rows}

    cost_by_thread = defaultdict(float)
    for trace in trace_costs:
        if trace["thread_id"]:
            cost_by_thread[trace["thread_id"]] += trace["cost"]

    longest_conversations = sorted(
        (
            {
                "thread_id": row["thread_id"],
                "turn_count": row["turn_count"],
                "minutes": format_duration_minutes(row["conversation_minutes"]),
                "sort_minutes": round(row["conversation_minutes"], 2),
                "detail_link": conversation_detail_link(row["thread_id"]),
            }
            for row in summary_rows
        ),
        key=lambda item: (item["turn_count"], item["sort_minutes"]),
        reverse=True,
    )[:8]

    longest_turns = sorted(
        (
            {
                "thread_id": turn["thread_id"],
                "trace_id": turn["trace_id"],
                "user_id": turn["user_id"],
                "seconds": format_duration_seconds(turn["duration_seconds"]),
                "sort_seconds": round(turn["duration_seconds"], 2),
                "question": turn["question_preview"],
                "detail_link": conversation_detail_link(turn["thread_id"]),
            }
            for turn in turns
        ),
        key=lambda item: item["sort_seconds"],
        reverse=True,
    )[:8]

    average_experience = {}
    if experience_rows:
        for key in (
            "task_success_score",
            "efficiency_score",
            "clarity_score",
            "trust_score",
            "friction_score",
            "overall_experience_score",
        ):
            average_experience[key] = round(
                sum(row[key] for row in experience_rows) / len(experience_rows), 2
            )

    clarification_hits = []
    for thread_id, thread_turns in turns_by_thread.items():
        ordered_turns = sorted(thread_turns, key=lambda item: item.get("start_time") or datetime.min)
        first_turn = ordered_turns[0] if ordered_turns else {}
        if is_smalltalk_question(first_turn.get("question", "")):
            continue
        matched_turns = [turn for turn in ordered_turns[1:] if is_clarification_followup(turn.get("question", ""))]
        if not matched_turns:
            continue
        first_match = matched_turns[0]
        summary = summary_by_thread.get(thread_id, {})
        clarification_hits.append(
            {
                "thread_id": thread_id,
                "user_id": first_match.get("user_id") or first_turn.get("user_id") or "n/a",
                "customer": display_customer(first_turn.get("customer_id", "")) if first_turn.get("customer_id") else "n/a",
                "clarification_turns": len(matched_turns),
                "turn_count": summary.get("turn_count", len(ordered_turns)),
                "minutes": format_duration_minutes(summary.get("conversation_minutes", 0)),
                "sort_minutes": round(summary.get("conversation_minutes", 0), 2),
                "first_question": first_turn.get("question_preview", ""),
                "followup_question": first_match.get("question_preview", ""),
                "detail_link": conversation_detail_link(thread_id),
                "root_cause": classify_clarification_root_cause(
                    first_turn.get("question_preview", ""),
                    first_match.get("question_preview", ""),
                ),
            }
        )

    clarification_hits = sorted(
        clarification_hits,
        key=lambda item: (-item["clarification_turns"], -item["turn_count"], -item["sort_minutes"]),
    )
    top_users_by_clarification = Counter(item["user_id"] for item in clarification_hits).most_common(8)
    clarification_root_causes = Counter(item["root_cause"] for item in clarification_hits).most_common()
    clarification_user_drilldowns = []
    clarification_users = []
    for label, _ in top_users_by_clarification:
        if label not in clarification_users:
            clarification_users.append(label)
    for user_id in clarification_users:
        user_cases = [item for item in clarification_hits if item["user_id"] == user_id]
        clarification_user_drilldowns.append(
            {
                "user_id": user_id,
                "anchor": clarification_user_anchor(user_id),
                "conversation_count": len(user_cases),
                "clarification_turns": sum(item["clarification_turns"] for item in user_cases),
                "conversations": user_cases,
            }
        )

    clarification_count_by_thread = {item["thread_id"]: item["clarification_turns"] for item in clarification_hits}
    review_candidates = []
    for row in summary_rows:
        thread_id = row["thread_id"]
        thread_turns = sorted(turns_by_thread.get(thread_id, []), key=lambda item: item.get("start_time") or datetime.min)
        if not thread_turns:
            continue
        first_turn = thread_turns[0]
        signal_set = {signal for turn in thread_turns for signal in turn.get("signals", [])}
        clarification_turns = clarification_count_by_thread.get(thread_id, 0)
        score = score_current_export_conversation(thread_turns, clarification_turns)
        reasons = []
        if clarification_turns:
            reasons.append("clarification_followup")
        if "Tool or system failure" in signal_set:
            reasons.append("tool_failure")
        if "Access limitation" in signal_set:
            reasons.append("access_limitation")
        if "Evidence / raw data emphasis" in signal_set:
            reasons.append("evidence_needed")
        if "Payload / query transparency" in signal_set:
            reasons.append("query_or_payload_detail")
        if row["turn_count"] >= 8:
            reasons.append("long_multi_turn")
        review_candidates.append(
            {
                "thread_id": thread_id,
                "user_id": first_turn.get("user_id") or "n/a",
                "customer": display_customer(first_turn.get("customer_id", "")) if first_turn.get("customer_id") else "n/a",
                "score": score,
                "label": label_score(score),
                "evidence": 1 if "Evidence / raw data emphasis" in signal_set else 0,
                "turn_count": row["turn_count"],
                "reasons": ",".join(reasons) if reasons else "low_friction",
                "question": first_turn.get("question_preview", ""),
                "detail_link": conversation_detail_link(thread_id),
                "reason_note": (
                    f"{clarification_turns} clarification follow-up(s); signals: "
                    + (", ".join(sorted(signal_set)) if signal_set else "none")
                ),
            }
        )

    worst_cases = []
    worst_case_source_rows = sorted(
        review_candidates,
        key=lambda item: (item["score"], -item["turn_count"], -item["evidence"]),
    )[:20]
    for index, row in enumerate(worst_case_source_rows, start=1):
        worst_cases.append({**row, "rank": index})

    top_customer_experience = customer_experience_rows[:8]
    bottom_customer_experience = sorted(
        customer_experience_rows,
        key=lambda item: (item["avg_overall_experience_score"], -item["conversation_count"]),
    )[:8]
    sample_assessments = [
        {
            "thread_id": row["thread_id"],
            "assessment": row["label"].title(),
            "reason": row["reason_note"],
            "detail_link": row["detail_link"],
        }
        for row in worst_case_source_rows[:6]
    ]

    experience_labels = Counter(row["label"] for row in review_candidates)
    average_experience = {
        "task_success_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
        "efficiency_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
        "clarity_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
        "trust_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
        "friction_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
        "overall_experience_score": round(sum(row["score"] for row in review_candidates) / len(review_candidates), 2) if review_candidates else 0,
    }

    top_topic_labels = [item["label"] for item in [{"label": label, "value": value} for label, value in category_turn_counts.most_common(4)]]
    top_topic_text = ", ".join(top_topic_labels) if top_topic_labels else "n/a"
    top_cost_user_text = "n/a"
    if top_users_by_cost:
        top_cost_user_text = f"{top_users_by_cost[0][0]} (${top_users_by_cost[0][1]:.2f})"
    insights = [
        (
            f"Current usage is mixed rather than single-shot: {round(sum(1 for value in turn_counts if value == 1) * 100 / len(turn_counts), 1)}% "
            f"of conversations are one turn, with an average of {round(sum(turn_counts) / len(turn_counts), 2)} turns and a max of {max(turn_counts)}."
        ),
        f"The busiest topic clusters in this export are {top_topic_text}.",
        f"The highest estimated-cost user in this export is {top_cost_user_text}.",
        (
            f"Conversation depth is moderate in this dataset: {sum(1 for value in turn_counts if 2 <= value <= 3)} conversations have 2-3 turns, "
            f"and {sum(1 for value in turn_counts if value >= 4)} have 4+ turns."
        ),
    ]
    if clarification_hits:
        insights.append(
            f"{len(clarification_hits)} conversations show explicit clarification follow-ups after Nexa answered, which is a strong proxy that the previous answer did not fully land."
        )
    if clarification_root_causes:
        dominant_cause, dominant_count = clarification_root_causes[0]
        insights.append(
            f"The dominant clarification pattern is `{dominant_cause}` with {dominant_count} flagged conversations."
        )

    actions = []
    if clarification_root_causes:
        dominant_cause, dominant_count = clarification_root_causes[0]
        if dominant_cause == "Definition mismatch":
            actions.append(
                {
                    "title": "Front-load metric definition checks",
                    "body": (
                        f"{dominant_count} clarification cases are definition mismatches. Add a first-response template that confirms metric definition, "
                        "time range, and conversion boundary before analysis runs."
                    ),
                }
            )
        else:
            actions.append(
                {
                    "title": "Tighten first-answer framing",
                    "body": (
                        f"The top clarification root cause is {dominant_cause.lower()}. Update the first answer to restate scope, assumptions, and next step more explicitly."
                    ),
                }
            )
    if quality_signal_counts.get("Evidence / raw data emphasis", 0):
        actions.append(
            {
                "title": "Expose evidence earlier",
                "body": (
                    f"{quality_signal_counts['Evidence / raw data emphasis']} turns contain evidence/raw-data emphasis in the response text. "
                    "Treat this as a signal that proof artifacts matter in this workflow, and default to including the source query, raw event conditions, "
                    "or proof snippet in the first answer for high-stakes analyses."
                ),
            }
        )
    if top_users_by_cost:
        actions.append(
            {
                "title": "Review the highest-cost user journeys",
                "body": (
                    f"{top_users_by_cost[0][0]} is driving the most estimated cost at ${top_users_by_cost[0][1]:.2f}. Audit that user's longest threads first to identify avoidable rework or over-long runs."
                ),
            }
        )
    if sum(1 for value in turn_counts if value >= 4):
        actions.append(
            {
                "title": "Prioritize multi-turn friction threads",
                "body": (
                    f"There are {sum(1 for value in turn_counts if value >= 4)} conversations with 4+ turns. Use the review queue to inspect those threads and separate productive analysis sessions from avoidable back-and-forth."
                ),
            }
        )

    linked_top_users_by_cost = [
        {"label": label, "value": round(value, 4), "href": f"#{user_anchor(label)}"}
        for label, value in top_users_by_cost
    ]
    linked_top_users = [
        {"label": label, "value": value, "href": f"#{user_anchor(label)}"}
        for label, value in top_users
    ]

    user_drilldown_ids = []
    for label, _ in top_users_by_cost:
        if label not in user_drilldown_ids:
            user_drilldown_ids.append(label)
    for label, _ in top_users:
        if label not in user_drilldown_ids:
            user_drilldown_ids.append(label)

    user_drilldowns = []
    for user_id in user_drilldown_ids:
        thread_ids = sorted(
            {turn["thread_id"] for turn in turns if turn["user_id"] == user_id},
            key=lambda thread_id: summary_by_thread.get(thread_id, {}).get("last_start_time", datetime.min),
            reverse=True,
        )
        conversations = []
        total_cost = 0.0
        total_turns = 0
        for thread_id in thread_ids:
            summary = summary_by_thread.get(thread_id, {})
            thread_turns = turns_by_thread.get(thread_id, [])
            first_turn = thread_turns[0] if thread_turns else {}
            customer_label = display_customer(first_turn.get("customer_id", "")) if first_turn.get("customer_id") else "n/a"
            question = first_turn.get("question_preview", "")
            conversation_cost = round(cost_by_thread.get(thread_id, 0.0), 4)
            turn_count = summary.get("turn_count", len(thread_turns))
            total_cost += conversation_cost
            total_turns += turn_count
            conversations.append(
                {
                    "thread_id": thread_id,
                    "customer": customer_label,
                    "turn_count": turn_count,
                    "minutes": format_duration_minutes(summary.get("conversation_minutes", 0)),
                    "estimated_cost": conversation_cost,
                    "question": question,
                    "detail_link": conversation_detail_link(thread_id),
                }
            )
        user_drilldowns.append(
            {
                "user_id": user_id,
                "anchor": user_anchor(user_id),
                "conversation_count": len(conversations),
                "turn_count": total_turns,
                "estimated_cost": round(total_cost, 4),
                "conversations": conversations,
            }
        )

    detail_rows_by_thread = {row["thread_id"]: dict(row) for row in conversation_rows}
    conversation_lookup_all = load_conversation_lookup(list(detail_rows_by_thread))
    write_conversation_detail_pages(list(detail_rows_by_thread.values()), conversation_lookup_all)

    raw_conversations = []
    clarification_by_thread = {row["thread_id"]: row for row in clarification_hits}
    review_by_thread = {row["thread_id"]: row for row in review_candidates}
    categories_by_thread = {
        thread_id: sorted(set(category for turn in thread_turns for category in turn["categories"]))
        for thread_id, thread_turns in turns_by_thread.items()
    }
    signals_by_thread = {
        thread_id: sorted(set(signal for turn in thread_turns for signal in turn["signals"]))
        for thread_id, thread_turns in turns_by_thread.items()
    }
    for row in conversation_rows:
        thread_id = row["thread_id"]
        review = review_by_thread.get(thread_id, {})
        clarification = clarification_by_thread.get(thread_id, {})
        raw_conversations.append(
            {
                "thread_id": thread_id,
                "user_id": row["user_id"],
                "customer": row["customer"],
                "turn_count": row["turn_count"],
                "minutes_num": row["sort_minutes"],
                "minutes": row["minutes"],
                "question": row["question"],
                "detail_link": row["detail_link"],
                "first_time": summary_by_thread.get(thread_id, {}).get("first_start_time", datetime.min).isoformat() if summary_by_thread.get(thread_id) else "",
                "cost": round(cost_by_thread.get(thread_id, 0.0), 4),
                "score": review.get("score", 5.0),
                "label": review.get("label", "good"),
                "evidence": review.get("evidence", 0),
                "reasons": review.get("reasons", ""),
                "reason_note": review.get("reason_note", ""),
                "clarification_turns": clarification.get("clarification_turns", 0),
                "followup_question": clarification.get("followup_question", ""),
                "root_cause": clarification.get("root_cause", ""),
                "categories": categories_by_thread.get(thread_id, []),
                "signals": signals_by_thread.get(thread_id, []),
            }
        )

    raw_turns = []
    for turn in turns:
        raw_turns.append(
            {
                "thread_id": turn["thread_id"],
                "trace_id": turn["trace_id"],
                "user_id": turn["user_id"],
                "customer": display_customer(turn["customer_id"]) if turn["customer_id"] else "n/a",
                "start_time": turn["start_time"].isoformat() if turn["start_time"] else "",
                "duration_seconds": round(turn["duration_seconds"], 2),
                "question": turn["question_preview"],
                "categories": turn["categories"],
                "signals": turn["signals"],
                "detail_link": conversation_detail_link(turn["thread_id"]),
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            "opik_base_url": str(opik_config.get("OPIK_BASE_URL", "") or ""),
            "opik_project": str(opik_config.get("OPIK_PROJECT", "") or ""),
            "target_env": str(opik_config.get("TARGET_ENV", "") or ""),
            "opik_workspace": str(opik_config.get("OPIK_WORKSPACE", "") or ""),
            "opik_url_override": str(opik_config.get("OPIK_URL_OVERRIDE", "") or ""),
        },
        "overview": {
            "conversations": len(summary_rows),
            "turns": len(turns),
            "single_turn_share": round(sum(1 for value in turn_counts if value == 1) * 100 / len(turn_counts), 1),
            "avg_turns": round(sum(turn_counts) / len(turn_counts), 2),
            "median_turns": sorted(turn_counts)[len(turn_counts) // 2],
            "max_turns": max(turn_counts),
            "avg_turn_duration_seconds": format_duration_seconds(sum(turn_durations) / len(turn_durations)),
            "p90_turn_duration_seconds": format_duration_seconds(percentile(turn_durations, 0.9)),
        },
        "distribution": {
            "turn_buckets": [
                {"label": "1 turn", "value": sum(1 for value in turn_counts if value == 1)},
                {"label": "2-3 turns", "value": sum(1 for value in turn_counts if 2 <= value <= 3)},
                {"label": "4-9 turns", "value": sum(1 for value in turn_counts if 4 <= value <= 9)},
                {"label": "10+ turns", "value": sum(1 for value in turn_counts if value >= 10)},
            ],
            "conversations_by_day": [
                {"label": label, "value": value}
                for label, value in sorted(conversations_by_day.items())
            ],
            "conversation_minutes": {
                "median": format_duration_minutes(percentile(conversation_minutes, 0.5)),
                "p90": format_duration_minutes(percentile(conversation_minutes, 0.9)),
                "max": format_duration_minutes(max(conversation_minutes)),
            },
        },
        "topics": {
            "by_turns": [{"label": label, "value": value} for label, value in category_turn_counts.most_common()],
            "by_conversations": [{"label": label, "value": value} for label, value in category_conversation_counts.most_common()],
        },
        "actors": {
            "top_users_by_cost": linked_top_users_by_cost,
            "top_users": linked_top_users,
            "top_customers": [{"label": display_customer(label), "value": value} for label, value in top_customers],
            "top_customer_experience": [
                {"label": row["customer_name"], "value": row["avg_overall_experience_score"]}
                for row in top_customer_experience
            ],
            "bottom_customer_experience": [
                {"label": row["customer_name"], "value": row["avg_overall_experience_score"]}
                for row in bottom_customer_experience
            ],
        },
        "quality": {
            "signal_counts": [{"label": label, "value": value} for label, value in quality_signal_counts.most_common()],
            "sample_assessments": sample_assessments,
            "experience_labels": [{"label": label.title(), "value": value} for label, value in experience_labels.most_common()],
            "experience_averages": [
                {"label": "Task Success", "value": average_experience.get("task_success_score", 0)},
                {"label": "Efficiency", "value": average_experience.get("efficiency_score", 0)},
                {"label": "Clarity", "value": average_experience.get("clarity_score", 0)},
                {"label": "Trust", "value": average_experience.get("trust_score", 0)},
                {"label": "Friction", "value": average_experience.get("friction_score", 0)},
                {"label": "Overall", "value": average_experience.get("overall_experience_score", 0)},
            ],
            "clarification_summary": [
                {"label": "Flagged conversations", "value": len(clarification_hits)},
                {
                    "label": "Share of all conversations",
                    "value": f"{round((len(clarification_hits) * 100 / len(summary_rows)) if summary_rows else 0, 1)}%",
                },
                {
                    "label": "Clarification turns",
                    "value": sum(item["clarification_turns"] for item in clarification_hits),
                },
                {
                    "label": "Users involved",
                    "value": len({item["user_id"] for item in clarification_hits}),
                },
            ],
            "top_users_by_clarification": [
                {"label": label, "value": value, "href": f"#{clarification_user_anchor(label)}"}
                for label, value in top_users_by_clarification
            ],
            "clarification_root_causes": [
                {"label": label, "value": value}
                for label, value in clarification_root_causes
            ],
        },
        "tables": {
            "longest_conversations": longest_conversations,
            "longest_turns": longest_turns,
            "worst_cases": worst_cases,
            "customer_experience": top_customer_experience,
            "customer_experience_bottom": bottom_customer_experience,
            "sample_questions": [
                {
                    "thread_id": turn["thread_id"],
                    "user_id": turn["user_id"],
                    "customer": display_customer(turn["customer_id"]) if turn["customer_id"] else "n/a",
                    "question": turn["question_preview"],
                    "categories": ", ".join(turn["categories"]),
                    "detail_link": conversation_detail_link(turn["thread_id"]),
                }
                for turn in turns[:12]
            ],
            "clarification_cases": clarification_hits[:20],
            "clarification_root_cause_cases": clarification_hits[:20],
            "clarification_user_drilldowns": clarification_user_drilldowns,
            "user_drilldowns": user_drilldowns,
        },
        "insights": insights,
        "actions": actions,
        "raw": {
            "conversations": raw_conversations,
            "turns": raw_turns,
        },
    }


def render_html(data, current_env, available_envs):
    data_json = json.dumps(data, ensure_ascii=False)
    env_switch = "".join(
        f'<a class="env-link{" active" if env_key == current_env else ""}" href="{filename}">{env_key.upper()}</a>'
        for env_key, filename in available_envs
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Conversation Analysis Dashboard</title>
  <style>
    :root {{
      --background: #fcfcfd;
      --background-soft: #f7f7f7;
      --panel: #ffffff;
      --panel-soft: #ffffff;
      --muted-panel: #f8f8f8;
      --ink: #222629;
      --muted: #707372;
      --muted-2: #9b9d9c;
      --accent: #5a9b00;
      --accent-strong: #4a8500;
      --accent-soft: #e8f5d0;
      --accent-soft-hover: #f0f7e6;
      --accent-alt: #583160;
      --accent-alt-soft: #efeaf2;
      --line: rgba(226, 227, 227, 1);
      --line-soft: rgba(241, 241, 241, 1);
      --shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--background);
      min-height: 100vh;
    }}
    a {{
      color: inherit;
      text-decoration: none;
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 40;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
    }}
    .topbar-inner {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 14px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }}
    .brand-badge {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 8px;
      background: var(--accent);
      color: white;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
    }}
    .brand-copy {{
      min-width: 0;
    }}
    .brand-copy strong {{
      display: block;
      font-size: 14px;
      line-height: 1.2;
    }}
    .brand-copy span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
      margin-top: 2px;
    }}
    .env-switch {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-left: auto;
      margin-right: 12px;
      flex-wrap: wrap;
    }}
    .env-link {{
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--muted-panel);
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.06em;
    }}
    .env-link.active {{
      border-color: var(--accent);
      color: var(--accent-strong);
      background: var(--accent-soft);
    }}
    .shell {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px 20px 60px;
    }}
    .app-shell {{
      min-width: 0;
    }}
    .hero {{
      padding: 22px 24px;
      border: 1px solid var(--line);
      background: #ffffff;
      border-radius: 12px;
    }}
    .hero-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }}
    .hero-copy {{
      min-width: 0;
      flex: 1;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid #d9cfe0;
      background: var(--accent-alt-soft);
      color: var(--accent-alt);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 14px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 3.2vw, 36px);
      line-height: 1.08;
      max-width: 900px;
      letter-spacing: -0.025em;
    }}
    .hero p {{
      max-width: 840px;
      font-size: 15px;
      line-height: 1.5;
      color: var(--muted);
      margin: 10px 0 0;
    }}
    .hero-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--muted-panel);
      color: #424548;
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
    }}
    .badge-success {{
      border-color: #c8e6a0;
      background: var(--accent-soft);
      color: var(--accent-strong);
    }}
    .badge-info {{
      border-color: #d9cfe0;
      background: var(--accent-alt-soft);
      color: var(--accent-alt);
    }}
    .section-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line-soft);
    }}
    .section-tabs a {{
      display: inline-flex;
      align-items: center;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--muted-panel);
      color: #424548;
      font-size: 13px;
      font-weight: 500;
      transition: border-color 120ms ease, color 120ms ease, background-color 120ms ease;
      min-width: 0;
      white-space: nowrap;
    }}
    .section-tabs a:hover {{
      border-color: var(--accent);
      color: var(--accent-strong);
      background: #fff;
    }}
    .config-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .filter-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid var(--line-soft);
    }}
    .filter-group {{
      display: grid;
      gap: 6px;
      min-width: 180px;
    }}
    .filter-group label {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted-2);
    }}
    .filter-group input {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      color: var(--ink);
      background: #fff;
    }}
    .filter-actions {{
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .filter-actions button {{
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--muted-panel);
      color: var(--ink);
      padding: 10px 14px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }}
    .filter-actions button:hover {{
      border-color: var(--accent);
      color: var(--accent-strong);
    }}
    .filter-summary {{
      font-size: 13px;
      color: var(--muted);
    }}
    .config-card {{
      padding: 14px 16px;
      border-radius: 8px;
      background: var(--muted-panel);
      border: 1px solid var(--line);
    }}
    .config-card .label {{
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted-2);
      margin-bottom: 8px;
    }}
    .config-card .value {{
      font-size: 14px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
      margin-top: 16px;
    }}
    .panel {{
      grid-column: span 12;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px;
      min-width: 0;
      overflow: hidden;
    }}
    .panel-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
      min-width: 0;
    }}
    .panel-head > div {{
      min-width: 0;
    }}
    .panel h2 {{
      margin: 0;
      font-size: 19px;
      letter-spacing: -0.015em;
      overflow-wrap: normal;
    }}
    .panel p.section-note {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: normal;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }}
    .stat {{
      padding: 16px;
      border-radius: 8px;
      background: var(--muted-panel);
      border: 1px solid var(--line);
    }}
    .stat .label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted-2);
      margin-bottom: 8px;
    }}
    .stat .value {{
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1;
    }}
    .bar-list {{
      display: grid;
      gap: 12px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr) 56px;
      gap: 12px;
      align-items: center;
      font-size: 14px;
    }}
    .bar-row > div:first-child {{
      min-width: 0;
      overflow-wrap: normal;
      word-break: keep-all;
    }}
    .bar-track {{
      height: 14px;
      border-radius: 999px;
      background: #f1f1f1;
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      border-radius: inherit;
      background: #5a9b00;
    }}
    .bar-link {{
      color: var(--ink);
      text-decoration: none;
      border-bottom: 1px dotted rgba(112, 115, 114, 0.45);
      display: inline;
      overflow-wrap: normal;
      word-break: keep-all;
    }}
    .bar-link:hover {{
      color: var(--accent-strong);
      border-bottom-color: var(--accent-strong);
    }}
    .two-col {{ grid-column: span 6; }}
    .three-col {{ grid-column: span 4; }}
    .three-col .stats {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .three-col .stat {{
      padding: 14px;
    }}
    .three-col .stat .value {{
      font-size: clamp(20px, 2.4vw, 28px);
    }}
    .three-col .bar-row {{
      grid-template-columns: minmax(0, 1fr) max-content;
      grid-template-areas:
        "label value"
        "track track";
      gap: 8px 10px;
      align-items: center;
    }}
    .three-col .bar-row > div:first-child {{
      grid-area: label;
    }}
    .three-col .bar-row > div:nth-child(2) {{
      grid-area: track;
    }}
    .three-col .bar-row > div:nth-child(3) {{
      grid-area: value;
      white-space: nowrap;
    }}
    .three-col .bar-track {{
      width: 100%;
      margin-top: 0;
    }}
    .insight-list {{
      display: grid;
      gap: 12px;
    }}
    .insight {{
      padding: 14px 16px;
      border-left: 4px solid var(--accent);
      background: var(--muted-panel);
      border-radius: 8px;
    }}
    .action-list {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }}
    .action-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 2px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--accent-strong);
      font-weight: 700;
    }}
    .action-card {{
      padding: 14px 16px;
      border: 1px solid #cfe3ad;
      background: #f7fbeeff;
      border-radius: 10px;
    }}
    .action-card strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 14px;
    }}
    .action-card p {{
      margin: 0;
      font-size: 14px;
      color: var(--muted);
      line-height: 1.55;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      display: block;
      overflow-x: auto;
    }}
    thead {{
      background: var(--muted-panel);
    }}
    th, td {{
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      min-width: 0;
      overflow-wrap: normal;
      word-break: normal;
    }}
    th {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted-2);
    }}
    tbody tr:hover {{
      background: #fafafa;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 500;
    }}
    .pill-good {{
      border: 1px solid #c8e6a0;
      background: var(--accent-soft);
      color: var(--accent-strong);
    }}
    .pill-mixed {{
      border: 1px solid #d9cfe0;
      background: var(--accent-alt-soft);
      color: var(--accent-alt);
    }}
    .pill-poor {{
      border: 1px solid #f5c6dc;
      background: #fce8f2;
      color: #942366;
    }}
    .footer-note {{
      margin-top: 20px;
      color: var(--muted);
      font-size: 13px;
    }}
    .user-drilldown {{
      margin-top: 18px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }}
    .user-drilldown:first-child {{
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }}
    .user-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin-bottom: 10px;
    }}
    .user-head h3 {{
      margin: 0;
      font-size: 20px;
      word-break: keep-all;
      overflow-wrap: normal;
    }}
    .user-meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 10px;
      overflow-wrap: normal;
    }}
    td a, th a {{
      overflow-wrap: normal;
      word-break: normal;
      white-space: nowrap;
    }}
    td:last-child, td:nth-last-child(2) {{
      max-width: 420px;
      white-space: normal;
      overflow-wrap: break-word;
    }}
    .compact-cell {{
      min-width: 0;
    }}
    .compact-meta {{
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 13px;
    }}
    .compact-meta strong {{
      color: var(--ink);
      font-size: 14px;
      font-weight: 600;
    }}
    .line-clamp-2 {{
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .line-clamp-3 {{
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    @media (max-width: 960px) {{
      .two-col, .three-col {{ grid-column: span 12; }}
      .config-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .bar-row {{ grid-template-columns: 120px minmax(0, 1fr) 48px; }}
    }}
    @media (max-width: 640px) {{
      .shell {{ padding: 18px 14px 40px; }}
      .hero {{ padding: 22px; border-radius: 12px; }}
      .panel {{ padding: 18px; border-radius: 12px; }}
      .hero-head {{ flex-direction: column; }}
      .hero-meta {{ justify-content: flex-start; }}
      .config-grid {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 1fr; }}
      table {{
        font-size: 13px;
      }}
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <div class="brand-badge">DPI</div>
        <div class="brand-copy">
          <strong>Conversation Analysis Dashboard</strong>
        </div>
      </div>
      <div class="env-switch">{env_switch}</div>
      <div class="brand-copy" style="text-align:right;">
        <strong style="font-size:13px;">Generated from Opik export</strong>
        <span>Env: <span id="top-project"></span></span>
      </div>
    </div>
  </div>
  <div class="shell">
    <main class="app-shell">
    <section class="hero" id="top">
      <div class="hero-head">
        <div class="hero-copy">
          <div class="eyebrow">Analysis Overview</div>
          <h1>Nexa Conversation Analysis</h1>
          <p>This page summarizes usage structure, dominant topics, quality signals, and representative cases from the exported conversation logs. Generated at <span id="generated-at"></span>.</p>
        </div>
        <div class="hero-meta">
          <span class="badge badge-success">Source: Opik export</span>
          <span class="badge badge-info">Env: <span id="top-project"></span></span>
        </div>
      </div>
      <div class="config-grid" id="config-grid"></div>
      <div class="filter-bar">
        <div class="filter-group">
          <label for="date-start">Start Date</label>
          <input id="date-start" type="date" />
        </div>
        <div class="filter-group">
          <label for="date-end">End Date</label>
          <input id="date-end" type="date" />
        </div>
        <div class="filter-actions">
          <button id="apply-filter" type="button">Apply Range</button>
          <button id="reset-filter" type="button">Reset</button>
          <span class="filter-summary" id="filter-summary"></span>
        </div>
      </div>
      <nav class="section-tabs">
        <a href="#overview-section">Overview</a>
        <a href="#interpretation-section">Interpretation</a>
        <a href="#worst-cases-section">Review Queue</a>
        <a href="#clarification-section">Clarification</a>
        <a href="#usage-section">Usage</a>
        <a href="#topics-section">Topics</a>
        <a href="#users-cost-section">Users by Cost</a>
        <a href="#customers-section">Customers</a>
        <a href="#quality-section">Quality</a>
        <a href="#tables-section">Reference</a>
        <a href="#users-drilldown-section">User Drilldown</a>
      </nav>
    </section>

    <div class="grid">
      <section class="panel" id="overview-section">
        <div class="panel-head">
          <div>
            <h2>Overview</h2>
            <p class="section-note">The strongest signal is structural: most usage is one-shot rather than long-form collaboration.</p>
          </div>
        </div>
        <div class="stats" id="overview-stats"></div>
      </section>

      <section class="panel" id="interpretation-section">
        <div class="panel-head"><div><h2>Interpretation</h2></div></div>
        <div class="insight-list" id="insights"></div>
        <div class="action-list">
          <div class="action-header">Recommended Actions</div>
          <div id="actions"></div>
        </div>
      </section>

      <section class="panel two-col">
        <div class="panel-head">
          <div>
            <h2>Conversations by Day</h2>
            <p class="section-note">Conversation volume grouped by the day each thread first appeared in the export.</p>
          </div>
        </div>
        <div class="bar-list" id="conversations-by-day"></div>
      </section>

      <section class="panel two-col" id="usage-section">
        <div class="panel-head">
          <div>
            <h2>Conversation Shape</h2>
            <p class="section-note">Turn-count buckets show how concentrated usage is around single-turn conversations.</p>
          </div>
        </div>
        <div class="bar-list" id="turn-buckets"></div>
      </section>

      <section class="panel" id="worst-cases-section">
        <div class="panel-head">
          <div>
            <h2>Top 20 Review Candidates</h2>
            <p class="section-note">Current-export conversations ranked by heuristic friction score, then by turn count and evidence emphasis.</p>
          </div>
        </div>
        <table>
          <thead><tr><th>Rank</th><th>Thread</th><th>Context</th><th>Score</th><th>Signals</th><th>Question</th></tr></thead>
          <tbody id="worst-cases"></tbody>
        </table>
      </section>

      <section class="panel three-col" id="clarification-section">
        <div class="panel-head">
          <div>
            <h2>Clarification Follow-ups</h2>
            <p class="section-note">Inferred from explicit follow-up wording such as `not just`, `to be clear`, or requests for exact/raw conditions. Failure-retry prompts are excluded.</p>
          </div>
        </div>
        <div class="stats" id="clarification-summary"></div>
      </section>

      <section class="panel three-col">
        <div class="panel-head">
          <div>
            <h2>Clarification Root Causes</h2>
            <p class="section-note">Heuristic buckets for why the first answer likely did not land.</p>
          </div>
        </div>
        <div class="bar-list" id="clarification-root-causes"></div>
      </section>

      <section class="panel three-col">
        <div class="panel-head">
          <div>
            <h2>Top Users Asking for Clarification</h2>
            <p class="section-note">Users ranked by how many conversations required follow-up clarification.</p>
          </div>
        </div>
        <div class="bar-list" id="top-users-by-clarification"></div>
      </section>

      <section class="panel" id="clarification-cases-section">
        <div class="panel-head">
          <div>
            <h2>Conversations With Clarification Follow-up</h2>
            <p class="section-note">Highest-signal cases where the user had to clarify or restate after Nexa responded.</p>
          </div>
        </div>
        <table>
          <thead><tr><th>Thread</th><th>User</th><th>Customer</th><th>Root Cause</th><th>Clarification Turns</th><th>Total Turns</th><th>First Question</th><th>Clarifying Follow-up</th></tr></thead>
          <tbody id="clarification-cases"></tbody>
        </table>
      </section>

      <section class="panel" id="clarification-user-drilldown-section">
        <div class="panel-head">
          <div>
            <h2>Clarification User Drilldown</h2>
            <p class="section-note">Linked from `Top Users Asking for Clarification`. Each section lists only the conversations where that user had to clarify or restate.</p>
          </div>
        </div>
        <div id="clarification-user-drilldowns"></div>
      </section>

      <section class="panel two-col" id="topics-section">
        <div class="panel-head">
          <div>
            <h2>Topic Mix</h2>
            <p class="section-note">Conversation-level topic tags derived from user questions.</p>
          </div>
        </div>
        <div class="bar-list" id="topic-bars"></div>
      </section>

      <section class="panel three-col" id="users-cost-section">
        <div class="panel-head">
          <div>
            <h2>Top Users by Estimated Cost</h2>
            <p class="section-note">Summed `total_estimated_cost` per user, shown in USD.</p>
          </div>
        </div>
        <div class="bar-list" id="top-users-by-cost"></div>
      </section>

      <section class="panel three-col" id="users-section">
        <div class="panel-head"><div><h2>Top Users</h2></div></div>
        <div class="bar-list" id="top-users"></div>
      </section>

      <section class="panel three-col" id="customers-section">
        <div class="panel-head"><div><h2>Top Customers</h2></div></div>
        <div class="bar-list" id="top-customers"></div>
      </section>

      <section class="panel two-col" id="quality-section">
        <div class="panel-head">
          <div>
            <h2>Quality Signals</h2>
            <p class="section-note">These are heuristic counts from answer text, so treat them as directional rather than exact labels.</p>
          </div>
        </div>
        <div class="bar-list" id="quality-signals"></div>
      </section>

      <section class="panel two-col" id="experience-section">
        <div class="panel-head">
          <div>
            <h2>Experience Labels</h2>
            <p class="section-note">Conversation-level experience scoring generated from the new `conversation_experience.csv` file.</p>
          </div>
        </div>
        <div class="bar-list" id="experience-labels"></div>
      </section>

      <section class="panel two-col">
        <div class="panel-head"><div><h2>Average Experience Scores</h2></div></div>
        <div class="bar-list" id="experience-averages"></div>
      </section>

      <section class="panel two-col" id="tables-section">
        <div class="panel-head"><div><h2>Longest Conversations</h2></div></div>
        <table>
          <thead><tr><th>Thread</th><th>Turns</th><th>Duration</th></tr></thead>
          <tbody id="longest-conversations"></tbody>
        </table>
      </section>

      <section class="panel two-col">
        <div class="panel-head"><div><h2>Longest Turns</h2></div></div>
        <table>
          <thead><tr><th>Thread</th><th>User</th><th>Duration</th><th>Question</th></tr></thead>
          <tbody id="longest-turns"></tbody>
        </table>
      </section>

      <section class="panel" id="sample-review-section">
        <div class="panel-head">
          <div>
            <h2>Sample Review Candidates</h2>
            <p class="section-note">Current-export examples ranked by heuristic friction signals, with every thread linked to its full conversation.</p>
          </div>
        </div>
        <table>
          <thead><tr><th>Thread</th><th>Assessment</th><th>Reason</th></tr></thead>
          <tbody id="sample-assessments"></tbody>
        </table>
      </section>

      <section class="panel">
        <div class="panel-head"><div><h2>Sample Questions</h2></div></div>
        <table>
          <thead><tr><th>Thread</th><th>User</th><th>Customer</th><th>Categories</th><th>Question</th></tr></thead>
          <tbody id="sample-questions"></tbody>
        </table>
        <div class="footer-note">Source files: conversations exported in the same folder as this dashboard.</div>
      </section>

      <section class="panel" id="users-drilldown-section">
        <div class="panel-head">
          <div>
            <h2>User Conversation Drilldown</h2>
            <p class="section-note">Click a user above to jump here and inspect all conversations for that user.</p>
          </div>
        </div>
        <div id="user-drilldowns"></div>
      </section>

    </div>
    </main>
  </div>

  <script>
    const data = {data_json};
    const fullData = data;
    let filteredData = data;

    document.getElementById("generated-at").textContent = fullData.generated_at;
    document.querySelectorAll("#top-project").forEach(node => {{
      node.textContent = fullData.config.opik_project || "n/a";
    }});

    const pillClass = value => {{
      const normalized = String(value || "").toLowerCase();
      if (normalized === "good") return "pill pill-good";
      if (normalized === "mixed") return "pill pill-mixed";
      if (normalized === "poor") return "pill pill-poor";
      return "pill";
    }};

    const formatDurationMinutes = minutes => {{
      const total = Math.max(0, Math.round(Number(minutes || 0)));
      const hours = Math.floor(total / 60);
      const mins = total % 60;
      if (hours && mins) return `${{hours}}h ${{mins}}m`;
      if (hours) return `${{hours}}h`;
      return `${{mins}}m`;
    }};

    const formatDurationSeconds = seconds => {{
      const total = Math.max(0, Math.round(Number(seconds || 0)));
      const minutes = Math.floor(total / 60);
      const secs = total % 60;
      const hours = Math.floor(minutes / 60);
      const mins = minutes % 60;
      if (hours && mins) return `${{hours}}h ${{mins}}m`;
      if (hours) return `${{hours}}h`;
      if (minutes && secs) return `${{minutes}}m ${{secs}}s`;
      if (minutes) return `${{minutes}}m`;
      return `${{secs}}s`;
    }};

    const percentile = (values, ratio) => {{
      if (!values.length) return 0;
      const ordered = [...values].sort((a, b) => a - b);
      const index = Math.max(0, Math.min(ordered.length - 1, Math.floor(ordered.length * ratio) - 1));
      return ordered[index];
    }};

    const sum = values => values.reduce((acc, value) => acc + value, 0);
    const byValueDesc = (a, b) => b.value - a.value;

    const buildInsights = (state) => {{
      const turnBuckets = state.distribution.turn_buckets;
      const topTopicLabels = state.topics.byTurns.slice(0, 4).map(item => item.label).join(", ") || "n/a";
      const topCost = state.actors.topUsersByCost[0];
      const insights = [
        `Current usage is mixed rather than single-shot: ${{state.overview.singleTurnShare}}% of conversations are one turn, with an average of ${{state.overview.avgTurns}} turns and a max of ${{state.overview.maxTurns}}.`,
        `The busiest topic clusters in this export are ${{topTopicLabels}}.`,
        topCost ? `The highest estimated-cost user in this export is ${{topCost.label}} ($${{Number(topCost.value).toFixed(2)}}).` : "No estimated-cost data is available in the selected range.",
        `Conversation depth is moderate in this dataset: ${{turnBuckets.find(item => item.label === "2-3 turns")?.value || 0}} conversations have 2-3 turns, and ${{(turnBuckets.find(item => item.label === "4-9 turns")?.value || 0) + (turnBuckets.find(item => item.label === "10+ turns")?.value || 0)}} have 4+ turns.`,
      ];
      if (state.clarification.cases.length) {{
        insights.push(`${{state.clarification.cases.length}} conversations show explicit clarification follow-ups after Nexa answered, which is a strong proxy that the previous answer did not fully land.`);
      }}
      if (state.clarification.rootCauses.length) {{
        const top = state.clarification.rootCauses[0];
        insights.push(`The dominant clarification pattern is \`${{top.label}}\` with ${{top.value}} flagged conversations.`);
      }}
      return insights;
    }};

    const buildActions = (state) => {{
      const actions = [];
      if (state.clarification.rootCauses.length) {{
        const top = state.clarification.rootCauses[0];
        if (top.label === "Definition mismatch") {{
          actions.push({{
            title: "Front-load metric definition checks",
            body: `${{top.value}} clarification cases are definition mismatches. Add a first-response template that confirms metric definition, time range, and conversion boundary before analysis runs.`,
          }});
        }} else {{
          actions.push({{
            title: "Tighten first-answer framing",
            body: `The top clarification root cause is ${{top.label.toLowerCase()}}. Update the first answer to restate scope, assumptions, and next step more explicitly.`,
          }});
        }}
      }}
      const evidenceSignal = state.quality.signalCounts.find(item => item.label === "Evidence / raw data emphasis");
      if (evidenceSignal) {{
        actions.push({{
          title: "Expose evidence earlier",
          body: `${{evidenceSignal.value}} turns contain evidence/raw-data emphasis in the response text. Treat this as a signal that proof artifacts matter in this workflow, and default to including the source query, raw event conditions, or proof snippet in the first answer for high-stakes analyses.`,
        }});
      }}
      const topCost = state.actors.topUsersByCost[0];
      if (topCost) {{
        actions.push({{
          title: "Review the highest-cost user journeys",
          body: `${{topCost.label}} is driving the most estimated cost at $${{Number(topCost.value).toFixed(2)}}. Audit that user's longest threads first to identify avoidable rework or over-long runs.`,
        }});
      }}
      const multiTurn = state.distribution.turn_buckets.find(item => item.label === "4-9 turns")?.value || 0;
      const longTurn = state.distribution.turn_buckets.find(item => item.label === "10+ turns")?.value || 0;
      if (multiTurn + longTurn > 0) {{
        actions.push({{
          title: "Prioritize multi-turn friction threads",
          body: `There are ${{multiTurn + longTurn}} conversations with 4+ turns in the selected range. Use the review queue to inspect those threads and separate productive analysis sessions from avoidable back-and-forth.`,
        }});
      }}
      return actions;
    }};

    const computeState = (sourceData, startDate, endDate) => {{
      const inRange = conv => {{
        const day = (conv.first_time || "").slice(0, 10);
        if (!day) return false;
        if (startDate && day < startDate) return false;
        if (endDate && day > endDate) return false;
        return true;
      }};

      const conversations = sourceData.raw.conversations.filter(inRange);
      const allowedThreads = new Set(conversations.map(conv => conv.thread_id));
      const turns = sourceData.raw.turns.filter(turn => allowedThreads.has(turn.thread_id));

      const turnCounts = conversations.map(conv => conv.turn_count);
      const turnDurations = turns.map(turn => Number(turn.duration_seconds || 0));
      const conversationMinutes = conversations.map(conv => Number(conv.minutes_num || 0));
      const safeCount = conversations.length || 1;

      const topicTurnCounter = new Map();
      const topicConversationCounter = new Map();
      const signalCounter = new Map();
      for (const turn of turns) {{
        for (const category of turn.categories || []) {{
          topicTurnCounter.set(category, (topicTurnCounter.get(category) || 0) + 1);
        }}
        for (const signal of turn.signals || []) {{
          signalCounter.set(signal, (signalCounter.get(signal) || 0) + 1);
        }}
      }}
      for (const conv of conversations) {{
        for (const category of conv.categories || []) {{
          topicConversationCounter.set(category, (topicConversationCounter.get(category) || 0) + 1);
        }}
      }}

      const toItems = counter => [...counter.entries()].map(([label, value]) => ({{ label, value }})).sort(byValueDesc);

      const byDayCounter = new Map();
      for (const conv of conversations) {{
        const day = (conv.first_time || "").slice(0, 10);
        byDayCounter.set(day, (byDayCounter.get(day) || 0) + 1);
      }}

      const userTurnCounter = new Map();
      const userCostCounter = new Map();
      const customerTurnCounter = new Map();
      for (const conv of conversations) {{
        if (conv.user_id && conv.user_id !== "n/a") {{
          userTurnCounter.set(conv.user_id, (userTurnCounter.get(conv.user_id) || 0) + conv.turn_count);
          userCostCounter.set(conv.user_id, (userCostCounter.get(conv.user_id) || 0) + Number(conv.cost || 0));
        }}
        if (conv.customer && conv.customer !== "n/a") {{
          customerTurnCounter.set(conv.customer, (customerTurnCounter.get(conv.customer) || 0) + conv.turn_count);
        }}
      }}

      const clarificationCases = conversations
        .filter(conv => Number(conv.clarification_turns || 0) > 0)
        .sort((a, b) => (b.clarification_turns - a.clarification_turns) || (b.turn_count - a.turn_count) || (b.minutes_num - a.minutes_num))
        .slice(0, 20)
        .map(conv => ({{
          thread_id: conv.thread_id,
          user_id: conv.user_id,
          customer: conv.customer,
          root_cause: conv.root_cause,
          clarification_turns: conv.clarification_turns,
          turn_count: conv.turn_count,
          first_question: conv.question,
          followup_question: conv.followup_question,
          detail_link: conv.detail_link,
        }}));

      const clarificationRootCounter = new Map();
      const clarificationUserCounter = new Map();
      for (const conv of conversations) {{
        if (conv.clarification_turns > 0) {{
          clarificationRootCounter.set(conv.root_cause, (clarificationRootCounter.get(conv.root_cause) || 0) + 1);
          clarificationUserCounter.set(conv.user_id, (clarificationUserCounter.get(conv.user_id) || 0) + 1);
        }}
      }}

      const userDrilldowns = [...userTurnCounter.entries()]
        .sort((a, b) => (userCostCounter.get(b[0]) || 0) - (userCostCounter.get(a[0]) || 0))
        .slice(0, 8)
        .map(([userId]) => {{
          const userConversations = conversations.filter(conv => conv.user_id === userId)
            .sort((a, b) => (b.first_time || "").localeCompare(a.first_time || ""));
          return {{
            user_id: userId,
            anchor: `user-${{userId.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "unknown-user"}}`,
            conversation_count: userConversations.length,
            turn_count: userConversations.reduce((acc, conv) => acc + conv.turn_count, 0),
            estimated_cost: userConversations.reduce((acc, conv) => acc + Number(conv.cost || 0), 0),
            conversations: userConversations.map(conv => ({{
              thread_id: conv.thread_id,
              customer: conv.customer,
              turn_count: conv.turn_count,
              minutes: conv.minutes,
              estimated_cost: conv.cost,
              question: conv.question,
              detail_link: conv.detail_link,
            }})),
          }};
        }});

      const clarificationUserDrilldowns = [...clarificationUserCounter.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([userId]) => {{
          const userConversations = conversations.filter(conv => conv.user_id === userId && conv.clarification_turns > 0);
          return {{
            user_id: userId,
            anchor: `clarification-user-${{userId.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "unknown-user"}}`,
            conversation_count: userConversations.length,
            clarification_turns: userConversations.reduce((acc, conv) => acc + conv.clarification_turns, 0),
            conversations: userConversations.map(conv => ({{
              thread_id: conv.thread_id,
              customer: conv.customer,
              clarification_turns: conv.clarification_turns,
              turn_count: conv.turn_count,
              first_question: conv.question,
              followup_question: conv.followup_question,
              detail_link: conv.detail_link,
            }})),
          }};
        }});

      const worstCases = [...conversations]
        .sort((a, b) => (a.score - b.score) || (b.turn_count - a.turn_count) || (b.evidence - a.evidence))
        .slice(0, 20)
        .map((conv, index) => ({{
          rank: index + 1,
          thread_id: conv.thread_id,
          user_id: conv.user_id,
          customer: conv.customer,
          score: conv.score,
          label: conv.label,
          turn_count: conv.turn_count,
          evidence: conv.evidence,
          reasons: conv.reasons,
          question: conv.question,
          detail_link: conv.detail_link,
          reason_note: conv.reason_note,
        }}));

      const state = {{
        overview: {{
          conversations: conversations.length,
          turns: turns.length,
          singleTurnShare: conversations.length ? Math.round((turnCounts.filter(v => v === 1).length * 1000) / conversations.length) / 10 : 0,
          avgTurns: conversations.length ? (sum(turnCounts) / conversations.length).toFixed(2) : "0.00",
          medianTurns: turnCounts.length ? [...turnCounts].sort((a, b) => a - b)[Math.floor(turnCounts.length / 2)] : 0,
          maxTurns: turnCounts.length ? Math.max(...turnCounts) : 0,
          avgTurnDuration: turnDurations.length ? formatDurationSeconds(sum(turnDurations) / turnDurations.length) : "0s",
          p90TurnDuration: turnDurations.length ? formatDurationSeconds(percentile(turnDurations, 0.9)) : "0s",
        }},
        distribution: {{
          turn_buckets: [
            {{ label: "1 turn", value: turnCounts.filter(v => v === 1).length }},
            {{ label: "2-3 turns", value: turnCounts.filter(v => v >= 2 && v <= 3).length }},
            {{ label: "4-9 turns", value: turnCounts.filter(v => v >= 4 && v <= 9).length }},
            {{ label: "10+ turns", value: turnCounts.filter(v => v >= 10).length }},
          ],
          conversationsByDay: [...byDayCounter.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([label, value]) => ({{ label, value }})),
        }},
        topics: {{
          byTurns: toItems(topicTurnCounter),
          byConversations: toItems(topicConversationCounter),
        }},
        actors: {{
          topUsersByCost: [...userCostCounter.entries()].map(([label, value]) => ({{ label, value, href: `#user-${{label.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "unknown-user"}}` }})).sort((a, b) => b.value - a.value).slice(0, 8),
          topUsers: [...userTurnCounter.entries()].map(([label, value]) => ({{ label, value, href: `#user-${{label.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "unknown-user"}}` }})).sort((a, b) => b.value - a.value).slice(0, 8),
          topCustomers: [...customerTurnCounter.entries()].map(([label, value]) => ({{ label, value }})).sort((a, b) => b.value - a.value).slice(0, 6),
        }},
        quality: {{
          signalCounts: toItems(signalCounter),
          experienceLabels: [
            {{ label: "Good", value: conversations.filter(conv => conv.label === "good").length }},
            {{ label: "Mixed", value: conversations.filter(conv => conv.label === "mixed").length }},
            {{ label: "Poor", value: conversations.filter(conv => conv.label === "poor").length }},
          ],
          experienceAverages: (() => {{
            const avgScore = conversations.length ? conversations.reduce((acc, conv) => acc + Number(conv.score || 0), 0) / conversations.length : 0;
            return [
              {{ label: "Task Success", value: avgScore }},
              {{ label: "Efficiency", value: avgScore }},
              {{ label: "Clarity", value: avgScore }},
              {{ label: "Trust", value: avgScore }},
              {{ label: "Friction", value: avgScore }},
              {{ label: "Overall", value: avgScore }},
            ];
          }})(),
          sampleAssessments: worstCases.slice(0, 6).map(row => ({{
            thread_id: row.thread_id,
            assessment: row.label.charAt(0).toUpperCase() + row.label.slice(1),
            reason: row.reason_note,
            detail_link: row.detail_link,
          }})),
        }},
        clarification: {{
          cases: clarificationCases,
          summary: [
            {{ label: "Flagged conversations", value: clarificationCases.length }},
            {{ label: "Share of all conversations", value: conversations.length ? `${{(clarificationCases.length * 100 / conversations.length).toFixed(1)}}%` : "0.0%" }},
            {{ label: "Clarification turns", value: conversations.reduce((acc, conv) => acc + Number(conv.clarification_turns || 0), 0) }},
            {{ label: "Users involved", value: new Set(conversations.filter(conv => conv.clarification_turns > 0).map(conv => conv.user_id)).size }},
          ],
          rootCauses: toItems(clarificationRootCounter),
          topUsers: [...clarificationUserCounter.entries()].map(([label, value]) => ({{ label, value, href: `#clarification-user-${{label.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "unknown-user"}}` }})).sort((a, b) => b.value - a.value).slice(0, 8),
          userDrilldowns: clarificationUserDrilldowns,
        }},
        tables: {{
          longestConversations: [...conversations].sort((a, b) => (b.turn_count - a.turn_count) || (b.minutes_num - a.minutes_num)).slice(0, 8).map(conv => ({{
            thread_id: conv.thread_id,
            turn_count: conv.turn_count,
            minutes: conv.minutes,
            detail_link: conv.detail_link,
          }})),
          longestTurns: [...turns].sort((a, b) => b.duration_seconds - a.duration_seconds).slice(0, 8).map(turn => ({{
            thread_id: turn.thread_id,
            user_id: turn.user_id,
            seconds: formatDurationSeconds(turn.duration_seconds),
            question: turn.question,
            detail_link: turn.detail_link,
          }})),
          sampleQuestions: [...turns].sort((a, b) => (b.start_time || "").localeCompare(a.start_time || "")).slice(0, 12).map(turn => ({{
            thread_id: turn.thread_id,
            user_id: turn.user_id,
            customer: turn.customer,
            categories: (turn.categories || []).join(", "),
            question: turn.question,
            detail_link: turn.detail_link,
          }})),
          worstCases,
          userDrilldowns,
        }},
      }};
      state.insights = buildInsights(state);
      state.actions = buildActions(state);
      return state;
    }};

    const renderStats = (rootId, items) => {{
      const root = document.getElementById(rootId);
      root.innerHTML = items.map(item => {{
        const label = Array.isArray(item) ? item[0] : item.label;
        const value = Array.isArray(item) ? item[1] : item.value;
        return `
        <div class="stat">
          <div class="label">${{label}}</div>
          <div class="value">${{typeof value === "number" && !Number.isInteger(value) ? value.toFixed(1) : value}}</div>
        </div>
      `;
      }}).join("");
    }};

    const renderBars = (rootId, items) => {{
      const root = document.getElementById(rootId);
      const max = Math.max(...items.map(item => item.value), 1);
      const formatValue = value => Number.isInteger(value) ? value : value.toFixed(4);
      const scoreRoots = new Set(["experience-averages"]);
      const displayValue = value => {{
        if (rootId === "top-users-by-cost") return `$${{Number(value).toFixed(2)}}`;
        if (scoreRoots.has(rootId)) return Number(value).toFixed(1);
        return formatValue(value);
      }};
      root.innerHTML = items.map(item => `
        <div class="bar-row">
          <div>${{item.href ? `<a class="bar-link" href="${{item.href}}">${{item.label}}</a>` : item.label}}</div>
          <div class="bar-track"><div class="bar-fill" style="width: ${{(item.value / max) * 100}}%"></div></div>
          <div>${{displayValue(item.value)}}</div>
        </div>
      `).join("");
    }};

    const renderTable = (rootId, rows, mapper) => {{
      const root = document.getElementById(rootId);
      root.innerHTML = rows.map(mapper).join("");
    }};

    const renderInsights = () => {{
      document.getElementById("insights").innerHTML = data.insights.map(text => `
        <div class="insight">${{text}}</div>
      `).join("");
    }};

    const renderActions = () => {{
      document.getElementById("actions").innerHTML = data.actions.map(item => `
        <div class="action-card">
          <strong>${{item.title}}</strong>
          <p>${{item.body}}</p>
        </div>
      `).join("");
    }};

    const renderConfig = () => {{
      const entries = [
        ["Env", fullData.config.opik_project],
        ["Target Env", fullData.config.target_env],
        ["Base URL", fullData.config.opik_base_url],
        ["URL Override", fullData.config.opik_url_override],
        ["Workspace", fullData.config.opik_workspace],
      ].filter(([, value]) => value);
      const root = document.getElementById("config-grid");
      root.innerHTML = entries.map(([label, value]) => `
        <div class="config-card">
          <div class="label">${{label}}</div>
          <div class="value">${{value}}</div>
        </div>
      `).join("");
    }};

    const renderUserDrilldowns = () => {{
      const root = document.getElementById("user-drilldowns");
      root.innerHTML = filteredData.tables.userDrilldowns.map(user => `
        <section class="user-drilldown" id="${{user.anchor}}">
          <div class="user-head">
            <h3>${{user.user_id}}</h3>
            <a class="bar-link" href="#top">Back to top</a>
          </div>
          <div class="user-meta">
            Conversations: ${{user.conversation_count}} | Turns: ${{user.turn_count}} | Estimated cost: $${{Number(user.estimated_cost).toFixed(2)}}
          </div>
          <table>
            <thead><tr><th>Thread</th><th>Customer</th><th>Turns</th><th>Duration</th><th>Est. Cost</th><th>First Question</th></tr></thead>
            <tbody>
              ${{
                user.conversations.map(row => `
                  <tr>
                    <td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td>
                    <td>${{row.customer}}</td>
                    <td>${{row.turn_count}}</td>
                    <td>${{row.minutes}}</td>
                    <td>$${{Number(row.estimated_cost).toFixed(2)}}</td>
                    <td>${{row.question || ""}}</td>
                  </tr>
                `).join("")
              }}
            </tbody>
          </table>
        </section>
      `).join("");
    }};

    const renderClarificationUserDrilldowns = () => {{
      const root = document.getElementById("clarification-user-drilldowns");
      root.innerHTML = filteredData.clarification.userDrilldowns.map(user => `
        <section class="user-drilldown" id="${{user.anchor}}">
          <div class="user-head">
            <h3>${{user.user_id}}</h3>
            <a class="bar-link" href="#clarification-section">Back to clarification</a>
          </div>
          <div class="user-meta">
            Flagged conversations: ${{user.conversation_count}} | Clarification turns: ${{user.clarification_turns}}
          </div>
          <table>
            <thead><tr><th>Thread</th><th>Customer</th><th>Clarification Turns</th><th>Total Turns</th><th>First Question</th><th>Clarifying Follow-up</th></tr></thead>
            <tbody>
              ${{
                user.conversations.map(row => `
                  <tr>
                    <td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td>
                    <td>${{row.customer}}</td>
                    <td>${{row.clarification_turns}}</td>
                    <td>${{row.turn_count}}</td>
                    <td>${{row.first_question || ""}}</td>
                    <td>${{row.followup_question || ""}}</td>
                  </tr>
                `).join("")
              }}
            </tbody>
          </table>
        </section>
      `).join("");
    }};

    const dateStartInput = document.getElementById("date-start");
    const dateEndInput = document.getElementById("date-end");
    const filterSummary = document.getElementById("filter-summary");

    const allDates = fullData.raw.conversations.map(conv => (conv.first_time || "").slice(0, 10)).filter(Boolean).sort();
    const minDate = allDates[0] || "";
    const maxDate = allDates[allDates.length - 1] || "";
    dateStartInput.min = minDate;
    dateStartInput.max = maxDate;
    dateEndInput.min = minDate;
    dateEndInput.max = maxDate;
    dateStartInput.value = minDate;
    dateEndInput.value = maxDate;

    const renderAll = () => {{
      const overviewStats = [
        ["Conversations", filteredData.overview.conversations],
        ["Turns", filteredData.overview.turns],
        ["Single-turn share", `${{filteredData.overview.singleTurnShare}}%`],
        ["Average turns", filteredData.overview.avgTurns],
        ["Median turns", filteredData.overview.medianTurns],
        ["Max turns", filteredData.overview.maxTurns],
        ["Avg turn duration", filteredData.overview.avgTurnDuration],
        ["P90 turn duration", filteredData.overview.p90TurnDuration],
      ];

      filterSummary.textContent = `${{dateStartInput.value || minDate}} to ${{dateEndInput.value || maxDate}} | ${{filteredData.overview.conversations}} conversations`;
      renderStats("overview-stats", overviewStats);
      renderBars("conversations-by-day", filteredData.distribution.conversationsByDay);
      renderBars("turn-buckets", filteredData.distribution.turn_buckets);
      renderBars("topic-bars", filteredData.topics.byConversations.slice(0, 8));
      renderBars("top-users-by-cost", filteredData.actors.topUsersByCost);
      renderBars("top-users", filteredData.actors.topUsers);
      renderBars("top-customers", filteredData.actors.topCustomers);
      renderBars("quality-signals", filteredData.quality.signalCounts);
      renderStats("clarification-summary", filteredData.clarification.summary);
      renderBars("clarification-root-causes", filteredData.clarification.rootCauses);
      renderBars("top-users-by-clarification", filteredData.clarification.topUsers);
      renderBars("experience-labels", filteredData.quality.experienceLabels);
      renderBars("experience-averages", filteredData.quality.experienceAverages);
      document.getElementById("insights").innerHTML = filteredData.insights.map(text => `<div class="insight">${{text}}</div>`).join("");
      document.getElementById("actions").innerHTML = filteredData.actions.map(item => `<div class="action-card"><strong>${{item.title}}</strong><p>${{item.body}}</p></div>`).join("");
      renderClarificationUserDrilldowns();
      renderUserDrilldowns();
      renderTable("longest-conversations", filteredData.tables.longestConversations, row => `
        <tr><td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td><td>${{row.turn_count}}</td><td>${{row.minutes}}</td></tr>
      `);
      renderTable("longest-turns", filteredData.tables.longestTurns, row => `
        <tr><td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td><td>${{row.user_id || "n/a"}}</td><td>${{row.seconds}}</td><td>${{row.question}}</td></tr>
      `);
      renderTable("sample-assessments", filteredData.quality.sampleAssessments, row => `
        <tr>
          <td>${{row.detail_link ? `<a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a>` : row.thread_id}}</td>
          <td><span class="${{pillClass(row.assessment)}}">${{row.assessment}}</span></td>
          <td>${{row.reason}}</td>
        </tr>
      `);
      renderTable("sample-questions", filteredData.tables.sampleQuestions, row => `
        <tr>
          <td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td>
          <td>${{row.user_id || "n/a"}}</td>
          <td>${{row.customer || "n/a"}}</td>
          <td>${{row.categories}}</td>
          <td>${{row.question}}</td>
        </tr>
      `);
      renderTable("clarification-cases", filteredData.clarification.cases, row => `
        <tr>
          <td><a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a></td>
          <td>${{row.user_id}}</td>
          <td>${{row.customer}}</td>
          <td>${{row.root_cause}}</td>
          <td>${{row.clarification_turns}}</td>
          <td>${{row.turn_count}}</td>
          <td>${{row.first_question}}</td>
          <td>${{row.followup_question}}</td>
        </tr>
      `);
      renderTable("worst-cases", filteredData.tables.worstCases, row => `
        <tr>
          <td>${{row.rank}}</td>
          <td>${{row.detail_link ? `<a class="bar-link" href="${{row.detail_link}}">${{row.thread_id}}</a>` : row.thread_id}}</td>
          <td class="compact-cell">
            <div class="compact-meta">
              <strong>${{row.user_id}}</strong>
              <span>${{row.customer}}</span>
              <span>Turns: ${{row.turn_count}} | Evidence: ${{row.evidence}}</span>
            </div>
          </td>
          <td>
            <div>${{Number(row.score).toFixed(1)}}</div>
            <div style="margin-top:6px;"><span class="${{pillClass(row.label)}}">${{row.label}}</span></div>
          </td>
          <td class="compact-cell"><div class="line-clamp-2">${{row.reasons}}</div></td>
          <td class="compact-cell"><div class="line-clamp-3">${{row.question}}</div></td>
        </tr>
      `);
    }};

    renderConfig();
    const applyFilter = () => {{
      const start = dateStartInput.value || minDate;
      const end = dateEndInput.value || maxDate;
      filteredData = computeState(fullData, start, end);
      renderAll();
    }};

    document.getElementById("apply-filter").addEventListener("click", applyFilter);
    document.getElementById("reset-filter").addEventListener("click", () => {{
      dateStartInput.value = minDate;
      dateEndInput.value = maxDate;
      applyFilter();
    }});

    filteredData = computeState(fullData, minDate, maxDate);
    renderAll();
  </script>
</body>
</html>
"""


def main():
    env_outputs = []
    for env_key in ("qe", "prod"):
        export_dir = ENV_DATA_DIR / env_key
        if not export_dir.exists():
            continue
        output_name = f"analysis_dashboard_{env_key}.html"
        set_runtime_context(export_dir, env_key, output_name)
        data = build_dashboard_data()
        env_outputs.append((env_key, output_name))
        html = render_html(data, env_key, [])
        with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
            file.write(html)

    for env_key, output_name in env_outputs:
        export_dir = ENV_DATA_DIR / env_key
        set_runtime_context(export_dir, env_key, output_name)
        data = build_dashboard_data()
        html = render_html(data, env_key, env_outputs)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as file:
            file.write(html)
        print(f"Generated dashboard: {OUTPUT_PATH}")

    if env_outputs:
        default_src = BASE_DIR / "analysis_dashboard_qe.html"
        if not default_src.exists():
            default_src = BASE_DIR / env_outputs[0][1]
        (BASE_DIR / "analysis_dashboard.html").write_text(default_src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Updated default dashboard: {BASE_DIR / 'analysis_dashboard.html'}")


if __name__ == "__main__":
    main()
