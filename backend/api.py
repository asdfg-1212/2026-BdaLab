"""FastAPI 接口：参数校验、任务查询、追问及受限产物下载。"""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import settings
from backend.tasks import TaskManager


class Message(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@asynccontextmanager
async def lifespan(app):
    app.state.tasks = TaskManager(settings)
    yield
    app.state.tasks.close()


app = FastAPI(title="MovieLens 数据治理", lifespan=lifespan)


@app.exception_handler(FileNotFoundError)
async def not_found(request, error):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": "任务或产物不存在"})


@app.exception_handler(ValueError)
async def invalid_state(request, error):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content={"detail": str(error)})


@app.get("/health")
def health():
    return {"status": "ok", "model_configured": bool(settings.llm_api_key.get_secret_value())}


@app.post("/tasks", status_code=202)
def start(body: Message, request: Request):
    if not body.message.strip():
        raise HTTPException(422, "请求不能为空")
    return request.app.state.tasks.submit(body.message)


@app.get("/tasks/{task_id}")
def status(task_id: str, request: Request):
    return request.app.state.tasks.get(task_id)


@app.get("/tasks/{task_id}/report")
def report(task_id: str, request: Request):
    return request.app.state.tasks.report(task_id)


@app.post("/tasks/{task_id}/chat")
def chat(task_id: str, body: Message, request: Request):
    try:
        return {"answer": request.app.state.tasks.chat(task_id, body.message)}
    except (FileNotFoundError, ValueError):
        raise
    except Exception as error:
        raise HTTPException(
            502, f"Agent 追问失败（{type(error).__name__}），请检查模型服务后重试。"
        ) from error


@app.get("/tasks/{task_id}/chat")
def chat_history(task_id: str, request: Request):
    return request.app.state.tasks.history(task_id)


@app.get("/tasks/{task_id}/files/{name}")
def download(
    task_id: str,
    name: Literal[
        "report.json",
        "report.md",
        "manifest.json",
        "audit.jsonl",
        "users.dat",
        "movies.dat",
        "ratings.dat",
        "before.log",
        "cleaning.log",
        "after.log",
    ],
    request: Request,
):
    manager = request.app.state.tasks
    directory = manager.directory(task_id)
    if not name.endswith(".log"):
        manager.report(task_id)
    path = directory / "clean" / name if name.endswith(".dat") else directory / name
    if not path.is_file():
        raise FileNotFoundError(name)
    media_type = "text/markdown; charset=utf-8" if name == "report.md" else "application/octet-stream"
    return FileResponse(path, filename=name, media_type=media_type)
