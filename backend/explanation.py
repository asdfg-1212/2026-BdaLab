"""从报告生成可核验的结果说明，不让模型重写数字或混淆前后阶段。"""


def summarize(report):
    def display(value):
        return "无法评价" if value is None else str(value)

    manifest = report["manifest"]
    lines = [
        f"任务 `{manifest['task_id']}` 的清洗与评估已完成。以下说明直接来自本次报告。",
        "",
        "| 维度 | 清洗前 | 清洗后 | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, before in report["before"]["scores"].items():
        after = report["after"]["scores"][name]
        lines.append(
            f"| {name} | {display(before)} | {display(after)} | "
            f"{display(report['delta'][name])} |"
        )
    lines += ["", "数据处置：修复保留可确定的信息；去重保留一条；隔离不等于修复。", ""]
    total_before = total_after = 0
    for table, actions in report["disposition"]["tables"].items():
        before = report["before"]["tables"][table]["counts"].get("rows", 0)
        after = report["after"]["tables"][table]["counts"].get("rows", 0)
        total_before += before
        total_after += after
        lines.append(
            f"- {table}：{before} → {after} 条；修复 {actions.get('repaired', 0)}，"
            f"去重 {actions.get('deduplicated', 0)}，隔离 {actions.get('quarantined', 0)}。"
        )
    retention = f"{total_after / total_before:.2%}" if total_before else "无法评价"
    lines += ["", f"总记录数：{total_before} → {total_after}；保留率：{retention}。"]
    reasons = report["disposition"].get("reasons", {})
    if reasons:
        lines += ["", "主要处置原因（同一记录可有多个原因，次数不能相加作为记录总数）：", ""]
        for code, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:8]:
            label = report.get("reason_labels", {}).get(code, code)
            lines.append(f"- {label}（{code}）：{count} 次。")
    lines += ["", "清洗后仍需关注（仅使用清洗后统计）：", ""]
    issues = report["after"]["issues"]
    warnings = report["after"]["warnings"]
    if not issues:
        lines.append("- 当前规则未发现遗留的结构化错误，不代表现实真实性已经得到证明。")
    for code, count in issues.items():
        lines.append(f"- 仍存在的问题 `{code}`：{count} 次。")
    for code, count in warnings.items():
        lines.append(f"- 待核验 `{code}`：{count} 次。")
    if not warnings:
        lines.append("- 当前规则未发现待核验告警。")
    lines += [
        "- Up-to-date 使用固定历史参照；users、movies 没有更新时间，不能称其时效性为满分。",
        "- 隔离和去重改变了分母，分数上升不代表信息恢复；旧的合法评分不会为提高时效分而删除。",
        "", "评价局限：", "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines += [
        "", f"规则版本：`{manifest['rule_version']}`；数据版本：`{manifest['data_version']}`。",
        f"T1={manifest['T1']}，T2={manifest['T2']}（Unix 秒，UTC）。",
    ]
    if manifest.get("splits"):
        splits = manifest["splits"]
        lines.append(
            f"训练/验证/测试评分数：{splits['train']} / {splits['validation']} / {splits['test']}。"
        )
    return "\n".join(lines)
