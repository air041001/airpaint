// Focused regression tests for the exhibit's draft/artwork separation.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const file = process.argv[2] || path.join(__dirname, '../web/index.html');
const html = fs.readFileSync(file, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].at(-1)[1];
function section(start, end) {
  const a=script.indexOf(start), b=script.indexOf(end,a);
  assert.ok(a>=0 && b>a, `missing tested function ${start}`);
  return script.slice(a,b);
}
const elements = {};
for (const id of ['prompt','promptEnEdit','conceptEdit','size','workflow','dl','badgeWf','status']) elements[id]={value:'unsent-'+id,textContent:'',className:''};
let displayedImage = null;
const context = vm.createContext({
  $: id => { assert.ok(elements[id], `unexpected UI write: ${id}`); return elements[id]; },
  API:'http://fixture',viewedJob:null,currentJobId:null,currentHistorySessionId:null,polling:null,actionBusy:false,
  setCanvasImage:src=>{displayedImage=src;}, setWorkbenchView:()=>{},
});
vm.runInContext(section('function restoreHistoryJob(job) {','async function recoverHistoryJob'),context);
const draft = JSON.stringify(Object.fromEntries(['prompt','promptEnEdit','conceptEdit','size','workflow'].map(id=>[id,elements[id].value])));
const art = {id:'portrait-A', image:'/owned/A.png', workflow:'recorded-anima', prompt_raw:'old raw',prompt_en:'old prompt',concept:'old concept',width:832,height:1216,session_ids:['branch-A']};
context.restoreHistoryJob(art);
assert.equal(context.currentJobId,'portrait-A');
assert.equal(context.currentHistorySessionId,'branch-A');
assert.equal(displayedImage,'http://fixture/owned/A.png');
assert.equal(elements.badgeWf.textContent,'recorded-anima');
assert.equal(JSON.stringify(Object.fromEntries(['prompt','promptEnEdit','conceptEdit','size','workflow'].map(id=>[id,elements[id].value]))),draft,'browsing must preserve all draft fields');
elements.status.textContent='完成 · seed A'; elements.status.className='text-green-400';
context.restoreHistoryJob({id:'landscape-B',image:'/owned/B.png',session_id:'branch-B'});
assert.equal(context.currentJobId,'landscape-B');
assert.equal(context.currentHistorySessionId,'branch-B');
assert.equal(displayedImage,'http://fixture/owned/B.png');
assert.equal(elements.status.textContent,'','an idle completion banner must not claim the previous image seed');
context.restoreHistoryJob({id:'independent',image:'/owned/C.png'});
assert.equal(context.currentHistorySessionId,null,'an independent work must not inherit the previous session');
// A background task must not become the source of the still-displayed image.
const historyLoad=section('async function loadHistory(',"$('historyMore').onclick");
assert.ok(!historyLoad.includes('currentJobId = active.id'));
// The source for displayed iteration metadata must match the displayed image.
const dialogRender=section('function renderDialog(turns) {','// ---------------- 启动');
assert.match(dialogRender,/viewedTurn = visible\?\.image \? visible : null/);
assert.match(dialogRender,/源图预览 · 新版尚未完成/);
console.log('Draft preservation, artwork/session selection, background-task separation, and iteration fallback checks passed');

async function checkBranchSubmission() {
  const sent = [];
  for (const id of ['dlg-go','dlg-delta','dlg-seed-strategy','dlg-denoise','dlg-fit','dlg-crop','dlg-status']) elements[id]={value:'',textContent:'',className:''};
  Object.assign(context, {
    dlgIsBusy:false, dlgMode:'redo', sessionId:'session-A', dlgSourceJobId:'selected-parent-A',
    dlgBusy:()=>{}, closeSheet:()=>{}, pollDialog:()=>{},
    dlgTurn:async body=>{sent.push(body);return {job_id:'new-child'};},
  });
  vm.runInContext(section("$('dlg-go').onclick = async () => {",'function pollDialog()'),context);
  elements['dlg-delta'].value='换成清晨光照';
  elements['dlg-seed-strategy'].value='random';
  await elements['dlg-go'].onclick();
  assert.equal(sent[0].source_job_id,'selected-parent-A');
  assert.equal(sent[0].action,'redo');
  assert.equal(sent[0].seed_strategy,'random');
  assert.equal(sent[0].delta,'换成清晨光照');
  assert.ok(!('denoise' in sent[0]),'text reroll must not silently become pixel redraw');
  assert.equal(context.dlgSourceJobId,'new-child');
  context.dlgMode='tweak'; context.dlgSourceJobId='older-parent-B';
  elements['dlg-delta'].value='调整整体氛围';
  elements['dlg-seed-strategy'].value='inherit';
  elements['dlg-denoise'].value='0.55'; elements['dlg-fit'].value='crop'; elements['dlg-crop'].value='top';
  await elements['dlg-go'].onclick();
  assert.equal(sent[1].source_job_id,'older-parent-B','branch must use selected ancestor, not the newest child');
  assert.equal(sent[1].action,'tweak'); assert.equal(sent[1].seed_strategy,'inherit');
  assert.equal(sent[1].denoise,.55); assert.equal(sent[1].fit_mode,'crop'); assert.equal(sent[1].crop_position,'top');
  console.log('Selected-ancestor reroll and pixel-redraw submission contracts passed');
}
checkBranchSubmission().catch(error=>{console.error(error);process.exitCode=1;});
