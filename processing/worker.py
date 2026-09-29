"""Hadoop Streaming 入口：map 分业务键，reduce 评分或清洗。"""

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter

from processing.quality import assessment, measure
from processing.rules import WIDTHS, conflict, parse, reference_ids


def emit(kind, value, output):
    output.write(kind + "\t" + json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")


def mapper(source, output, rules):
    for line in source:
        item = json.loads(line)
        if item["table"] == "_meta":
            emit("_meta", item, output)
            continue
        record = parse(item["table"], item["raw"], rules)
        key = record.key or "invalid:" + hashlib.sha256(item["raw"].encode()).hexdigest()
        emit(record.table + ":" + key, item, output)


def reducer(source, output, rules, refs, mode):
    counts = {table: Counter() for table in WIDTHS}
    actions = {table: Counter() for table in WIDTHS}
    evaluation_reasons = Counter()
    reasons = Counter()
    warnings = Counter()
    for _, lines in itertools.groupby(source, key=lambda line: line.split("\t", 1)[0]):
        items = [json.loads(line.split("\t", 1)[1]) for line in lines]
        if items[0]["table"] == "_meta":
            continue
        # 稳定保留最早来源行，不依赖 Hadoop 对相同 key 的 value 排序。
        items.sort(key=lambda item: item["line"])
        records = [parse(item["table"], item["raw"], rules) for item in items]
        table = records[0].table
        has_conflict = records[0].key is not None and conflict(records)
        counts[table]["unique"] += int(records[0].key is not None)
        retained = False
        for item, record in zip(items, records):
            counts[table].update(measure(record, rules, refs, has_conflict))
            warnings.update(record.warnings)
            problems = list(record.errors)
            if has_conflict:
                problems.append("key_conflict")
            if table == "ratings" and len(record.fields) == 4:
                if record.fields[0] not in refs["users"]:
                    problems.append("user_reference_unavailable")
                if record.fields[1] not in refs["movies"]:
                    problems.append("movie_reference_unavailable")
            evaluation_reasons.update(problems)
            if mode == "evaluate":
                continue
            if problems:
                action = "quarantined"
            elif retained:
                action, problems = "deduplicated", ["duplicate_business_record"]
            else:
                retained = True
                action = "repaired" if record.raw != record.normalized else "unchanged"
                if action == "repaired":
                    problems = ["canonical_format"]
                emit("clean", {"table": table, "raw": record.normalized}, output)
            actions[table][action] += 1
            reasons.update(problems)
            if action != "unchanged" or record.warnings:
                emit(
                    "audit",
                    {
                        **item,
                        "action": action,
                        "reasons": problems,
                        "warnings": record.warnings,
                        "cleaned": record.normalized if action == "repaired" else None,
                    },
                    output,
                )
    emit(
        "assessment",
        {**assessment(counts), "issues": dict(evaluation_reasons), "warnings": dict(warnings)},
        output,
    )
    if mode == "clean":
        emit(
            "disposition",
            {
                "tables": {t: dict(c) for t, c in actions.items()},
                "reasons": dict(reasons),
                "warnings": dict(warnings),
            },
            output,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["map", "reduce"])
    parser.add_argument("mode", choices=["evaluate", "clean"])
    args = parser.parse_args()
    # 这些文件由工具通过 Hadoop distributed cache 分发。
    with open("rules.json", encoding="utf-8") as source:
        rules = json.load(source)
    if args.stage == "map":
        mapper(sys.stdin, sys.stdout, rules)
    else:
        refs = {t: reference_ids(f"{t}.dat", t, rules) for t in ("users", "movies")}
        reducer(sys.stdin, sys.stdout, rules, refs, args.mode)


if __name__ == "__main__":
    main()
