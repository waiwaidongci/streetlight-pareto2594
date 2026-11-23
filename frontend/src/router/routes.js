export const pages = ['overview', 'inputs', 'run', 'results', 'review', 'delivery', 'segments'];

export function route() {
  const [page, first, second] = location.hash.slice(1).split('/');
  const target = pages.includes(page) ? page : 'overview';
  if (target === 'segments') {
    if (first === 'new') return {page:target, id:null, dataset:Number(second) || null, offset:0};
    return {page:target, id:Number(first) || null, dataset:null, offset:0};
  }
  return {page:target, id:Number(first) || null, offset:Math.max(0, Number(second) || 0)};
}
