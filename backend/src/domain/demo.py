"""临汀市虚拟路灯台账与已归并的暗区事件。"""


_SITES = (
    ("滨河片区", "柳汀大道北段", 32, (14, 14, 15, 16, 17, 18, 19, 20)),
    ("老城片区", "东门巷", 5, (14, 16, 17, 19, 20)),
    ("滨河片区", "汀桥路公交站段", 24, (15, 17, 18, 20)),
    ("滨河片区", "长堤辅道", 3, (16, 20)),
    ("老城片区", "西仓里路口", 2, (19,)),
    ("滨河片区", "渡口支路", 9, ()),
    ("滨河片区", "桐浦堤下通道", 6, ()),
    ("老城片区", "埠头街南段", 14, ()),
    ("老城片区", "石平码头广场路", 11, ()),
    ("老城片区", "钟楼西街", 17, ()),
)


def demo_rows() -> list[dict]:
    rows = []
    for number, (district, site, fixtures, days) in enumerate(_SITES, start=1):
        site_id = f"LH-{number:02d}"
        rows.append({
            "record_type": "灯组台账", "site_id": site_id, "district": district,
            "site_name": site, "fixture_count": fixtures, "incident_id": None, "report_date": None,
        })
        for sequence, day in enumerate(days, start=1):
            rows.append({
                "record_type": "暗区工单", "site_id": site_id, "district": None,
                "site_name": None, "fixture_count": None,
                "incident_id": f"LT-2611{day}-{number:02d}-{sequence:02d}",
                "report_date": f"2026-11-{day}",
            })
    return rows


# 11 月 14–27 日：前半段老城东门巷、西仓里重复报修密集，后半段重心转到滨河长堤、柳汀大道。
_SEGMENT_SITES = (
    ("滨河片区", "柳汀大道北段", 32),
    ("老城片区", "东门巷", 5),
    ("滨河片区", "汀桥路公交站段", 24),
    ("滨河片区", "长堤辅道", 3),
    ("老城片区", "西仓里路口", 2),
    ("滨河片区", "渡口支路", 9),
    ("老城片区", "埠头街南段", 14),
)
_SEGMENT_INCIDENTS = (
    # 14–20 日：东门巷每日 3 单，西仓里单双日各 1 单，汀桥路隔日 1 单。
    *[("LH-02", f"2026-11-{day:02d}") for day in range(14, 21) for _ in range(3)],
    ("LH-05", "2026-11-14"), ("LH-05", "2026-11-16"), ("LH-05", "2026-11-18"), ("LH-05", "2026-11-20"),
    ("LH-03", "2026-11-15"), ("LH-03", "2026-11-17"), ("LH-03", "2026-11-19"),
    # 21–27 日：长堤辅道每日 3 单，柳汀大道北段隔日 1 单，东门巷归零。
    *[("LH-04", f"2026-11-{day:02d}") for day in range(21, 28) for _ in range(3)],
    ("LH-01", "2026-11-21"), ("LH-01", "2026-11-23"), ("LH-01", "2026-11-25"), ("LH-01", "2026-11-27"),
)


def segment_rows() -> list[dict]:
    """格局分段演示批次：11 月 14–27 日逐日工单，含零单灯组与零单日期。"""
    rows = []
    for number, (district, site, fixtures) in enumerate(_SEGMENT_SITES, start=1):
        rows.append({
            "record_type": "灯组台账", "site_id": f"LH-{number:02d}", "district": district,
            "site_name": site, "fixture_count": fixtures, "incident_id": None, "report_date": None,
        })
    sequence = {}
    for site_id, report_date in _SEGMENT_INCIDENTS:
        sequence[report_date] = sequence.get(report_date, 0) + 1
        rows.append({
            "record_type": "暗区工单", "site_id": site_id, "district": None,
            "site_name": None, "fixture_count": None,
            "incident_id": f"LT-{report_date.replace('-', '')}-{sequence[report_date]:02d}",
            "report_date": report_date,
        })
    return rows
