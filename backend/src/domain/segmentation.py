"""按逐日灯组单量，找出片区报修格局从哪天开始变化。"""

import hashlib
import json
import math
from datetime import date, timedelta
from fractions import Fraction

from .analysis import validate_rows
from .spec import SPEC

SEGMENT = SPEC["segment"]
MIN_DAYS = SEGMENT["min_days"]
_REGISTER, _INCIDENT = SPEC["fields"][0]["options"]
_CHECKPOINT_VERSION = 1


class Interrupted(Exception):
    """租约失效或已有更新的执行者接管，当前计算应停止并保留检查点。"""


def _iso(value, label):
    if isinstance(value, str):
        try:
            if date.fromisoformat(value).isoformat() == value:
                return value
        except ValueError:
            pass
    raise ValueError(f"{label}须使用有效的 YYYY-MM-DD 日期。")


def _options(params):
    if not isinstance(params, dict):
        raise ValueError("格局分段参数格式有误。")
    options = {}
    for field in SEGMENT["params"]:
        key, label, kind = field["key"], field["label"], field["type"]
        value = params.get(key, field["default"])
        if kind in ("int", "number"):
            if type(value) not in (int, float) or isinstance(value, bool):
                raise ValueError(f"「{label}」须填写数字。")
            if not math.isfinite(value) or not field["min"] <= value <= field["max"]:
                raise ValueError(f"「{label}」须在 {field['min']} 至 {field['max']} 之间。")
        elif kind == "date":
            if value in (None, ""):
                raise ValueError(f"「{label}」不能为空。")
            value = _iso(value, label)
        else:
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError(f"「{label}」须填写文本。")
            value = value.strip()
        options[key] = value
    if options["start_date"] > options["end_date"]:
        raise ValueError("统计起日须早于或等于统计止日。")
    return options


def _matrix(records, district, start, end):
    sites = sorted(
        (row for row in records
         if row["record_type"] == _REGISTER and (not district or row["district"] == district)),
        key=lambda row: row["site_id"],
    )
    if not sites:
        raise ValueError(f"台账中没有「{district}」的灯组，请检查片区名称。")
    days = (end - start).days + 1
    if days < MIN_DAYS:
        raise ValueError(f"所选日期只有 {days} 天，不足 {MIN_DAYS} 天，无法分段。")
    position = {row["site_id"]: index for index, row in enumerate(sites)}
    counts = [[0] * days for _ in sites]
    for row in records:
        if row["record_type"] != _INCIDENT or row["site_id"] not in position:
            continue
        reported = date.fromisoformat(row["report_date"])
        if start <= reported <= end:
            counts[position[row["site_id"]]][(reported - start).days] += 1
    return sites, counts, days


def _frozen_cuts(snapshot, start, days):
    """读取发起运行时冻结的已交班切点，换算为日期偏移。"""
    if not isinstance(snapshot, dict):
        return set()
    cuts = set()
    for raw in snapshot.get("frozen_cuts") or []:
        if not isinstance(raw, str):
            continue
        try:
            stamp = date.fromisoformat(raw)
        except ValueError:
            continue
        offset = (stamp - start).days
        if 0 < offset < days:
            cuts.add(offset)
    return cuts


def _fingerprint(rows, options, frozen):
    payload = json.dumps(
        {"v": _CHECKPOINT_VERSION, "rows": rows, "params": options, "cuts": sorted(frozen)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _number(value):
    value = Fraction(value)
    return int(value) if value.denominator == 1 else round(float(value), 4)


def _twice_deviation(histogram, size):
    """单组窗口内每日单量与中位数绝对差之和的两倍（整数，避免舍入）。"""
    if not size:
        return 0
    lower, upper = (size - 1) // 2, size // 2
    running, first, median_twice = 0, None, None
    for value in sorted(histogram):
        running += histogram[value]
        if first is None and running > lower:
            first = value
        if running > upper:
            median_twice = first + value if size % 2 == 0 else 2 * value
            break
    if median_twice is None:
        return 0
    return sum(frequency * abs(2 * value - median_twice) for value, frequency in histogram.items())


def _load_checkpoint(checkpoint, fingerprint, days):
    stored = {"costs": {}, "processed": 0}
    if isinstance(checkpoint, dict) and checkpoint.get("fingerprint") == fingerprint:
        raw = checkpoint.get("costs")
        if isinstance(raw, dict):
            for key, value in raw.items():
                try:
                    left, right = (int(part) for part in key.split(","))
                    cost = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= left < right <= days:
                    stored["costs"][f"{left},{right}"] = cost
        try:
            stored["processed"] = max(0, min(int(checkpoint.get("processed", 0)), days))
        except (TypeError, ValueError):
            stored["processed"] = 0
    return stored


def _chunk_options(costs, origin, length):
    """区间内每种段数的最优拟合成本与切点，同成本取最早切点。"""
    best = {(length, 0): (0, None)}
    for start in range(length - MIN_DAYS, -1, -1):
        for parts in range(1, (length - start) // MIN_DAYS + 1):
            choice = None
            for cut in range(start + MIN_DAYS, length - (parts - 1) * MIN_DAYS + 1):
                suffix = best.get((cut, parts - 1))
                if suffix is None:
                    continue
                candidate = costs[(origin + start, origin + cut)] + suffix[0]
                if choice is None or candidate < choice[0]:
                    choice = (candidate, cut)
            if choice is not None:
                best[(start, parts)] = choice
    options = {}
    for parts in range(1, length // MIN_DAYS + 1):
        state = best.get((0, parts))
        if state is None:
            continue
        cuts, position = [], 0
        for remaining in range(parts, 1, -1):
            cut = best[(position, remaining)][1]
            cuts.append(origin + cut)
            position = cut
        options[parts] = (state[0], tuple(cuts))
    return options


def analyze(rows, params, snapshot=None, checkpoint=None, progress=None):
    """返回分段结果；checkpoint 提供已算完的日期前缀，progress 保存进度并检测接管。"""
    records = validate_rows(rows)
    options = _options(params)
    district = options["district_scope"]
    start, end = date.fromisoformat(options["start_date"]), date.fromisoformat(options["end_date"])
    penalty = Fraction(str(options["segment_penalty"]))
    sites, counts, days = _matrix(records, district, start, end)
    frozen = _frozen_cuts(snapshot, start, days)

    fingerprint = _fingerprint(rows, options, frozen)
    checkpoint = _load_checkpoint(checkpoint, fingerprint, days)
    checkpoint["fingerprint"] = fingerprint
    resumed_from = checkpoint["processed"]

    bounds = sorted({0, days, *frozen})
    chunk_end = {}
    for left, right in zip(bounds, bounds[1:]):
        if right - left < MIN_DAYS:
            stamp = (start + timedelta(days=right)).isoformat()
            raise ValueError(f"已交班切点 {stamp} 与所选日期冲突：每段至少 {MIN_DAYS} 天，请调整日期范围。")
        for day in range(left, right):
            chunk_end[day] = right

    costs = {}
    for key, value in checkpoint["costs"].items():
        left, right = (int(part) for part in key.split(","))
        if left < checkpoint["processed"]:
            costs[(left, right)] = value
    step = max(1, days // 20)
    for left in range(checkpoint["processed"], days):
        histograms = [dict() for _ in counts]
        for right in range(left + 1, chunk_end[left] + 1):
            for position, series in enumerate(counts):
                value = series[right - 1]
                histograms[position][value] = histograms[position].get(value, 0) + 1
            size = right - left
            if size >= MIN_DAYS:
                costs[(left, right)] = sum(_twice_deviation(histogram, size) for histogram in histograms)
        if progress and ((left + 1) % step == 0 or left + 1 == days):
            alive = progress({
                "version": _CHECKPOINT_VERSION, "fingerprint": fingerprint,
                "processed": left + 1, "total": days,
                "costs": {f"{a},{b}": value for (a, b), value in costs.items()},
            })
            if alive is False:
                raise Interrupted

    # 已交班切点把日期切成互不跨越的区块，区块内独立分段后再合并段数。
    states = {0: (0, ())}
    for left, right in zip(bounds, bounds[1:]):
        merged = {}
        for used, (total, cuts) in states.items():
            for parts, (piece, piece_cuts) in _chunk_options(costs, left, right - left).items():
                candidate = (total + piece, cuts + piece_cuts)
                count = used + parts
                if count not in merged or candidate < merged[count]:
                    merged[count] = candidate
        states = merged
    if not states:
        raise ValueError(f"所选日期无法按每段至少 {MIN_DAYS} 天分段。")

    total_twice, parts, cuts = min(
        ((total, count, cuts) for count, (total, cuts) in states.items()),
        key=lambda item: (Fraction(item[0], 2) + penalty * (item[1] - 1), item[1], item[2]),
    )
    fit_cost = Fraction(total_twice, 2)
    filing_cost = penalty * (parts - 1)
    total_cost = fit_cost + filing_cost

    edges = sorted({0, days, *cuts, *frozen})
    segments, median_rows = [], []
    for number, (left, right) in enumerate(zip(edges, edges[1:]), start=1):
        first, last = start + timedelta(days=left), start + timedelta(days=right - 1)
        if number == 1:
            basis = "起始段"
        elif left in frozen:
            basis = "已交班固定"
        else:
            basis = "测算切点"
        segments.append({
            "segment": number,
            "start_date": first.isoformat(),
            "end_date": last.isoformat(),
            "days": right - left,
            "incident_count": sum(sum(series[left:right]) for series in counts),
            "deviation": _number(Fraction(costs[(left, right)], 2)),
            "basis": basis,
        })
    for position, site in enumerate(sites):
        row = {"site_id": site["site_id"], "site_name": site["site_name"]}
        for number, (left, right) in enumerate(zip(edges, edges[1:]), start=1):
            window = sorted(counts[position][left:right])
            middle = len(window) // 2
            twice = 2 * window[middle] if len(window) % 2 else window[middle - 1] + window[middle]
            row[f"segment_{number}"] = _number(Fraction(twice, 2))
        median_rows.append(row)

    daily_rows = []
    for offset in range(days):
        row = {"report_date": (start + timedelta(days=offset)).isoformat()}
        for position, site in enumerate(sites):
            row[site["site_id"]] = counts[position][offset]
        row["total"] = sum(series[offset] for series in counts)
        daily_rows.append(row)

    cut_dates = [(start + timedelta(days=offset)).isoformat() for offset in cuts]
    frozen_dates = [(start + timedelta(days=offset)).isoformat() for offset in sorted(frozen)]
    note = (
        f"{district or '全部片区'} · 统计起日 {start.isoformat()} · 统计止日 {end.isoformat()}，"
        f"逐日统计 {len(sites)} 个在册灯组（零单也计入），每段至少 {MIN_DAYS} 天。"
        f"每多一段记填报代价 {_number(penalty)}，已交班切点 {('、'.join(frozen_dates) or '无')}。"
    )
    if resumed_from:
        note += f" 本次从中断前的第 {resumed_from} 天前缀继续。"

    return {
        "kind": "segment",
        "state": "warning" if parts > 1 else "ok",
        "headline": (
            f"{district or '全部片区'} · {start.isoformat()} 至 {end.isoformat()} 共 {days} 天，"
            f"分为 {parts} 段，总成本 {_number(total_cost)}。"
        ),
        "note": note,
        "total_cost": _number(total_cost),
        "segments": segments,
        "cuts": cut_dates,
        "frozen_cuts": frozen_dates,
        "resumed_from": resumed_from,
        "metrics": [
            {"label": "日期天数", "value": days, "detail": f"{start.isoformat()} 至 {end.isoformat()}"},
            {"label": "在册灯组", "value": len(sites), "detail": "零报修灯组也计入每日分母"},
            {"label": "分段数", "value": parts, "detail": f"每段至少 {MIN_DAYS} 天"},
            {"label": "拟合偏差", "value": _number(fit_cost), "detail": "各组每日单量与段内中位数的绝对差之和"},
            {"label": "分段代价", "value": _number(filing_cost), "detail": f"每多一段 {_number(penalty)} × {parts - 1} 段"},
            {"label": "总成本", "value": _number(total_cost), "detail": "拟合偏差 + 分段代价，取最低"},
            {"label": "首个切点", "value": cut_dates[0] if cut_dates else "无", "detail": "格局从切点当天开始变化"},
        ],
        "tables": [
            {"title": "分段结果", "columns": [
                {"key": "segment", "label": "段"}, {"key": "start_date", "label": "起始日期"},
                {"key": "end_date", "label": "结束日期"}, {"key": "days", "label": "天数"},
                {"key": "incident_count", "label": "工单数"}, {"key": "deviation", "label": "拟合偏差"},
                {"key": "basis", "label": "切点依据"},
            ], "rows": segments},
            {"title": "段内各组日中单量中位数", "columns": [
                {"key": "site_id", "label": "灯组编号"}, {"key": "site_name", "label": "台账点位"},
                *[{"key": f"segment_{number}", "label": f"第 {number} 段（{item['start_date']} 起）"}
                  for number, item in enumerate(segments, start=1)],
            ], "rows": median_rows},
            {"title": "逐日灯组单量", "columns": [
                {"key": "report_date", "label": "报修日期"},
                *[{"key": site["site_id"], "label": site["site_id"]} for site in sites],
                {"key": "total", "label": "合计"},
            ], "rows": daily_rows},
        ],
        "charts": [{
            "title": "逐日报修单量", "kind": "bar",
            "series": [{"label": row["report_date"], "value": row["total"]} for row in daily_rows],
        }],
        "sections": [{
            "title": "分段与交班",
            "text": (
                f"切点日期：{('、'.join(cut_dates) or '无')}。切点是下一段的起始日期。"
                "已交班切点在下一次补录重算时固定不动；出现更新批次或新交班后，旧结果只留作历史。"
            ),
        }],
    }
