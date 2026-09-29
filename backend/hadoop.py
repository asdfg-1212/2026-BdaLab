"""Hadoop 工具边界：准备输入、提交真实作业、提取输出；不在本机替代计算。"""

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from contextlib import ExitStack
from pathlib import Path

from backend.config import ROOT
from processing.rules import WIDTHS


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        digest.update(path.name.encode())
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def stage_input(data_dir, target):
    """只转码和封装来源，不校验、不清洗、不统计。"""
    with target.open("w", encoding="utf-8", newline="\n") as output:
        # 保证全空数据也启动 reducer，以输出 null 评分而不是伪造满分。
        output.write(json.dumps({"table": "_meta", "line": 0, "raw": ""}) + "\n")
        for table in WIDTHS:
            with (data_dir / f"{table}.dat").open(encoding="latin-1") as source:
                for number, line in enumerate(source, 1):
                    output.write(
                        json.dumps(
                            {"table": table, "line": number, "raw": line.rstrip("\r\n")},
                            ensure_ascii=True,
                        )
                        + "\n"
                    )


class HadoopTool:
    def __init__(self, settings):
        self.settings = settings

    def check(self):
        executable = self.settings.hadoop_home / "bin" / "hadoop"
        jars = list(
            (self.settings.hadoop_home / "share/hadoop/tools/lib").glob("hadoop-streaming-*.jar")
        )
        if not executable.is_file() or len(jars) != 1 or shutil.which("java") is None:
            raise RuntimeError(
                "Hadoop/Java 未就绪：请使用 Docker Compose 环境，或配置 HADOOP_HOME 与 Java。"
            )
        if os.name == "nt":
            raise RuntimeError(
                "Hadoop 工具需在 Linux 容器或 WSL 中运行；Windows 可运行前端和规则测试。"
            )
        return executable, jars[0]

    def run(self, task_dir, stage, mode, input_path, dimensions):
        executable, jar = self.check()
        output = task_dir / stage
        package = task_dir / "processing.zip"
        if not package.exists():
            with zipfile.ZipFile(package, "w") as archive:
                for file in sorted((ROOT / "processing").glob("*.py")):
                    archive.write(file, "processing/" + file.name)
        distributed = [
            package,
            task_dir / "rules.json",
            dimensions / "users.dat",
            dimensions / "movies.dat",
        ]
        command = [
            str(executable),
            "jar",
            str(jar),
            "-D",
            "mapreduce.framework.name=local",
            "-D",
            "fs.defaultFS=file:///",
            "-D",
            "mapreduce.job.reduces=1",
            "-D",
            "mapreduce.map.speculative=false",
            "-D",
            "mapreduce.reduce.speculative=false",
            "-files",
            ",".join(p.resolve().as_uri() + "#" + p.name for p in distributed),
            "-input",
            input_path.resolve().as_uri(),
            "-output",
            output.resolve().as_uri(),
            "-mapper",
            f"python3 -m processing.worker map {mode}",
            "-reducer",
            f"python3 -m processing.worker reduce {mode}",
            "-cmdenv",
            "PYTHONPATH=processing.zip",
            "-cmdenv",
            "PYTHONIOENCODING=utf-8",
        ]
        write_json(task_dir / f"{stage}-command.json", command)
        with (task_dir / f"{stage}.log").open("w", encoding="utf-8") as log:
            try:
                process = subprocess.run(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.settings.hadoop_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"{stage} 超时；参见 {stage}.log。未生成有效评估结果。"
                ) from error
        if process.returncode or not (output / "_SUCCESS").exists():
            raise RuntimeError(
                f"{stage} Hadoop 执行失败（exit={process.returncode}）；参见 {stage}.log。"
            )
        return output


def output_records(output_dir):
    for file in sorted(output_dir.glob("part-*")):
        with file.open(encoding="utf-8") as source:
            for line in source:
                kind, value = line.rstrip("\n").split("\t", 1)
                yield kind, json.loads(value)


def extract_assessment(output_dir):
    values = [value for kind, value in output_records(output_dir) if kind == "assessment"]
    if len(values) != 1:
        raise RuntimeError("评分输出不完整：本版本要求单 reducer 的唯一评估结果。")
    return values[0]


def extract_clean(output_dir, task_dir):
    clean = task_dir / "clean"
    clean.mkdir()
    examples = []
    disposition = None
    assessment = None
    with ExitStack() as stack:
        writers = {
            table: stack.enter_context(
                (clean / f"{table}.dat").open("w", encoding="latin-1", newline="\n")
            )
            for table in WIDTHS
        }
        audit = stack.enter_context((task_dir / "audit.jsonl").open("w", encoding="utf-8"))
        seen = set()
        for kind, value in output_records(output_dir):
            if kind == "clean":
                writers[value["table"]].write(value["raw"] + "\n")
            elif kind == "audit":
                audit.write(json.dumps(value, ensure_ascii=False) + "\n")
                category = (
                    value["table"],
                    value["action"],
                    tuple(value["reasons"]),
                    tuple(value["warnings"]),
                )
                if category not in seen and len(examples) < 24:
                    examples.append(value)
                    seen.add(category)
            elif kind == "disposition":
                if disposition is not None:
                    raise RuntimeError("清洗结果含有重复汇总。")
                disposition = value
            elif kind == "assessment":
                if assessment is not None:
                    raise RuntimeError("清洗结果含有重复评分。")
                assessment = value
    if disposition is None or assessment is None:
        raise RuntimeError("缺少 Hadoop 清洗汇总。")
    return clean, disposition, examples, assessment
