"""Streamlit 页面：只调用后端，不读取数据目录、不自行计算质量指标。"""

import os

import httpx
import streamlit as st

API = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
PUBLIC_API = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000").rstrip("/")
ACTIVE = {"queued", "running"}

st.set_page_config(page_title="MovieLens 数据治理", page_icon="🎬", layout="wide")


def request(method, path, **kwargs):
    try:
        response = httpx.request(method, API + path, timeout=210, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        try:
            detail = error.response.json().get("detail", error.response.text)
        except ValueError:
            detail = error.response.text
        st.error(str(detail))
    except httpx.RequestError:
        st.error("无法连接后端或请求超时，请检查后端服务。")
    return None


def reset():
    for key in ("task_id", "task_status", "report", "history"):
        st.session_state.pop(key, None)
    st.query_params.clear()


task_id = st.query_params.get("task") or st.session_state.get("task_id")
if task_id != st.session_state.get("task_id"):
    for key in ("task_status", "report", "history"):
        st.session_state.pop(key, None)
title, action = st.columns([12, 1], vertical_alignment="center")
with title:
    st.title("MovieLens 数据治理 Agent")
    st.caption("Hadoop 清洗与五维质量评估 · 第一轮迭代")
with action:
    if task_id:
        st.button(
            "＋",
            help="发起新的治理任务",
            on_click=reset,
            disabled=st.session_state.get("task_status") in ACTIVE,
            use_container_width=True,
        )

if task_id:
    st.session_state.task_id = task_id
    st.caption(f"任务标识：{task_id}")


@st.fragment(
    run_every=5 if task_id and st.session_state.get("task_status", "queued") in ACTIVE else None
)
def progress():
    if not task_id:
        return
    task = request("GET", f"/tasks/{task_id}")
    if task is None:
        return
    previous = st.session_state.get("task_status")
    st.session_state.task_status = task["status"]
    if task["status"] in ACTIVE:
        st.info(f"正在执行 · {task['message']}")
        stages = ["agent", "checking", "cleaning", "after", "reporting"]
        stage = task["stage"]
        st.progress(stages.index(stage) / len(stages) if stage in stages else 0)
    elif task["status"] == "completed":
        st.success(task["message"])
    else:
        st.error(task.get("error", task["message"]))
        st.caption(f"停止环节：{task['stage']}")
        for stage in ("cleaning", "after"):
            st.link_button(
                f"{stage} 作业日志（已生成时可下载）",
                f"{PUBLIC_API}/tasks/{task_id}/files/{stage}.log",
            )
    if previous != task["status"] and task["status"] not in ACTIVE:
        st.rerun()


progress()

if task_id and st.session_state.get("task_status") == "completed":
    if "report" not in st.session_state:
        report = request("GET", f"/tasks/{task_id}/report")
        if report is not None:
            st.session_state.report = report
    report = st.session_state.get("report")
    if report:
        st.subheader("五维评分对比")
        score_rows = [
            {
                "维度": name,
                "清洗前": before,
                "清洗后": report["after"]["scores"][name],
                "变化": report["delta"][name],
            }
            for name, before in report["before"]["scores"].items()
        ]
        st.dataframe(score_rows, hide_index=True, use_container_width=True)
        st.caption("分值范围 0—100；空值表示无法评价。分数提升须结合数据保留情况解读。")

        with st.expander("评分依据与局限", expanded=False):
            st.markdown("#### 评分方法")
            for name, method in report["methods"].items():
                st.markdown(f"**{name}**：{method}")
            st.divider()
            st.markdown("#### 适用范围与局限")
            for limitation in report["limitations"]:
                st.markdown(f"- {limitation}")

        st.subheader("数据处置结果")
        disposition_rows = []
        for table, actions in report["disposition"]["tables"].items():
            before = report["before"]["tables"][table]["counts"].get("rows", 0)
            after = report["after"]["tables"][table]["counts"].get("rows", 0)
            disposition_rows.append(
                {
                    "数据表": table,
                    "清洗前": before,
                    "清洗后": after,
                    "保留率": f"{after / before:.2%}" if before else "无法评价",
                    "保持原样": actions.get("unchanged", 0),
                    "修复后保留": actions.get("repaired", 0),
                    "去重": actions.get("deduplicated", 0),
                    "隔离": actions.get("quarantined", 0),
                }
            )
        st.dataframe(disposition_rows, hide_index=True, use_container_width=True)
        st.caption("清洗前 = 保持原样 + 修复后保留 + 去重 + 隔离；清洗后 = 保持原样 + 修复后保留。")

        with st.expander("问题记录与处理原因", expanded=False):
            reason_rows = [
                {
                    "原因代码": code,
                    "说明": report["reason_labels"].get(code, code),
                    "记录次数": count,
                }
                for code, count in report["disposition"]["reasons"].items()
            ]
            if reason_rows:
                st.dataframe(reason_rows, hide_index=True, use_container_width=True)
            if report["examples"]:
                st.markdown("#### 代表性记录")
                st.json(report["examples"])
            st.markdown("#### 清洗后仍需关注")
            st.json(
                {
                    "仍存在的问题": report["after"]["issues"],
                    "待核验信息": report["after"]["warnings"],
                }
            )
            st.caption("一条记录可能对应多个原因，原因次数不能直接相加作为记录总数。")

        history = request("GET", f"/tasks/{task_id}/chat")
        if history is not None:
            st.session_state.history = history
        with st.expander("Agent 解释与追问记录", expanded=True):
            history = st.session_state.get("history", [])
            if history:
                for message in history:
                    with st.chat_message(message["role"]):
                        st.write(message["content"])
            else:
                st.write(report.get("explanation", "Agent 解释尚未生成，可以在下方继续追问。"))

        st.subheader("结果获取")
        with st.expander("数据版本与时间边界", expanded=False):
            st.json(report["manifest"])
            st.caption("后续迭代须使用同一数据版本及 T1/T2；时间戳单位为秒，时区 UTC。")
            st.link_button(
                "下载版本清单 manifest.json",
                f"{PUBLIC_API}/tasks/{task_id}/files/manifest.json",
            )
        with st.expander("查看清洗后的数据样例", expanded=False):
            for table, sample in report["samples"].items():
                st.markdown(f"**{table}.dat**")
                st.code("\n".join(sample), language="text")
        downloads = st.columns(5)
        files = (
            ("Markdown 评估报告", "report.md"),
            ("users.dat", "users.dat"),
            ("movies.dat", "movies.dat"),
            ("ratings.dat", "ratings.dat"),
            ("审计记录", "audit.jsonl"),
        )
        for column, (label, name) in zip(downloads, files):
            column.link_button(label, f"{PUBLIC_API}/tasks/{task_id}/files/{name}")

        st.caption("↳ 下方输入框用于继续追问本次治理结果，不会创建新的治理任务。")

prompt = st.chat_input(
    "例如：请使用默认规则清洗 MovieLens 1M，评估五维质量并解释仍未解决的问题。"
    if not task_id
    else "继续追问本次结果，例如：为什么 Unique 分数发生变化？",
    disabled=bool(task_id and st.session_state.get("task_status") != "completed"),
)
if prompt:
    with st.spinner("Agent 正在处理请求…"):
        if not task_id:
            task = request("POST", "/tasks", json={"message": prompt})
            if task:
                st.session_state.task_id = task["task_id"]
                st.session_state.task_status = task["status"]
                st.query_params["task"] = task["task_id"]
                st.rerun()
        else:
            answer = request("POST", f"/tasks/{task_id}/chat", json={"message": prompt})
            if answer:
                st.session_state.pop("history", None)
                st.rerun()
