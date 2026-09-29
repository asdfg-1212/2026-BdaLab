from pathlib import Path
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest


def test_landing_page_has_chat_and_no_exception():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "frontend/app.py"))
    app.run(timeout=20)
    assert not app.exception
    assert app.title[0].value == "MovieLens 数据治理 Agent"
    assert len(app.chat_input) == 1
    assert not app.chat_input[0].disabled


def test_followup_shows_server_history_and_switches_tasks():
    histories = {
        "a" * 32: [{"role": "user", "content": "任务A"},
                   {"role": "assistant", "content": "A已完成"}],
        "b" * 32: [{"role": "user", "content": "任务B"},
                   {"role": "assistant", "content": "B已完成"}],
    }
    posts = []

    def respond(method, url, **kwargs):
        parts = url.split("/tasks/")[1].split("/")
        task_id = parts[0]
        if len(parts) == 1:
            value = {"status": "completed", "stage": "completed", "message": "完成"}
        elif parts[1] == "report":
            value = {
                "before": {"scores": {"Unique": 90}, "tables": {}},
                "after": {"scores": {"Unique": 100}, "tables": {},
                          "issues": {}, "warnings": {}},
                "delta": {"Unique": 10}, "methods": {}, "limitations": [],
                "disposition": {"tables": {}, "reasons": {}}, "reason_labels": {},
                "examples": [], "samples": {}, "manifest": {"task_id": task_id},
            }
        elif method == "POST":
            posts.append(kwargs["json"]["message"])
            histories[task_id] += [
                {"role": "user", "content": kwargs["json"]["message"]},
                {"role": "assistant", "content": "新的追问回答"},
            ]
            value = {"answer": "新的追问回答"}
        else:
            value = histories[task_id]
        return httpx.Response(200, json=value, request=httpx.Request(method, url))

    with patch("httpx.request", side_effect=respond):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "frontend/app.py"))
        app.query_params["task"] = "a" * 32
        app.run(timeout=20)
        assert not app.exception
        # Another session has added a conversation while this page remained open.
        histories["a" * 32] += [
            {"role": "user", "content": "另一个窗口的问题"},
            {"role": "assistant", "content": "另一个窗口的回答"},
        ]
        app.chat_input[0].set_value("为什么？").run(timeout=20)
        assert not app.exception
        assert app.session_state["history"] == histories["a" * 32]
        assert len(app.chat_message) == 6
        chat_panel = next(e for e in app.expander if e.label == "Agent 解释与追问记录")
        assert chat_panel.proto.expanded
        app.run(timeout=20)
        assert posts == ["为什么？"]
        assert len(app.chat_message) == 6
        app.query_params["task"] = "b" * 32
        app.run(timeout=20)
        assert not app.exception
        assert app.session_state["task_id"] == "b" * 32
        assert app.session_state["history"] == histories["b" * 32]
        assert app.session_state["report"]["manifest"]["task_id"] == "b" * 32
        assert len(app.chat_message) == 2
