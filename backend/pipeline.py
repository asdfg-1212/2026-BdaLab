"""确定性的任务流程：冻结输入 → 前评估 → 清洗 → 后评估 → 登记产物。"""

import shutil
from datetime import datetime, timezone

from backend.config import ROOT
from backend.hadoop import (
    HadoopTool,
    extract_assessment,
    extract_clean,
    fingerprint,
    read_json,
    stage_input,
    write_json,
)
from processing.quality import LIMITATIONS, METHODS
from processing.rules import REASONS, WIDTHS


def markdown_report(report):
    """把同一份真实报告渲染为便于提交和阅读的 Markdown。"""
    manifest = report["manifest"]
    lines = [
        "# MovieLens 数据治理评估报告",
        "",
        f"- 任务标识：`{manifest['task_id']}`",
        f"- 原始数据版本：`{manifest['raw_data_version']}`",
        f"- 清洗后数据版本：`{manifest['data_version']}`",
        f"- 规则版本：`{manifest['rule_version']}`",
        f"- 时间边界：T1=`{manifest['T1']}`，T2=`{manifest['T2']}`（UTC）",
        "",
        "## 五维评分",
        "",
        "| 维度 | 清洗前 | 清洗后 | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, before in report["before"]["scores"].items():
        after = report["after"]["scores"][name]
        delta = report["delta"][name]
        lines.append(f"| {name} | {before} | {after} | {delta} |")

    lines.extend(["", "## 数据处置", "", "| 数据表 | 清洗前 | 清洗后 | 修复 | 去重 | 隔离 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]) 
    for table, actions in report["disposition"]["tables"].items():
        before = report["before"]["tables"][table]["counts"].get("rows", 0)
        after = report["after"]["tables"][table]["counts"].get("rows", 0)
        lines.append(
            f"| {table} | {before} | {after} | {actions.get('repaired', 0)} | "
            f"{actions.get('deduplicated', 0)} | {actions.get('quarantined', 0)} |"
        )

    lines.extend(["", "## 评分方法", ""])
    lines.extend(f"- **{name}**：{method}" for name, method in report["methods"].items())
    lines.extend(["", "## 局限", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(["", "## 清洗后仍需关注", ""])
    issues = report["after"]["issues"]
    warnings = report["after"]["warnings"]
    if not issues and not warnings:
        lines.append("- 当前规则未发现遗留的结构化问题；这不代表外部真实性已经得到证明。")
    else:
        lines.extend(f"- 问题 `{name}`：{count}" for name, count in issues.items())
        lines.extend(f"- 待核验 `{name}`：{count}" for name, count in warnings.items())
    if report.get("explanation"):
        lines.extend(["", "## Agent 解释", "", report["explanation"]])
    return "\n".join(lines) + "\n"


def write_report(task_dir, report):
    write_json(task_dir / "report.json", report)
    (task_dir / "report.md").write_text(markdown_report(report), encoding="utf-8")


def execute(settings, task_dir, update, expected_raw_version="configured"):
    update("checking", "检查数据、Hadoop 和默认规则")
    tool = HadoopTool(settings)
    tool.check()
    raw = task_dir / "raw"
    raw.mkdir()
    for table in WIDTHS:
        source = settings.data_dir / f"{table}.dat"
        if not source.is_file():
            raise RuntimeError(f"缺少原始数据文件：{source.name}")
        shutil.copyfile(source, raw / source.name)
    shutil.copyfile(ROOT / "config/rules.json", task_dir / "rules.json")
    rules = read_json(task_dir / "rules.json")
    if not rules["history_start"] < rules["T1"] < rules["T2"] < rules["reference_time"]:
        raise RuntimeError("规则时间边界必须满足 history_start < T1 < T2 < reference_time。")
    if rules["recent_days"] <= 0:
        raise RuntimeError("recent_days 必须为正数。")
    raw_version = fingerprint(list(raw.glob("*.dat")))
    if expected_raw_version not in {"configured", raw_version}:
        raise RuntimeError("请求的原始数据版本与配置的数据不一致，已停止任务。")
    update("cleaning", "Hadoop：清洗前评分并执行规范化、去重与隔离")
    stage_input(raw, task_dir / "before-input.jsonl")
    output = tool.run(task_dir, "cleaning", "clean", task_dir / "before-input.jsonl", raw)
    clean, disposition, examples, before = extract_clean(output, task_dir)
    update("after", "Hadoop：使用相同口径对清洗结果重新评分")
    stage_input(clean, task_dir / "after-input.jsonl")
    after = extract_assessment(
        tool.run(task_dir, "after", "evaluate", task_dir / "after-input.jsonl", clean)
    )
    for table in WIDTHS:
        actions = disposition["tables"][table]
        before_rows = before["tables"][table]["counts"].get("rows", 0)
        after_rows = after["tables"][table]["counts"].get("rows", 0)
        if before_rows != sum(actions.values()) or after_rows != (
            actions.get("unchanged", 0) + actions.get("repaired", 0)
        ):
            raise RuntimeError(f"{table} 处置数量不守恒，停止登记数据版本。")
    update("reporting", "保存数据版本、时间边界和真实评估报告")
    splits = {k: after["counts"].get(k, 0) for k in ("train", "validation", "test")}
    manifest = {
        "task_id": task_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "raw_data_version": raw_version,
        "data_version": fingerprint(list(clean.glob("*.dat"))),
        "rule_version": rules["version"],
        "rule_fingerprint": fingerprint([task_dir / "rules.json", task_dir / "processing.zip"]),
        "T1": rules["T1"],
        "T2": rules["T2"],
        "timezone": "UTC",
        "splits": splits,
        "split_definition": "train: timestamp<=T1; validation: T1<timestamp<=T2; test: timestamp>T2",
        "ready_for_next_iteration": all(splits.values()),
        "encoding": "ISO-8859-1",
        "separator": "::",
        "header": False,
        "engine": "Hadoop Streaming / LocalJobRunner / 1 reducer",
    }
    report = {
        "manifest": manifest,
        "rules": rules,
        "methods": METHODS,
        "limitations": LIMITATIONS,
        "before": before,
        "after": after,
        "delta": {
            name: round(after["scores"][name] - score, 4)
            if score is not None and after["scores"][name] is not None
            else None
            for name, score in before["scores"].items()
        },
        "disposition": disposition,
        "examples": examples,
        "reason_labels": REASONS,
        "samples": {},
    }
    for table in WIDTHS:
        with (clean / f"{table}.dat").open(encoding="latin-1") as source:
            report["samples"][table] = [line.rstrip("\n") for _, line in zip(range(5), source)]
    write_json(task_dir / "manifest.json", manifest)
    write_report(task_dir, report)
    return report
