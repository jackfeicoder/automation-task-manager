import {api, send} from './api/client.js';
import {escape, badge, date, toast, names} from './components/ui.js';
import {tasksPage, runsPage, settingsPage, environmentPage} from './pages/render.js';

const state={tasks:[],runs:[],settings:{},environment:{variables:[]},doctor:null,selected:new Set(),search:'',runFilter:'',loaded:false};
const pages={tasks:tasksPage,runs:runsPage,settings:settingsPage,environment:environmentPage};
const pageNames={tasks:'任务管理',runs:'执行记录',settings:'全局配置',environment:'运行环境'};
const content=document.querySelector('#content');
let editing=null, logId=null, refreshing=false;
const currentPage=()=>location.hash.slice(1) in pages?location.hash.slice(1):'tasks';

function render() {
  if (!state.loaded) return;
  const page=currentPage();
  document.querySelector('#page-name').textContent=pageNames[page];
  document.querySelectorAll('nav a').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
  content.innerHTML=pages[page](state);
}

async function refresh(renderPage=true) {
  if (refreshing) return;
  refreshing=true;
  try {
    const [tasks,runs,settings,environment]=await Promise.all([api('/tasks'),api('/runs'),api('/settings'),api('/environment')]);
    Object.assign(state,{tasks,runs,settings,environment,loaded:true});
    state.selected=new Set([...state.selected].filter(id=>tasks.some(x=>x.id===id)));
    document.querySelector('#connection').textContent='后台服务在线';
    document.querySelector('#connection-dot').className='online';
    const editingPage=document.activeElement?.closest('#content form') || document.activeElement?.id==='search';
    if (renderPage && !editingPage && (renderPage!=='poll' || ['tasks','runs'].includes(currentPage()))) render();
    if (logId && document.querySelector('#log-dialog').open) await updateLog();
  } catch(error) {
    if(error.status===401) {
      if(!document.querySelector('#login-dialog').open) document.querySelector('#login-dialog').showModal();
    } else {
      document.querySelector('#connection').textContent='连接中断，正在重试';
      document.querySelector('#connection-dot').className='';
      if(!state.loaded) content.innerHTML='<div class="empty">后台服务连接失败，页面将自动重试。</div>';
    }
  } finally {refreshing=false;}
}

function openTask(task=null) {
  editing=task?.id||null;
  const form=document.querySelector('#task-form');
  form.reset();
  const initial=task||{id:'',name:'',description:'',enabled:false,cwd:'tasks/example',command:['{python}','main.py'],timeout_seconds:120,max_attempts:1,resource_group:'',daily_once:false,env_names:[],schedule:{kind:'manual',time:'09:00',weekdays:[0],interval_minutes:1440,cron:'0 9 * * *',timezone:state.settings.timezone}};
  const values={...initial,...initial.schedule,command:JSON.stringify(initial.command),env_names:initial.env_names.join(', ')};
  for(const [key,value] of Object.entries(values)) {
    const input=form.elements.namedItem(key);
    if(input instanceof HTMLInputElement || input instanceof HTMLSelectElement || input instanceof HTMLTextAreaElement) {
      if(input.type==='checkbox') input.checked=Boolean(value);
      else input.value=value;
    }
  }
  form.elements.id.readOnly=Boolean(task);
  form.querySelectorAll('[name="weekday"]').forEach(x=>x.checked=initial.schedule.weekdays.includes(Number(x.value)));
  document.querySelector('#task-title').textContent=task?'编辑任务':'新建任务';
  frequencyFields();
  document.querySelector('#task-dialog').showModal();
}

function frequencyFields() {
  const kind=document.querySelector('#task-form').elements.kind.value;
  document.querySelectorAll('[data-frequency]').forEach(x=>x.hidden=!x.dataset.frequency.split(' ').includes(kind));
}

async function updateLog() {
  const run=await api('/runs/'+logId);
  document.querySelector('#log-title').textContent=run.task_name+' · 执行日志';
  document.querySelector('#log-meta').innerHTML=`${badge(run.status)}<span>${date(run.created_at)} · 第 ${run.attempt} 次尝试</span><p>${escape(run.message||'等待执行')}</p>`;
  const pre=document.querySelector('#log-output');
  const atBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<40;
  pre.textContent=run.log||'等待任务输出…';
  if(atBottom) pre.scrollTop=pre.scrollHeight;
  document.querySelector('#stop-log').hidden=!['queued','running'].includes(run.status);
}

async function run(id) {
  const result=await send('/tasks/'+id+'/run',{});
  if(result.status==='already_completed') toast('该任务今日已完成');
  else if(result.status==='already_running') toast('该任务已在队列或执行中');
  else toast('任务已加入执行队列');
  await refresh();
}

document.addEventListener('click',async event=>{
  const close=event.target.closest('[data-close]');
  if(close) {document.getElementById(close.dataset.close).close();if(close.dataset.close==='log-dialog')logId=null;return;}
  const button=event.target.closest('[data-action]');
  if(!button)return;
  const {action,id}=button.dataset;
  const task=state.tasks.find(x=>x.id===id);
  button.disabled=true;
  try {
    if(action==='new')openTask();
    if(action==='edit')openTask(task);
    if(action==='run')await run(id);
    if(action==='toggle') {await send('/tasks/'+id,{...task,enabled:!task.enabled},'PUT');await refresh();}
    if(action==='delete' && confirm(`删除「${task.name}」？历史记录会保留。`)) {await api('/tasks/'+id,{method:'DELETE'});toast('任务已删除');await refresh();}
    if(action==='history') {state.runFilter=id;location.hash='runs';render();}
    if(action==='discover') {const result=await send('/tasks/discover',{});toast(`发现 ${result.added.length} 个新任务${result.errors.length?'；'+result.errors.length+' 个插件配置错误':''}`,result.errors.length>0);await refresh();}
    if(action.startsWith('batch-')) {
      if(!state.selected.size){toast('请先勾选任务');return;}
      const results=await send('/tasks/batch',{ids:[...state.selected],action:action.split('-')[1]});
      const summary=results.reduce((all,x)=>{const label=names[x.status]||({already_running:'已在执行',updated:'已更新',not_found:'任务不存在'}[x.status])||x.status;all[label]=(all[label]||0)+1;return all;},{});
      toast(Object.entries(summary).map(([key,value])=>`${key} ${value} 项`).join('，'));
      await refresh();
    }
    if(action==='log'){logId=id;await updateLog();document.querySelector('#log-dialog').showModal();}
    if(action==='stop'){await send('/runs/'+id+'/stop',{});toast('已请求停止任务');await refresh();}
    if(action==='refresh')await refresh();
    if(action==='doctor'){state.doctor=await api('/plugins/workbuddy/doctor');render();}
    if(action==='env-remove' && confirm(`清除 ${id} 的本地值？系统环境变量如有配置，仍会生效。`)){await send('/environment',{values:{[id]:null}},'PUT');toast('本地值已清除');await refresh();}
  } catch(error){toast(error.message,true);} finally {button.disabled=false;}
});

document.addEventListener('change',event=>{
  const target=event.target;
  if(target.dataset.select){if(target.checked)state.selected.add(target.dataset.select);else state.selected.delete(target.dataset.select);document.querySelector('#selected-count').textContent=state.selected.size;}
  if(target.id==='select-all'){content.querySelectorAll('[data-select]').forEach(x=>{x.checked=target.checked;if(x.checked)state.selected.add(x.dataset.select);else state.selected.delete(x.dataset.select);});document.querySelector('#selected-count').textContent=state.selected.size;}
  if(target.id==='run-filter'){state.runFilter=target.value;render();}
  if(target.name==='kind')frequencyFields();
});
document.addEventListener('input',event=>{
  if(event.target.id==='search') {
    state.search=event.target.value;
    const position=event.target.selectionStart;
    render();
    const input=document.querySelector('#search');input.focus();input.setSelectionRange(position,position);
  }
});

document.addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.target;
  const button=form.querySelector('[type="submit"]')||form.querySelector('.primary');
  if(button)button.disabled=true;
  try {
    const fd=new FormData(form);
    if(form.id==='task-form') {
      const command=JSON.parse(fd.get('command'));
      if(!Array.isArray(command))throw new Error('启动命令需要填写 JSON 数组');
      const payload={id:fd.get('id'),name:fd.get('name'),description:fd.get('description'),enabled:fd.has('enabled'),daily_once:fd.has('daily_once'),cwd:fd.get('cwd'),command,timeout_seconds:Number(fd.get('timeout_seconds')),max_attempts:Number(fd.get('max_attempts')),resource_group:fd.get('resource_group'),env_names:fd.get('env_names').split(',').map(x=>x.trim()).filter(Boolean),schedule:{kind:fd.get('kind'),timezone:fd.get('timezone'),time:fd.get('time')||'09:00',weekdays:fd.getAll('weekday').map(Number),interval_minutes:Number(fd.get('interval_minutes')),cron:fd.get('cron')}};
      await send(editing?'/tasks/'+editing:'/tasks',payload,editing?'PUT':'POST');
      document.querySelector('#task-dialog').close();toast('任务配置已保存');
    }
    if(form.id==='settings-form') {
      await send('/settings',{max_parallel:Number(fd.get('max_parallel')),timezone:fd.get('timezone'),retry_delay_seconds:Number(fd.get('retry_delay_seconds')),log_retention_days:Number(fd.get('log_retention_days')),scheduler_enabled:fd.has('scheduler_enabled'),catch_up:fd.has('catch_up')},'PUT');
      toast('全局配置已保存');
    }
    if(form.id==='environment-form') {
      const values={};form.querySelectorAll('[data-env]').forEach(x=>{if(x.value)values[x.dataset.env]=x.value;});
      const name=document.querySelector('#env-new-name').value.trim(),value=document.querySelector('#env-new-value').value;
      if(name && value)values[name]=value;
      if((name&&!value)||(!name&&value))throw new Error('新增变量请同时填写名称和值');
      await send('/environment',{values},'PUT');form.reset();toast('环境变量已保存');
      state.doctor=await api('/plugins/workbuddy/doctor');
    }
    if(form.id==='login-form'){await send('/auth/login',{token:fd.get('token')});form.reset();document.querySelector('#login-dialog').close();}
    await refresh(false);render();
  }catch(error){toast(error.message,true);}finally{if(button)button.disabled=false;}
});

document.querySelector('#stop-log').addEventListener('click',async()=>{try{await send('/runs/'+logId+'/stop',{});await updateLog();}catch(error){toast(error.message,true);}});
document.querySelector('#copy-log').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(document.querySelector('#log-output').textContent);toast('日志已复制');}catch{toast('请手动选择日志并复制',true);}});
document.querySelector('#log-dialog').addEventListener('close',()=>logId=null);
window.addEventListener('hashchange',render);
setInterval(()=>{document.querySelector('#clock').textContent=new Date().toLocaleString('zh-CN',{hour12:false});},1000);
setInterval(()=>refresh('poll'),3000);
content.innerHTML='<div class="empty">正在加载工作空间…</div>';
await refresh();
