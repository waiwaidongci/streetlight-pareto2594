import {datasetField, feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const finished = item => ['complete', 'failed', 'superseded'].includes(item.status);

export function segmentResult(job, refresh, message = feedback()) {
  const result = job.result;
  if (!result) {
    return panel(job.name, pill(STATUS[job.status], job.status),
      p(job.error || '当前运行没有可展示的分段结果。', 'feedback'),
      link('查看运行记录', '#segments', {class:'quiet'}));
  }
  const extra = feedback();
  const cuts = new Set(result.cuts || []);
  const recorded = new Set((result.frozen_cuts || []));
  const rows = result.segments.map(item => ({
    ...item,
    cut: item.segment === 1 ? '—' : item.start_date,
    frozen: item.segment !== 1 && recorded.has(item.start_date),
  }));
  const actions = session.canReview && job.status === 'complete' ? el('div', {class:'actions'},
    ...rows.filter(row => row.cut !== '—' && !row.frozen && cuts.has(row.cut)).map(row => {
      const action = button(`交班 ${row.cut}`, {class:'quiet', 'data-testid':`handover-${row.cut}`});
      action.addEventListener('click', handler(action, extra, async () => {
        await workspace.handover(job.id, {cut_date:row.cut, note:'现场交班确认'});
        await refresh();
      }));
      return action;
    })) : null;
  return el('section', {class:'analysis', 'data-testid':'segment-result'},
    el('div', {class:'result-head'}, p(job.name, 'eyeline'), h(2, result.headline), p(result.note, 'intro-copy'),
      el('div', {class:'actions'}, pill(STATUS[job.status], job.status)), extra),
    metrics(result.metrics),
    panel('各段日期与总成本', table([
      {key:'segment', label:'段'}, {key:'start_date', label:'起始日期'}, {key:'end_date', label:'结束日期'},
      {key:'days', label:'天数'}, {key:'incident_count', label:'工单数'},
      {key:'deviation', label:'拟合偏差'}, {key:'basis', label:'切点依据'},
    ], result.segments, {caption:'各段日期与总成本'}),
      p(`分段总成本 ${value(result.total_cost)} · 共 ${result.segments.length} 段`, 'filename'),
      actions),
    result.tables.map(item => panel(item.title, table(item.columns, item.rows, {caption:item.title}))),
    (result.sections || []).map(item => panel(item.title, p(item.text))));
}

export async function segments(route, refresh) {
  const {spec, datasets, runs} = workspace;
  const segment = spec.segment;
  const segmentRuns = runs.filter(item => item.kind === 'segment');
  const selected = Number(route.id) || segmentRuns[0]?.id;
  const job = selected ? await workspace.getRun(selected) : null;
  const datasetId = route.dataset ?? datasets[0]?.id;
  const message = feedback();
  const sample = button('下载分段示例 CSV', {class:'quiet', onClick:event =>
    handler(sample, message, () => workspace.download(`/template?name=${spec.segment.short_name}`, segment.sample_name))(event)});
  if (job && !finished(job)) {
    const original = location.hash;
    const status = el('p', {role:'status'}, '后台正在计算分段，完成后会自动展示。');
    poll(() => workspace.getRun(job.id), finished, () => location.hash === original)
      .then(result => { if (result && status.isConnected && location.hash === original) refresh(); })
      .catch(error => { if (status.isConnected) status.textContent = error.message; });
    return panel(job.name, pill(STATUS[job.status], job.status), status,
      button('刷新', {class:'quiet', onClick:refresh}));
  }
  const submit = button(`执行${segment.run}`, {type:'submit', class:'primary', 'data-testid':'start-segment'});
  const form = session.canWrite && el('form', {class:'fields', 'data-testid':'segment-form',
    onSubmit:handler(submit, message, async event => {
      const data = new FormData(event.target);
      const item = await workspace.start({
        dataset_id:Number(data.get('dataset_id')), name:data.get('name'), kind:'segment',
        params:Object.fromEntries(segment.params.map(param => [param.key, data.get(param.key)])),
      });
      location.hash = `#segments/${item.id}`;
    })}, datasetField(datasets, datasetId),
    field({key:'name', label:'运行名称', type:'string', max_length:120}, `样本${segment.run}`),
    el('div', {class:'form-grid'}, segment.params.map(param => field(param))),
    el('div', {class:'actions'}, submit, sample), message);
  const history = table([
    {label:'运行', render:item => link(item.name, `#segments/${item.id}`)},
    {label:'状态', render:item => pill(STATUS[item.status], item.status)},
    {label:'时间', render:item => date(item.created_at)},
  ], segmentRuns, {caption:'分段运行记录', empty:'首次分段后将在此显示后台状态。'});
  const picker = segmentRuns.length > 1 && el('label', {class:'field run-picker', for:'segment-picker'},
    el('span', {}, '切换分段运行'),
    el('select', {id:'segment-picker', onChange:event => { location.hash = `#segments/${event.target.value}`; }},
      segmentRuns.map(item => el('option', {value:item.id, selected:item.id === job?.id}, item.name))));
  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'},
      panel(segment.title, session.canWrite
        ? (datasets.length ? form : empty('请先导入数据。', '#inputs', '前往导入'))
        : p('只读角色可以查看已完成的分段结果。', 'empty')),
      job ? el('div', {}, picker, segmentResult(job, refresh, message))
        : panel(segment.title, p('还没有分段结果，先执行一次格局分段测算。', 'empty')),
      session.canWrite && panel('运行记录', history)),
    el('aside', {class:'sidebar-panel'},
      panel('计算口径', el('ul', {class:'method-list'}, [
        `逐日按灯组统计工单，零单也计入；每段至少 ${segment.min_days} 天。`,
        '总成本 = 各组每日单量与段内中位数的绝对差之和 + 每多一段的填报代价。',
        '取总成本最低；同分选段数少的，再选切点日期最早的一组。',
        '已交班切点重算时固定不动；中断恢复沿用发起时的工单与切点，跳过已算完的日期前缀。',
        '并发重算遇到更新批次或新交班时，旧结果只留作历史。',
      ]))));
}
