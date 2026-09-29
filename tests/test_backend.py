"""接口、任务状态、Agent 工具协议；模型响应只在测试内模拟。"""

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from backend import agent, api
from backend.config import Settings
from backend.hadoop import HadoopTool, read_json, stage_input, write_json
from backend.pipeline import execute
from backend.tasks import TaskManager


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        artifact_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data",
        hadoop_home=tmp_path / "missing-hadoop",
        llm_api_key="test-only",
    )


def test_missing_hadoop_cannot_succeed(settings):
    with pytest.raises(RuntimeError, match="Hadoop/Java"):
        HadoopTool(settings).check()


def test_api_rejects_blank_missing_key_and_unknown_task(settings):
    settings = Settings(_env_file=None, artifact_dir=settings.artifact_dir, llm_api_key="")
    with patch.object(api, "settings", settings), TestClient(api.app) as client:
        assert client.get("/health").json()["model_configured"] is False
        assert client.post("/tasks", json={"message": " "}).status_code == 422
        assert client.post("/tasks", json={"message": "清洗"}).status_code == 409
        assert client.get("/tasks/unknown").status_code == 404


def test_failed_task_has_no_report_and_cannot_download(settings):
    with (
        patch.object(api, "settings", settings),
        patch.object(agent, "govern", side_effect=RuntimeError("Hadoop 不可用")),
        TestClient(api.app) as client,
    ):
        task = client.post("/tasks", json={"message": "清洗"}).json()
        manager = api.app.state.tasks
        manager.executor.shutdown(wait=True)
        result = client.get(f"/tasks/{task['task_id']}").json()
        assert result["status"] == "failed"
        assert "Hadoop" in result["error"]
        assert client.get(f"/tasks/{task['task_id']}/report").status_code == 409
        assert client.get(f"/tasks/{task['task_id']}/files/ratings.dat").status_code == 409
        assert client.get(f"/tasks/{task['task_id']}/files/task.json").status_code == 422


def test_concurrent_submit_and_restart(settings):
    started, release = threading.Event(), threading.Event()

    def wait(*args):
        started.set()
        assert release.wait(5)
        return "不支持该任务"

    manager = TaskManager(settings)
    try:
        with patch.object(agent, "govern", side_effect=wait):
            task = manager.submit("清洗")
            assert started.wait(5)
            with pytest.raises(ValueError, match="正在执行"):
                manager.submit("再次清洗")
            release.set()
            manager.close()
        assert manager.get(task["task_id"])["status"] == "rejected"
        manager.change(manager.directory(task["task_id"]), status="running")
        restarted = TaskManager(settings)
        assert restarted.get(task["task_id"])["status"] == "failed"
        restarted.close()
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize("call_count", [1, 2])
def test_agent_really_calls_bound_tool(settings, tmp_path, call_count):
    requests = []

    def execute_once(config, directory, update, version):
        report = {"manifest": {"raw_data_version": "test-only-snapshot"}}
        write_json(directory / "report.json", report)
        return report

    def model_response(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_test_{number}",
                        "type": "function",
                        "function": {"name": "run_governance", "arguments": "{}"},
                    }
                    for number in range(call_count)
                ],
            }
            finish = "tool_calls"
        else:
            assert any(message["role"] == "tool" for message in body["messages"])
            message, finish = {"role": "assistant", "content": "工具已返回实际报告。"}, "stop"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(model_response)) as client:
        sdk = OpenAI(
            api_key="test",
            base_url="https://model.invalid/v1",
            http_client=client,
            max_retries=0,
        )
        with (
            patch.object(agent, "model", return_value=sdk),
            patch.object(agent, "execute", side_effect=execute_once) as tool,
        ):
            answer = agent.govern(settings, tmp_path, "清洗并评估", lambda *args: None)
        tool.assert_called_once()
        assert len(requests) == 2
        assert "实际报告" in answer


def test_stage_preserves_encoding_and_adds_empty_sentinel(tmp_path):
    for table in ("users", "movies", "ratings"):
        (tmp_path / f"{table}.dat").write_text(
            "1::Amélie (2001)::Drama\n" if table == "movies" else "", encoding="latin-1"
        )
    stage_input(tmp_path, tmp_path / "input.jsonl")
    rows = [json.loads(line) for line in (tmp_path / "input.jsonl").read_text().splitlines()]
    assert rows[0]["table"] == "_meta"
    assert rows[1]["raw"] == "1::Amélie (2001)::Drama"
    assert rows[1]["line"] == 1


def test_agent_explanation_failure_preserves_completed_report(settings):
    def execution_then_failure(config, directory, prompt, update):
        write_json(directory / "report.json", {"evidence": "test-only"})
        raise RuntimeError("model unavailable")

    with patch.object(agent, "govern", side_effect=execution_then_failure):
        manager = TaskManager(settings)
        task = manager.submit("清洗")
        manager.close()
    assert manager.get(task["task_id"])["status"] == "completed"
    assert "explanation_error" in manager.get(task["task_id"])


@pytest.mark.skipif(os.getenv("RUN_HADOOP_TESTS") != "1", reason="需要真实 Linux Hadoop 环境")
def test_real_hadoop_pipeline(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path,
        hadoop_home=Path(os.environ.get("HADOOP_HOME", "/opt/hadoop")),
    )
    settings.data_dir.mkdir()
    data = {
        "users": "1::F::18::0::00123\n",
        "movies": "1::Amélie (2001)::Drama\n",
        "ratings": "1::1::4::978307199\n1::1::4::978307199\n1::1::5::1009843199\n1::1::4::1030000000\n",
    }
    for table, content in data.items():
        (settings.data_dir / f"{table}.dat").write_text(content, encoding="latin-1")
    directory = tmp_path / "job"
    directory.mkdir()
    report = execute(settings, directory, lambda *args: None)
    assert report["after"]["counts"]["rows"] == 5
    assert report["disposition"]["tables"]["ratings"]["deduplicated"] == 1
    assert report["manifest"]["splits"] == {"train": 1, "validation": 1, "test": 1}
    assert read_json(directory / "report.json") == report
