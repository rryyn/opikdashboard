import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

from opik import Opik


DEFAULT_OPIK_BASE_URL = "http://opik-nexa-01.us-east4.dev.gcp.conviva.com:5173/api"
CONFIG_FILE = Path(__file__).with_name("opik_config.json")
RAW_OUTPUT_FILE = "conversations_raw.json"
CSV_OUTPUT_FILE = "conversations_flat.csv"
MAX_RESULTS = 5000


def load_config() -> Dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def get_config_value(config: Dict[str, str], key: str) -> str:
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value
    file_value = str(config.get(key, "")).strip()
    if file_value:
        return file_value
    return ""


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def get_optional_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    return ""


def normalize_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def trace_to_dict(trace: Any) -> Dict[str, Any]:
    if hasattr(trace, "model_dump"):
        return trace.model_dump()
    if hasattr(trace, "dict"):
        return trace.dict()
    if hasattr(trace, "to_dict"):
        return trace.to_dict()
    if hasattr(trace, "__dict__"):
        return {
            key: value
            for key, value in trace.__dict__.items()
            if not key.startswith("_")
        }
    raise TypeError(f"Unsupported trace object type: {type(trace)!r}")


def metadata_has_target_env(metadata: Dict[str, Any], target_env: str) -> bool:
    for key in ("environment", "env", "target_env"):
        if str(metadata.get(key, "")).strip().lower() == target_env.lower():
            return True
    return False


def metadata_has_any_env(metadata: Dict[str, Any]) -> bool:
    for key in ("environment", "env", "target_env"):
        if str(metadata.get(key, "")).strip():
            return True
    return False


def tags_have_target_env(tags: Iterable[Any], target_env: str) -> bool:
    normalized_target = target_env.lower()
    for tag in tags or []:
        tag_text = str(tag).strip().lower()
        if tag_text == normalized_target:
            return True
        if tag_text in {
            f"env:{normalized_target}",
            f"environment:{normalized_target}",
            f"target_env:{normalized_target}",
        }:
            return True
    return False


def tags_have_any_env(tags: Iterable[Any]) -> bool:
    for tag in tags or []:
        tag_text = str(tag).strip().lower()
        if tag_text.startswith("env:"):
            return True
        if tag_text.startswith("environment:"):
            return True
        if tag_text.startswith("target_env:"):
            return True
    return False


def keep_trace(trace_dict: Dict[str, Any], target_env: str) -> bool:
    metadata = trace_dict.get("metadata") or {}
    tags = trace_dict.get("tags") or []
    has_env_marker = metadata_has_any_env(metadata) or tags_have_any_env(tags)

    if not has_env_marker:
        return True
    return metadata_has_target_env(metadata, target_env) or tags_have_target_env(
        tags, target_env
    )


def flatten_trace(trace_dict: Dict[str, Any]) -> Dict[str, str]:
    return {
        "thread_id": str(trace_dict.get("thread_id", "") or ""),
        "trace_id": str(trace_dict.get("id", "") or ""),
        "name": str(trace_dict.get("name", "") or ""),
        "start_time": str(trace_dict.get("start_time", "") or ""),
        "input": normalize_json(trace_dict.get("input")),
        "output": normalize_json(trace_dict.get("output")),
        "tags": normalize_json(trace_dict.get("tags") or []),
        "metadata": normalize_json(trace_dict.get("metadata") or {}),
    }


def main() -> None:
    config = load_config()
    project_name = get_config_value(config, "OPIK_PROJECT")
    target_env = get_config_value(config, "TARGET_ENV")
    if not project_name:
        print(
            f"Missing OPIK_PROJECT. Set it in {CONFIG_FILE} or as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not target_env:
        print(
            f"Missing TARGET_ENV. Set it in {CONFIG_FILE} or as an environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)
    host = (
        get_config_value(config, "OPIK_BASE_URL")
        or get_config_value(config, "OPIK_URL_OVERRIDE")
        or DEFAULT_OPIK_BASE_URL
    )
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

    traces = client.search_traces(
        project_name=project_name,
        max_results=MAX_RESULTS,
        truncate=False,
    )

    trace_dicts = [trace_to_dict(trace) for trace in traces]
    filtered_traces = [trace for trace in trace_dicts if keep_trace(trace, target_env)]
    flat_rows = [flatten_trace(trace) for trace in filtered_traces]

    with open(RAW_OUTPUT_FILE, "w", encoding="utf-8") as raw_file:
        json.dump(filtered_traces, raw_file, ensure_ascii=False, indent=2)

    with open(CSV_OUTPUT_FILE, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "thread_id",
                "trace_id",
                "name",
                "start_time",
                "input",
                "output",
                "tags",
                "metadata",
            ],
        )
        writer.writeheader()
        writer.writerows(flat_rows)

    print(f"Exported {len(filtered_traces)} traces.")
    print(f"Raw JSON: {os.path.abspath(RAW_OUTPUT_FILE)}")
    print(f"Flat CSV: {os.path.abspath(CSV_OUTPUT_FILE)}")


if __name__ == "__main__":
    main()
