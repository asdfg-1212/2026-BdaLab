"""共享规则：解析、无歧义规范化、校验和同键冲突判定。"""

import re
from dataclasses import dataclass

WIDTHS = {"users": 5, "movies": 3, "ratings": 4}
AGES = {1, 18, 25, 35, 45, 50, 56}
GENRES = {
    x.casefold(): x
    for x in (
        "Action",
        "Adventure",
        "Animation",
        "Children's",
        "Comedy",
        "Crime",
        "Documentary",
        "Drama",
        "Fantasy",
        "Film-Noir",
        "Horror",
        "Musical",
        "Mystery",
        "Romance",
        "Sci-Fi",
        "Thriller",
        "War",
        "Western",
    )
}
MISSING = {"", "null", "n/a", "na", "?"}
REASONS = {
    "field_count": "字段数量不符合表结构",
    "missing_field": "必需字段缺失",
    "id_range": "用户或电影标识超出数据集范围",
    "movie_id_range": "电影标识超出范围",
    "control_character": "包含不可见控制字符",
    "gender_domain": "性别编码非法",
    "age_domain": "年龄类别编码非法",
    "occupation_domain": "职业编码非法",
    "genre_domain": "电影类型未在允许列表中",
    "rating_domain": "评分不是 1—5 的整数",
    "timestamp_range": "时间戳不是合法历史范围内的秒数",
    "key_conflict": "同一业务键存在不同内容，无法判定真实值，整组隔离",
    "user_reference_unavailable": "引用的用户不存在或用户资料已被隔离",
    "movie_reference_unavailable": "引用的电影不存在或电影资料已被隔离",
    "duplicate_business_record": "规范化后业务记录重复，保留来源行号最小的一条",
    "canonical_format": "去除多余空白、统一类别大小写或数值/类型列表格式",
    "zip_unverified": "邮编不是常见美国格式，保留原字符串，真实性待核验",
    "title_year_unverified": "标题末尾年份无法识别，保留记录，待外部核验",
    **{f"integer_field_{i}": f"第 {i + 1} 个字段不能解析为非负整数" for i in range(4)},
}


@dataclass
class Record:
    table: str
    raw: str
    fields: list[str]
    key: str | None
    errors: list[str]
    warnings: list[str]
    present: int
    timestamp: int | None = None

    @property
    def normalized(self):
        return "::".join(self.fields)


def integer(value):
    # 不推测浮点、科学计数法或毫秒时间戳。
    return int(value) if re.fullmatch(r"[0-9]{1,12}", value) else None


def parse(table, raw, rules):
    fields = raw.split("::")
    present = sum(x.strip().casefold() not in MISSING for x in fields[: WIDTHS[table]])
    fields = [x.strip() for x in fields]
    record = Record(table, raw, fields, None, [], [], present)
    if len(fields) != WIDTHS[table]:
        record.errors.append("field_count")
        return record
    if present != WIDTHS[table]:
        record.errors.append("missing_field")
    numeric = {"users": (0, 2, 3), "movies": (0,), "ratings": (0, 1, 2, 3)}[table]
    values = {i: integer(fields[i]) for i in numeric}
    for i, value in values.items():
        if value is None:
            record.errors.append(f"integer_field_{i}")
        else:
            fields[i] = str(value)
    identifier = values[0]
    limit = rules["max_movie_id" if table == "movies" else "max_user_id"]
    if identifier is None or not 1 <= identifier <= limit:
        record.errors.append("id_range")
    else:
        record.key = str(identifier)
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in raw):
        record.errors.append("control_character")
    if table == "users":
        fields[1] = fields[1].upper()
        if fields[1] not in {"M", "F"}:
            record.errors.append("gender_domain")
        if values[2] not in AGES:
            record.errors.append("age_domain")
        if values[3] is None or not 0 <= values[3] <= 20:
            record.errors.append("occupation_domain")
        if not re.fullmatch(r"[0-9]{5}(-[0-9]{4})?", fields[4]):
            # 非美国邮编不直接认定为错误；保留字符串及证据。
            record.warnings.append("zip_unverified")
    elif table == "movies":
        fields[1] = " ".join(fields[1].split())
        genres = [GENRES.get(x.strip().casefold()) for x in fields[2].split("|")]
        if any(x is None for x in genres):
            record.errors.append("genre_domain")
        else:
            fields[2] = "|".join(sorted(set(genres)))
        if not re.search(r"\([0-9]{4}\)$", fields[1]):
            record.warnings.append("title_year_unverified")
    else:
        if values[1] is None or not 1 <= values[1] <= rules["max_movie_id"]:
            record.errors.append("movie_id_range")
            record.key = None
        if values[2] is None or not 1 <= values[2] <= 5:
            record.errors.append("rating_domain")
        timestamp = values[3]
        if timestamp is None or not rules["history_start"] <= timestamp <= rules["reference_time"]:
            record.errors.append("timestamp_range")
        else:
            record.timestamp = timestamp
        if record.key is not None and timestamp is not None:
            record.key = f"{fields[0]}:{fields[1]}:{fields[3]}"
        else:
            record.key = None
    return record


def conflict(records):
    """同一业务键的规范化内容不同，无法判断谁真，整组隔离。"""
    return len({r.normalized for r in records}) > 1


def reference_ids(path, table, rules):
    """仅在 Hadoop worker 中读取小维表；与主清洗使用相同判定。"""
    groups = {}
    with open(path, encoding="latin-1") as source:
        for line in source:
            record = parse(table, line.rstrip("\r\n"), rules)
            if record.key is not None:
                groups.setdefault(record.key, []).append(record)
    return {
        key
        for key, rows in groups.items()
        if not conflict(rows) and any(not row.errors for row in rows)
    }
