import json
import os
import sys
import csv
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from opik import Opik


DEFAULT_OPIK_BASE_URL = "http://opik-nexa-01.us-east4.dev.gcp.conviva.com:5173/api"
CONFIG_FILE = Path(__file__).with_name("opik_config.json")
OUTPUT_FILE = "conversations.jsonl"
SUMMARY_FILE = "conversations_summary.csv"
TURNS_FILE = "turns_flat.csv"
DEFAULT_BATCH_SIZE = 1000
MIN_WINDOW_SECONDS = 1
COMMON_HOST_FIXES = {
    ".useast4.": ".us-east4.",
}


def load_config():
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def get_config_value(config, key):
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value
    file_value = str(config.get(key, "")).strip()
    if file_value:
        return file_value
    return ""


def get_required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        print(f"Missing environment variable: {name}")
        sys.exit(1)
    return value


def get_optional_env(name):
    value = os.getenv(name, "").strip()
    if value:
        return value
    return None


def get_batch_size():
    value = os.getenv("MAX_RESULTS", "").strip()
    if not value:
        return DEFAULT_BATCH_SIZE
    return int(value)


def normalize_host(host):
    parsed = urlparse(host)
    hostname = parsed.hostname or ""
    fixed_hostname = hostname
    for wrong, correct in COMMON_HOST_FIXES.items():
        if wrong in fixed_hostname:
            fixed_hostname = fixed_hostname.replace(wrong, correct)

    if fixed_hostname == hostname:
        return host

    netloc = fixed_hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{netloc}"

    fixed_host = urlunparse(parsed._replace(netloc=netloc))
    print(f"Normalized OPIK host from {host} to {fixed_host}")
    return fixed_host


def explain_connection_error(error, host):
    message = str(error)
    if "nodename nor servname provided, or not known" in message:
        print(f"Failed to resolve OPIK host: {host}")
        print("Check OPIK_BASE_URL spelling and network/DNS access to the Opik environment.")
        return
    print(f"Failed to connect to OPIK host: {host}")
    print(message)


def trace_to_dict(trace):
    if hasattr(trace, "model_dump"):
        return trace.model_dump()
    if hasattr(trace, "dict"):
        return trace.dict()
    if hasattr(trace, "to_dict"):
        return trace.to_dict()
    if hasattr(trace, "__dict__"):
        result = {}
        for key, value in trace.__dict__.items():
            if not key.startswith("_"):
                result[key] = value
        return result
    raise TypeError(f"Unsupported trace type: {type(trace)}")


def get_thread_id(trace_dict):
    thread_id = trace_dict.get("thread_id")
    if thread_id:
        return str(thread_id)
    return "no_thread_id"


def get_sort_key(trace_dict):
    return str(trace_dict.get("start_time", "") or "")


def normalize_json(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_datetime(value):
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_datetime(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_time_filter(start_time, end_time):
    start_text = format_datetime(start_time)
    end_text = format_datetime(end_time)
    return f'start_time >= "{start_text}" AND start_time < "{end_text}"'


def fetch_window(client, project_name, start_time, end_time, batch_size):
    filter_string = build_time_filter(start_time, end_time)
    traces = client.search_traces(
        project_name=project_name,
        filter_string=filter_string,
        max_results=batch_size,
        truncate=False,
    )
    return [trace_to_dict(trace) for trace in traces]


def split_window(start_time, end_time):
    seconds = (end_time - start_time).total_seconds()
    midpoint = start_time + timedelta(seconds=seconds / 2)
    return midpoint


def fetch_all_traces_in_window(client, project_name, start_time, end_time, batch_size, depth=0):
    traces = fetch_window(client, project_name, start_time, end_time, batch_size)

    if len(traces) < batch_size:
        print(
            f"batch done: depth={depth} traces={len(traces)} "
            f"range={format_datetime(start_time)} -> {format_datetime(end_time)}"
        )
        return traces

    window_seconds = (end_time - start_time).total_seconds()
    if window_seconds <= MIN_WINDOW_SECONDS:
        print("warning: reached minimum window size, this range may still be truncated")
        return traces

    print(
        f"splitting window: depth={depth} traces={len(traces)} "
        f"range={format_datetime(start_time)} -> {format_datetime(end_time)}"
    )
    midpoint = split_window(start_time, end_time)
    left_traces = fetch_all_traces_in_window(
        client, project_name, start_time, midpoint, batch_size, depth + 1
    )
    right_traces = fetch_all_traces_in_window(
        client, project_name, midpoint, end_time, batch_size, depth + 1
    )
    return left_traces + right_traces


def dedupe_traces(trace_dicts):
    unique = {}
    for trace_dict in trace_dicts:
        trace_id = str(trace_dict.get("id", "") or "")
        if trace_id:
            unique[trace_id] = trace_dict
    return list(unique.values())


def get_latest_trace_time(client, project_name):
    traces = client.search_traces(
        project_name=project_name,
        max_results=1,
        truncate=False,
    )
    if not traces:
        return None
    latest_trace = trace_to_dict(traces[0])
    return parse_datetime(latest_trace["start_time"])


def main():
    config = load_config()
    project_name = get_config_value(config, "OPIK_PROJECT")
    if not project_name:
        print(f"Missing OPIK_PROJECT. Set it in {CONFIG_FILE} or as an environment variable.")
        sys.exit(1)
    batch_size = get_batch_size()
    host = (
        get_config_value(config, "OPIK_BASE_URL")
        or get_config_value(config, "OPIK_URL_OVERRIDE")
        or DEFAULT_OPIK_BASE_URL
    )
    host = normalize_host(host)
    api_key = get_config_value(config, "OPIK_API_KEY")
    workspace = get_config_value(config, "OPIK_WORKSPACE")

    client_kwargs = {
        "project_name": project_name,
        "host": host,
    }
    if api_key:
        client_kwargs["api_key"] = api_key
    if workspace:
        client_kwargs["workspace"] = workspace

    client = Opik(**client_kwargs)

    try:
        latest_trace_time = get_latest_trace_time(client, project_name)
    except Exception as error:
        explain_connection_error(error, host)
        sys.exit(1)
    if latest_trace_time is None:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            file.write("")
        print(f"Saved conversations to {os.path.abspath(OUTPUT_FILE)}")
        print("fetched traces: 0")
        print("number of conversations: 0")
        print("average turns: 0.00")
        print("max turns: 0")
        return

    start_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
    end_time = latest_trace_time + timedelta(microseconds=1)

    try:
        trace_dicts = fetch_all_traces_in_window(
            client=client,
            project_name=project_name,
            start_time=start_time,
            end_time=end_time,
            batch_size=batch_size,
        )
    except Exception as error:
        explain_connection_error(error, host)
        sys.exit(1)
    trace_dicts = dedupe_traces(trace_dicts)

    conversations = {}
    for trace_dict in trace_dicts:
        thread_id = get_thread_id(trace_dict)
        if thread_id not in conversations:
            conversations[thread_id] = []
        conversations[thread_id].append(trace_dict)

    for thread_id in conversations:
        conversations[thread_id].sort(key=get_sort_key)

    conversation_count = len(conversations)
    turn_counts = []
    for thread_id in conversations:
        turn_counts.append(len(conversations[thread_id]))

    if conversation_count == 0:
        average_turns = 0
        max_turns = 0
    else:
        average_turns = sum(turn_counts) / conversation_count
        max_turns = max(turn_counts)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        for thread_id in conversations:
            conversation = {
                "thread_id": thread_id,
                "turn_count": len(conversations[thread_id]),
                "traces": conversations[thread_id],
            }
            file.write(json.dumps(conversation, ensure_ascii=False, default=str) + "\n")

    with open(SUMMARY_FILE, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "thread_id",
                "turn_count",
                "first_start_time",
                "last_start_time",
            ],
        )
        writer.writeheader()
        for thread_id in conversations:
            traces = conversations[thread_id]
            writer.writerow(
                {
                    "thread_id": thread_id,
                    "turn_count": len(traces),
                    "first_start_time": str(traces[0].get("start_time", "") or ""),
                    "last_start_time": str(traces[-1].get("start_time", "") or ""),
                }
            )

    with open(TURNS_FILE, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "thread_id",
                "trace_id",
                "name",
                "start_time",
                "end_time",
                "input",
                "output",
                "tags",
                "metadata",
            ],
        )
        writer.writeheader()
        for thread_id in conversations:
            traces = conversations[thread_id]
            for trace in traces:
                writer.writerow(
                    {
                        "thread_id": thread_id,
                        "trace_id": str(trace.get("id", "") or ""),
                        "name": str(trace.get("name", "") or ""),
                        "start_time": str(trace.get("start_time", "") or ""),
                        "end_time": str(trace.get("end_time", "") or ""),
                        "input": normalize_json(trace.get("input")),
                        "output": normalize_json(trace.get("output")),
                        "tags": normalize_json(trace.get("tags")),
                        "metadata": normalize_json(trace.get("metadata")),
                    }
                )

    print(f"Saved conversations to {os.path.abspath(OUTPUT_FILE)}")
    print(f"Saved summary CSV to {os.path.abspath(SUMMARY_FILE)}")
    print(f"Saved turns CSV to {os.path.abspath(TURNS_FILE)}")
    print(f"fetched traces: {len(trace_dicts)}")
    print(f"number of conversations: {conversation_count}")
    print(f"average turns: {average_turns:.2f}")
    print(f"max turns: {max_turns}")


if __name__ == "__main__":
    main()
