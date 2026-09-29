"""五维评分公式及说明；由 Hadoop reducer 调用。"""

from collections import Counter

from processing.rules import WIDTHS

METHODS = {
    "Accurate": "值域与类型均合规的记录数 / 全部记录数 ×100。仅为准确性代理，不证明现实真实性。",
    "Complete": "已填写的必需字段数 / 全部记录应有字段数 ×100。空白、NULL、NA、N/A、? 算缺失。",
    "Unique": "可识别的不同业务键数 / 全部记录数 ×100。用户/电影按 ID，评分按用户+电影+时间；无法识别的键不计入分子。",
    "Up-to-date": "历史参照日前最近 recent_days 天内的合法评分时间数 / 全部评分记录数 ×100；用户和电影无更新时间，不单独评分。",
    "Consistent": "格式规范、类型值域合规、无同键冲突且引用可用维表实体的记录数 / 全部记录数 ×100。",
}
LIMITATIONS = [
    "用户属性为自报信息，未核验现实真实性；标题、电影年份和邮编也未接入外部权威库。",
    "时效性使用固定历史参照，不衡量数据对今天的适用性；较早的合法评分仍保留。",
    "隔离和去重会改变分母，分数提高不等于恢复了信息，必须结合保留率和处置数量解读。",
    "异常评分数量、评分集中、同名电影和不同时刻的评分不自动认定为错误。",
    "综合口径按记录或字段汇总，评分表规模大、占主要权重；同时提供各表指标，不设综合总分。",
]


def measure(record, rules, refs, has_conflict=False):
    count = Counter(rows=1, expected=WIDTHS[record.table], present=record.present)
    count["accurate"] = int(not record.errors)
    linked = record.table != "ratings" or (
        len(record.fields) == 4
        and record.fields[0] in refs["users"]
        and record.fields[1] in refs["movies"]
    )
    count["consistent"] = int(
        not record.errors and not has_conflict and linked and record.raw == record.normalized
    )
    if record.table == "ratings":
        count["rating_rows"] = 1
        count["recent"] = int(
            record.timestamp is not None
            and record.timestamp >= rules["reference_time"] - rules["recent_days"] * 86400
        )
        if record.timestamp is not None:
            split = (
                "train"
                if record.timestamp <= rules["T1"]
                else ("validation" if record.timestamp <= rules["T2"] else "test")
            )
            count[split] = 1
    return count


def scores(count):
    def ratio(top, bottom):
        denominator = count.get(bottom, 0)
        return round(100 * count.get(top, 0) / denominator, 4) if denominator else None

    return {
        "Accurate": ratio("accurate", "rows"),
        "Complete": ratio("present", "expected"),
        "Unique": ratio("unique", "rows"),
        "Up-to-date": ratio("recent", "rating_rows"),
        "Consistent": ratio("consistent", "rows"),
    }


def assessment(tables):
    total = Counter()
    for counts in tables.values():
        total.update(counts)
    return {
        "scores": scores(total),
        "counts": dict(total),
        "tables": {
            table: {"scores": scores(counts), "counts": dict(counts)}
            for table, counts in tables.items()
        },
    }
