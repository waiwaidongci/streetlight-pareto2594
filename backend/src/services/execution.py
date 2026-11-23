import logging
from datetime import date, timedelta
from sqlalchemy import and_, func, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.domain.analysis import analyze
from src.domain.segmentation import Interrupted, analyze as analyze_segments
from src.domain.spec import SPEC
from src.models.jobs import Delivery, Handover, Run, Schedule
from src.models.source import Dataset
from src.services.audit import record
from src.utils.exports import write_report
from src.utils.time import now

log = logging.getLogger(__name__)


def scope_of(job):
    return (job.params or {}).get('district_scope', '') if job.kind == 'segment' else ''


def identity_of(job):
    """同一片区的同一起止日期视为同一次重算序列，只保留最新结果。"""
    params = job.params or {}
    return (scope_of(job), params.get('start_date'), params.get('end_date'))


def supersede(db, job, *, include_running=False, include_complete=True):
    """把同一次重算序列里更早的旧结果标为历史，不删除结果本身。"""
    if not job or job.kind != 'segment':
        return 0
    states = []
    if include_complete:
        states.append(Status.COMPLETE)
    states.append(Status.QUEUED)
    if include_running:
        states.append(Status.RUNNING)
    if not states:
        return 0
    count = 0
    for other in db.scalars(select(Run).where(Run.kind == 'segment', Run.id < job.id, Run.status.in_(states))):
        if identity_of(other) != identity_of(job):
            continue
        other.status = Status.SUPERSEDED
        other.superseded_at = now()
        count += 1
    return count


def frozen_handovers(db, scope, start, end):
    query = select(Handover).where(Handover.district == scope).order_by(Handover.cut_date)
    if start and end:
        query = query.where(Handover.cut_date >= start, Handover.cut_date <= end)
    return list(db.scalars(query))


def snapshot_for(db, params, dataset_id):
    """发起时冻结工单批次与已交班切点，中断恢复和补录重算都沿用这份快照。"""
    scope = params.get('district_scope', '')
    start, end = params.get('start_date'), params.get('end_date')
    handovers = frozen_handovers(db, scope, start, end)
    return {
        'dataset_id': dataset_id,
        'dataset_mark': db.scalar(select(func.max(Dataset.id))) or dataset_id,
        'start_date': start,
        'end_date': end,
        'district_scope': scope,
        'frozen_cuts': [row.cut_date for row in handovers],
        'handover_mark': max((row.id for row in handovers), default=0),
    }


def materialize_due():
    if not SPEC.get('scheduled'):
        return
    stamp = now()
    with Session.begin() as db:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_at <= stamp)
                                .order_by(Schedule.next_at).limit(20).with_for_update(skip_locked=True))
        for item in schedules:
            due = item.next_at
            dataset_id = item.dataset_id
            params = dict(item.params)
            if item.repeat_daily:
                elapsed_days = (stamp - due).days
                due += timedelta(days=elapsed_days)
                period_key = SPEC['schedule_date_param']
                report_day = date.fromisoformat(params[period_key]) + timedelta(days=elapsed_days)
                params[period_key] = report_day.isoformat()
                item.params = {**params, period_key: (report_day + timedelta(days=1)).isoformat()}
                dataset_id = db.scalar(select(Dataset.id).order_by(Dataset.id.desc()).limit(1)) or dataset_id
                item.next_at = due + timedelta(days=1)
            else:
                item.enabled = False
            db.add(Run(dataset_id=dataset_id, schedule_id=item.id, scheduled_at=due,
                       name=item.name, params=params, author=item.author))


def claim():
    stamp = now()
    with Session.begin() as db:
        query = select(Run).where(or_(Run.status == Status.QUEUED,
                                      and_(Run.status == Status.RUNNING, Run.lease_until < stamp)))
        job = db.scalar(query.order_by(Run.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的运行'
            job.finished_at = stamp
            record(db, 'worker', 'analysis.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        # 中断恢复沿用发起时冻结的工单批次、切点与已有检查点。
        snapshot = job.snapshot if job.kind == 'segment' else None
        dataset_id = (snapshot or {}).get('dataset_id') or job.dataset_id
        dataset = db.get(Dataset, dataset_id) or db.get(Dataset, job.dataset_id)
        return {
            'id': job.id, 'attempt': job.attempt, 'rows': dataset.rows, 'params': job.params,
            'kind': job.kind, 'snapshot': snapshot, 'checkpoint': job.checkpoint,
        }


def _progress(run_id, attempt):
    """保存日期前缀进度并续租；返回是否仍持有本次执行权。"""
    def save(checkpoint):
        with Session.begin() as db:
            job = db.get(Run, run_id, with_for_update=True)
            if job is None or job.attempt != attempt or job.status != Status.RUNNING:
                return False
            job.checkpoint = checkpoint
            job.lease_until = now() + timedelta(minutes=3)
            return True
    return save


def stale_reason(db, job):
    """并发重算期间出现更新批次、新交班或更新并发结果时，本次旧结果只留历史。"""
    if job.kind != 'segment':
        return None
    snapshot = job.snapshot or {}
    latest_dataset = db.scalar(select(func.max(Dataset.id))) or 0
    if latest_dataset > snapshot.get('dataset_mark', job.dataset_id):
        return '计算期间导入了更新的数据批次，本次结果仅留历史'
    scope = scope_of(job)
    query = select(func.max(Handover.id)).where(Handover.district == scope)
    if snapshot.get('start_date') and snapshot.get('end_date'):
        query = query.where(Handover.cut_date >= snapshot['start_date'],
                            Handover.cut_date <= snapshot['end_date'])
    if (db.scalar(query) or 0) > snapshot.get('handover_mark', 0):
        return '计算期间记录了新的交班切点，本次结果仅留历史'
    newer = db.scalars(
        select(Run).where(Run.kind == 'segment', Run.id > job.id,
                          Run.status.in_([Status.QUEUED, Status.RUNNING, Status.COMPLETE]))
    )
    for other in newer:
        if identity_of(other) == identity_of(job):
            return '已有更新的并发重算，本次结果仅留历史'
    return None


def execute(lease):
    job_id, attempt = lease['id'], lease['attempt']
    result = export = failure = None
    aborted = False
    try:
        if lease['kind'] == 'segment':
            result = analyze_segments(
                lease['rows'], lease['params'], snapshot=lease['snapshot'],
                checkpoint=lease['checkpoint'], progress=_progress(job_id, attempt),
            )
        else:
            result = analyze(lease['rows'], lease['params'])
        export = write_report(job_id, attempt, result)
    except Interrupted:
        aborted = True
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Analysis %s failed', job_id)
        failure = '计算失败，请检查字段口径后重试'
    if aborted:
        return
    with Session.begin() as db:
        job = db.get(Run, job_id, with_for_update=True)
        if job.attempt != attempt or job.status != Status.RUNNING:
            return
        job.lease_until = None
        job.finished_at = now()
        stale = failure or stale_reason(db, job)
        if stale:
            job.status = Status.FAILED if failure else Status.SUPERSEDED
            job.error = stale[:500]
            job.superseded_at = None if failure else job.finished_at
            job.result = result
            job.export_file = export
        else:
            job.status = Status.COMPLETE
            job.result = result
            job.export_file = export
            job.checkpoint = None
            if job.kind == 'segment':
                supersede(db, job)
        record(db, 'worker', f'analysis.{job.status}', job.id, attempt=attempt, reason=job.error)
        if job.schedule_id:
            plan = db.get(Schedule, job.schedule_id)
            ready = job.status == Status.COMPLETE and result.get('ready', True)
            detail = '已保存 CSV 到站内收件箱' if ready else (job.error or (result or {}).get('headline') or '未生成结果')
            receipt = Delivery(run_id=job.id, recipient=plan.recipient, state='delivered' if ready else 'blocked', detail=detail[:500])
            db.add(receipt)
            record(db, 'worker', f'delivery.{receipt.state}', job.id, recipient=plan.recipient)
