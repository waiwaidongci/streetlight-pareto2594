# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、复核和报表分发动作。

# 002 格局分段

`runs` 新增分段所需字段，`handovers` 保存已交班切点。`create_all` 会补建 `handovers`，旧 `runs` 表由 `src/config/migrate.py` 幂等补列。

- `runs.kind` 区分 `pareto`（集中度）和 `segment`（格局分段），默认 `pareto`。
- `runs.snapshot` 冻结发起时的工单批次号、已交班切点与统计范围，供中断恢复和补录重算沿用。
- `runs.checkpoint` 保存后台计算已算完的日期前缀与区间偏差，中断后从该前缀继续。
- `runs.superseded_at` 记录旧结果转为历史的时间；状态 `superseded` 表示只留历史、不再作为当前结果。
- `handovers` 以 `district` 与 `cut_date` 唯一，记录切点来源运行、交班人说明和时间。已交班切点固定保留，重算不能再挪。
