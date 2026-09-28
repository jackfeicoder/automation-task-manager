export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const names = {queued:'排队中',running:'执行中',success:'成功',already_completed:'今日已完成',failed:'失败',timeout:'超时',needs_login:'需要登录',needs_attention:'需要处理',cancelled:'已停止',interrupted:'服务中断'};
export const badge = status => status ? `<span class="badge ${escape(status)}"><i></i>${escape(names[status] || status)}</span>` : '<span class="muted">尚未执行</span>';
export function date(value) {
  return value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false}) : '—';
}
export function schedule(task) {
  const s = task.schedule;
  const weekdays = ['一','二','三','四','五','六','日'];
  return {manual:'仅手动',daily:`每天 ${s.time}`,weekly:`周${s.weekdays.map(x => weekdays[x]).join('、')} ${s.time}`,interval:`每 ${s.interval_minutes} 分钟`,cron:`Cron · ${s.cron}`}[s.kind];
}
export function toast(message, bad = false) {
  const element = document.createElement('div');
  element.className = 'toast' + (bad ? ' bad' : '');
  element.textContent = message;
  document.querySelector('#toasts').append(element);
  setTimeout(() => element.remove(), 5000);
}
