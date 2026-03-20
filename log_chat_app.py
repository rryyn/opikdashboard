#!/usr/bin/env python3

import ast
import json
import re
import tkinter as tk
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import ttk


BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = Path("/Users/linwang")
CONVERSATIONS_PATH = EXPORT_DIR / "conversations.jsonl"


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
    chunks = re.findall(r"SSEEvent\(event='([^']+)', data=(\".*?\"|'.*?')\)", answer, re.S)
    if not chunks:
        return answer
    parts = []
    for event_name, raw_data in chunks:
        try:
            text = ast.literal_eval(raw_data)
        except Exception:
            text = raw_data.strip("'\"")
        if event_name == "delta":
            parts.append(text)
    return "".join(parts).strip() or answer


CLARIFICATION_REGEX = re.compile(
    r"(?:\bnot right\b|\bto be clear\b|\bi mean\b|\bwhat i mean\b|\bthat's not\b|\bthat is not\b|"
    r"\bdidn't answer\b|\bdid not answer\b|\bnot what i\b|\byou missed\b|\bi asked\b|"
    r"\bhow did you define\b|\bdid you read our previous conversations\b|\bstill using\b|"
    r"\brun this same analysis\b|\bshould be\b|\bexclude bot traffic\b|\bactually\b|"
    r"\blet me rephrase\b|\brephrase\b|\breframe\b|\bexact system labels\b|\braw event conditions\b|"
    r"\bwith evidence\b|你没回答|不是这个意思|不是这个问题|我问的是|你返回的.*跟.*不一致|"
    r"你返回的.*跟.*对不上|和.*不一致|跟.*不一致)",
    re.IGNORECASE,
)


@dataclass
class Turn:
    thread_id: str
    index: int
    user_id: str
    customer_id: str
    start_time: str
    question: str
    answer: str


@dataclass
class Conversation:
    thread_id: str
    user_id: str
    customer_id: str
    turn_count: int
    cost: float
    first_time: str
    questions_blob: str
    turns: list[Turn]


class LogKnowledgeBase:
    def __init__(self):
        self.conversations = self._load_conversations()
        self.by_thread = {conv.thread_id: conv for conv in self.conversations}
        self.by_user = defaultdict(list)
        self.by_customer = defaultdict(list)
        for conv in self.conversations:
            self.by_user[conv.user_id].append(conv)
            self.by_customer[conv.customer_id].append(conv)

    def _load_conversations(self):
        conversations = []
        if not CONVERSATIONS_PATH.exists():
            return conversations
        for line in CONVERSATIONS_PATH.read_text(encoding="utf-8").splitlines():
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
                    Turn(
                        thread_id=row.get("thread_id", ""),
                        index=index,
                        user_id=user_id,
                        customer_id=customer_id,
                        start_time=str(trace.get("start_time", "") or ""),
                        question=extract_question(trace),
                        answer=clean_answer(extract_answer(trace)),
                    )
                )
            questions_blob = " ".join(turn.question for turn in turns).lower()
            conversations.append(
                Conversation(
                    thread_id=row.get("thread_id", ""),
                    user_id=user_id,
                    customer_id=customer_id,
                    turn_count=len(turns),
                    cost=round(total_cost, 4),
                    first_time=turns[0].start_time if turns else "",
                    questions_blob=questions_blob,
                    turns=turns,
                )
            )
        conversations.sort(key=lambda item: item.first_time, reverse=True)
        return conversations

    def conversations_by_day(self):
        counts = Counter()
        for conv in self.conversations:
            if conv.first_time:
                try:
                    counts[datetime.fromisoformat(conv.first_time).date().isoformat()] += 1
                except Exception:
                    continue
        return sorted(counts.items())

    def top_users_by_cost(self, limit=8):
        costs = defaultdict(float)
        for conv in self.conversations:
            if conv.user_id and conv.user_id != "n/a":
                costs[conv.user_id] += conv.cost
        return sorted(costs.items(), key=lambda item: item[1], reverse=True)[:limit]

    def top_users_by_turns(self, limit=8):
        counts = Counter()
        for conv in self.conversations:
            if conv.user_id and conv.user_id != "n/a":
                counts[conv.user_id] += conv.turn_count
        return counts.most_common(limit)

    def top_customers(self, limit=8):
        counts = Counter()
        for conv in self.conversations:
            if conv.customer_id and conv.customer_id != "n/a":
                counts[conv.customer_id] += conv.turn_count
        return counts.most_common(limit)

    def clarification_cases(self):
        matches = []
        for conv in self.conversations:
            flagged_turns = [turn for turn in conv.turns[1:] if CLARIFICATION_REGEX.search(turn.question or "")]
            if flagged_turns:
                matches.append((conv, flagged_turns))
        return matches

    def search(self, query, limit=10):
        q = query.strip().lower()
        if not q:
            return []
        results = []
        for conv in self.conversations:
            haystack = " ".join(
                [conv.thread_id.lower(), conv.user_id.lower(), conv.customer_id.lower(), conv.questions_blob]
            )
            if q in haystack:
                score = haystack.count(q) + conv.turn_count * 0.05
                results.append((score, conv))
        results.sort(key=lambda item: item[0], reverse=True)
        return [conv for _, conv in results[:limit]]


class LogChatApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Log Chat")
        self.root.geometry("1240x860")
        self.root.minsize(980, 720)
        self.kb = LogKnowledgeBase()
        self._build_ui()
        self._append_message(
            "assistant",
            "日志已加载。你可以问：top users by cost、clarification cases、conversations by day、search <关键词>、show thread <thread_id>。",
        )

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        self.root.configure(bg="#fcfcfd")
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=3)
        shell.columnconfigure(1, weight=4)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(header, text="Log Chat", font=("Helvetica", 18, "bold")).pack(side="left")
        ttk.Label(
            header,
            text=f"{len(self.kb.conversations)} conversations loaded from {CONVERSATIONS_PATH}",
        ).pack(side="left", padx=(12, 0))

        left = ttk.Frame(shell)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=0)
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(shell)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.chat = tk.Text(
            left,
            wrap="word",
            bg="#ffffff",
            fg="#222629",
            relief="solid",
            borderwidth=1,
            padx=12,
            pady=12,
        )
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.chat.configure(state="disabled")

        input_row = ttk.Frame(left)
        input_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        input_row.columnconfigure(0, weight=1)

        self.entry = ttk.Entry(input_row)
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", self._handle_submit)
        ttk.Button(input_row, text="Ask", command=self._handle_submit).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(right, text="Matched Conversations", font=("Helvetica", 14, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.result_list = tk.Listbox(
            right,
            bg="#ffffff",
            fg="#222629",
            relief="solid",
            borderwidth=1,
            exportselection=False,
        )
        self.result_list.grid(row=1, column=0, sticky="nsew")
        self.result_list.bind("<<ListboxSelect>>", self._show_selected_conversation)

        ttk.Label(right, text="Conversation Detail", font=("Helvetica", 14, "bold")).grid(
            row=2, column=0, sticky="w", pady=(10, 8)
        )
        self.detail = tk.Text(
            right,
            wrap="word",
            bg="#ffffff",
            fg="#222629",
            relief="solid",
            borderwidth=1,
            padx=12,
            pady=12,
        )
        self.detail.grid(row=3, column=0, sticky="nsew")
        right.rowconfigure(3, weight=2)

        self.current_results = []

    def _append_message(self, role, text):
        self.chat.configure(state="normal")
        prefix = "You" if role == "user" else "Assistant"
        self.chat.insert("end", f"{prefix}\n", ("role",))
        self.chat.insert("end", f"{text}\n\n")
        self.chat.tag_configure("role", foreground="#4a8500", font=("Helvetica", 10, "bold"))
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _set_results(self, conversations):
        self.current_results = conversations
        self.result_list.delete(0, "end")
        for conv in conversations:
            label = f"{conv.thread_id} | {conv.user_id} | turns={conv.turn_count} | ${conv.cost:.2f}"
            self.result_list.insert("end", label)
        if conversations:
            self.result_list.selection_set(0)
            self._show_conversation(conversations[0])
        else:
            self.detail.delete("1.0", "end")

    def _show_selected_conversation(self, _event=None):
        selection = self.result_list.curselection()
        if not selection:
            return
        self._show_conversation(self.current_results[selection[0]])

    def _show_conversation(self, conv):
        self.detail.delete("1.0", "end")
        self.detail.insert("end", f"Thread: {conv.thread_id}\n")
        self.detail.insert("end", f"User: {conv.user_id}\nCustomer: {conv.customer_id}\n")
        self.detail.insert("end", f"Turns: {conv.turn_count} | Estimated cost: ${conv.cost:.2f}\n\n")
        for turn in conv.turns:
            self.detail.insert("end", f"Turn {turn.index} | {turn.start_time}\n", ("turn",))
            self.detail.insert("end", f"Question\n{turn.question or '(empty)'}\n\n", ("q",))
            self.detail.insert("end", f"Answer\n{turn.answer or '(empty)'}\n\n", ("a",))
        self.detail.tag_configure("turn", foreground="#4a8500", font=("Helvetica", 10, "bold"))
        self.detail.tag_configure("q", font=("Helvetica", 10, "bold"))
        self.detail.tag_configure("a", foreground="#333333")

    def _handle_submit(self, _event=None):
        question = self.entry.get().strip()
        if not question:
            return
        self.entry.delete(0, "end")
        self._append_message("user", question)
        answer, results = self._answer(question)
        self._append_message("assistant", answer)
        self._set_results(results)

    def _answer(self, question):
        q = question.strip().lower()
        if q.startswith("show thread "):
            thread_id = question.split(" ", 2)[-1].strip()
            conv = self.kb.by_thread.get(thread_id)
            if conv:
                return (
                    f"Found thread `{thread_id}`. The full conversation is shown on the right.",
                    [conv],
                )
            return (f"Thread `{thread_id}` was not found in the current export.", [])

        if "top users by cost" in q or ("top user" in q and "cost" in q):
            rows = self.kb.top_users_by_cost()
            text = "\n".join(f"{index}. {user}: ${cost:.2f}" for index, (user, cost) in enumerate(rows, 1))
            return (f"Top users by estimated cost:\n{text}", [])

        if "top users" in q:
            rows = self.kb.top_users_by_turns()
            text = "\n".join(f"{index}. {user}: {turns} turns" for index, (user, turns) in enumerate(rows, 1))
            return (f"Top users by turn volume:\n{text}", [])

        if "top customers" in q:
            rows = self.kb.top_customers()
            text = "\n".join(f"{index}. {customer}: {turns} turns" for index, (customer, turns) in enumerate(rows, 1))
            return (f"Top customers by turn volume:\n{text}", [])

        if "clarification" in q:
            cases = self.kb.clarification_cases()
            if not cases:
                return ("No clarification follow-up cases were found in the current export.", [])
            conversations = [conv for conv, _ in cases[:10]]
            lines = []
            for conv, flagged_turns in cases[:10]:
                lines.append(f"{conv.thread_id} | {conv.user_id} | clarification turns={len(flagged_turns)}")
            return (
                "Clarification cases in the current export:\n" + "\n".join(lines),
                conversations,
            )

        if "conversations by day" in q or ("day by day" in q and "conversation" in q):
            rows = self.kb.conversations_by_day()
            text = "\n".join(f"{day}: {count}" for day, count in rows)
            return (f"Conversation volume by day:\n{text}", [])

        if q.startswith("search "):
            keyword = question[7:].strip()
            matches = self.kb.search(keyword)
            if not matches:
                return (f"No conversations matched `{keyword}`.", [])
            text = "\n".join(
                f"{index}. {conv.thread_id} | {conv.user_id} | {conv.turn_count} turns"
                for index, conv in enumerate(matches, 1)
            )
            return (f"Matches for `{keyword}`:\n{text}\nOpen one from the right panel.", matches)

        matches = self.kb.search(question)
        if matches:
            top = matches[0]
            return (
                "I matched your question against thread ids, users, customers, and question text. "
                f"Top match is `{top.thread_id}`. I also listed related conversations on the right.",
                matches,
            )

        return (
            "I couldn't answer that directly yet. Try `top users by cost`, `clarification cases`, "
            "`conversations by day`, `search payment`, or `show thread <thread_id>`.",
            [],
        )


def main():
    root = tk.Tk()
    app = LogChatApp(root)
    app.entry.focus()
    root.mainloop()


if __name__ == "__main__":
    main()
