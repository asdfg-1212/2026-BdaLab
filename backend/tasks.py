"""单进程任务生命周期与文件持久化；不包含数据清洗逻辑。"""

import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend import agent
from backend.hadoop import read_json, write_json
from backend.pipeline import write_report

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.artifact_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="governance")
        self.lock = threading.RLock()
        self.chat_lock = threading.Lock()
        self.active = False
        for path in self.root.glob("*/task.json"):
            task = read_json(path)
            if task["status"] in {"queued", "running"}:
                task.update(
                    status="failed", error="服务中断，任务未完成。请重新发起；不会发布部分结果。"
                )
                write_json(path, task)

    def directory(self, task_id):
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise FileNotFoundError("任务不存在")
        directory = self.root / task_id
        if not (directory / "task.json").is_file():
            raise FileNotFoundError("任务不存在")
        return directory

    def get(self, task_id):
        with self.lock:
            return read_json(self.directory(task_id) / "task.json")

    def change(self, directory, **fields):
        with self.lock:
            task = read_json(directory / "task.json")
            task.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
            write_json(directory / "task.json", task)

    def submit(self, prompt):
        if not self.settings.llm_api_key.get_secret_value():
            raise ValueError("请先配置 .env 中的 LLM_API_KEY、LLM_BASE_URL 和 LLM_MODEL。")
        with self.lock:
            if self.active:
                raise ValueError("已有治理任务正在执行；本轮版本同一时间运行一个任务。")
            self.active = True
            task_id = uuid.uuid4().hex
            directory = self.root / task_id
            directory.mkdir()
            task = {
                "task_id": task_id,
                "status": "queued",
                "stage": "agent",
                "message": "Agent 正在理解请求",
                "prompt": prompt,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                write_json(directory / "task.json", task)
                self.executor.submit(self._run, directory, prompt)
            except Exception:
                self.active = False
                raise
            return task

    def _run(self, directory, prompt):
        def update(stage, message):
            self.change(directory, status="running", stage=stage, message=message)

        update("agent", "Agent 正在判断请求并调用工具")
        try:
            explanation = agent.govern(self.settings, directory, prompt, update)
            if not (directory / "report.json").exists():
                self.change(directory, status="rejected", stage="agent", message=explanation)
                return
            report = read_json(directory / "report.json")
            report["explanation"] = explanation
            write_report(directory, report)
            write_json(
                directory / "chat.json",
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": explanation},
                ],
            )
            self.change(
                directory, status="completed", stage="completed", message="清洗与评估已完成"
            )
        except Exception as error:
            logger.exception("任务 %s 执行异常", directory.name)
            # 评分文件仅在全部 Hadoop 步骤与守恒检查通过后生成。
            if (directory / "report.json").exists():
                self.change(
                    directory,
                    status="completed",
                    stage="completed",
                    message="Hadoop 清洗与评估已完成，Agent 解释生成失败，可查看报告并重试追问。",
                    explanation_error=type(error).__name__,
                )
            else:
                stage = self.get(directory.name)["stage"]
                if isinstance(error, (RuntimeError, FileNotFoundError)):
                    detail = str(error)
                else:
                    detail = {
                        "AuthenticationError": "模型服务认证失败，请检查 LLM_API_KEY。",
                        "APIConnectionError": "无法连接模型服务，请检查 LLM_BASE_URL 和网络。",
                        "APITimeoutError": "模型服务响应超时，请稍后重试。",
                        "RateLimitError": "模型服务限流或额度不足，请检查账户后重试。",
                        "BadRequestError": "模型服务不接受当前请求，请确认模型支持工具调用。",
                    }.get(type(error).__name__, f"执行异常：{type(error).__name__}，详见后端日志。")
                self.change(
                    directory,
                    status="failed",
                    error=detail,
                    message=f"{stage} 阶段失败，未生成有效报告。",
                )
        finally:
            with self.lock:
                self.active = False

    def report(self, task_id):
        if self.get(task_id)["status"] != "completed":
            raise ValueError("任务尚未完成，没有可发布的评估报告。")
        return read_json(self.directory(task_id) / "report.json")

    def chat(self, task_id, question):
        self.report(task_id)
        with self.chat_lock:
            directory = self.directory(task_id)
            path = directory / "chat.json"
            history = read_json(path) if path.exists() else []
            answer = agent.follow_up(self.settings, directory, question, history)
            history.extend(
                [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
            )
            write_json(path, history)
            return answer

    def history(self, task_id):
        self.report(task_id)
        path = self.directory(task_id) / "chat.json"
        return read_json(path) if path.exists() else []

    def close(self):
        self.executor.shutdown(wait=True)
