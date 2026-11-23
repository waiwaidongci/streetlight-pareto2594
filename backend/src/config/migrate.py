"""在已有数据库上补齐新增字段，避免旧卷缺少分段列。"""

from sqlalchemy import inspect, text
from src.config.database import engine

COLUMNS = (
    ('kind', "VARCHAR(20) NOT NULL DEFAULT 'pareto'"),
    ('snapshot', 'JSON'),
    ('checkpoint', 'JSON'),
    ('superseded_at', 'TIMESTAMP WITHOUT TIME ZONE'),
)


def upgrade():
    """只补建缺失的列，重复执行安全；新建表由 create_all 直接带齐字段。"""
    inspector = inspect(engine)
    if 'runs' not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns('runs')}
    with engine.begin() as connection:
        for name, ddl in COLUMNS:
            if name not in existing:
                connection.execute(text(f'ALTER TABLE runs ADD COLUMN {name} {ddl}'))
