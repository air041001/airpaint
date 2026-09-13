// P2B 行为测试：go 一键提交 final_prompt_en；jobsBody 提交 usage_refs；manual 不自动应用资料。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const file = process.argv[2] || path.join(__dirname, '../web/index.html');
const html = fs.readFileSync(file, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].at(-1)[1];
function section(start, end) {
  const a = script.indexOf(start), b = script.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `missing ${start}`);
  return script.slice(a, b);
}

function stubElement() {
  return {
    value: '', textContent: '', className: '', innerHTML: '', hidden: false,
    dataset: {}, style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return true; } },
  };
}

(async () => {
  // 1) jobsBody：final 语义 + 资料 refs（无 translation 时不带 refs）
  {
    const elements = {
      workflow: { value: 'anima' }, size: { value: '832x1216' },
      negativeEdit: { value: '', dataset: {}, placeholder: '' },
      'image-fit': { value: 'preserve' }, 'image-crop': { value: 'center' },
      denoise: { value: '0.35' },
    };
    const context = vm.createContext({
      $: id => elements[id], workflows: [{ name: 'anima', quality_prefix: 'Q, ', default_negative: 'D' }],
      completionLevel: 'auto', sourceImage: null,
      getSelectedLoraSelections: () => [], getDetailer: () => null, isImg2Img: () => false,
    });
    vm.runInContext(section('function setNegativeEditor(', 'function initNegativeEditorForWorkflow'), context);
    vm.runInContext(section('function jobsBody(', 'async function doTranslate'), context);
    context.setNegativeEditor('', true);
    const body = context.jobsBody('Q, 1girl', '少女', { usage_applied: ['u1', 'u2'], final_prompt_en: 'Q, 1girl' });
    assert.equal(body.prompt_state, 'final');
    assert.deepEqual(body.usage_refs, ['u1', 'u2']);
    assert.equal(context.jobsBody('x', 'y', null).usage_refs, undefined);
    console.log('jobsBody: final semantics + usage refs OK');
  }

  // 2) go 一键必须提交 final_prompt_en（不是 body）
  {
    const elements = {};
    const context = vm.createContext({
      $: id => (elements[id] = elements[id] || stubElement()),
      workflows: [{ name: 'anima' }], completionLevel: 'auto', refImage: null, conceptDirty: false,
      doTranslate: async () => ({ concept: 'c', breakdown: null, prompt_en: 'BODY-ONLY', final_prompt_en: 'Q, FINAL' }),
      rememberTranslation: () => {}, renderTranslation: () => {}, translationStaleReasons: () => [],
      setBusy: () => {}, stopPolling: () => {}, startPolling: () => {}, setConceptDirty: () => {},
      setWorkbenchView: () => {}, setCanvasImage: () => {}, showReviewReady: () => {},
      done: () => {}, isImg2Img: () => false,
      submitJob: async (promptEn) => { context.__submitted = promptEn; },
    });
    elements.prompt = stubElement();
    elements.prompt.value = '少女坐着';
    context.__submitted = null;
    vm.runInContext(section("$('go').onclick = async () => {", "$('confirm').onclick"), context);
    await elements.go.onclick();
    assert.equal(context.__submitted, 'Q, FINAL', String(context.__submitted));
    console.log('go handler: submits final_prompt_en OK');
  }

  // 3) manual 直出：提交给后端的是用户原文（不套用资料文本）
  {
    const elements = {};
    const context = vm.createContext({
      $: id => (elements[id] = elements[id] || stubElement()),
      workflows: [{ name: 'anima' }], completionLevel: 'auto', lastTranslation: null,
      setBusy: () => {}, stopPolling: () => {}, done: () => {}, isImg2Img: () => false,
      submitJob: async (promptEn, raw, translation, mode) => {
        context.__manual = { promptEn, mode };
      },
      renderUsageNotes: () => {},
    });
    elements.promptEnEdit = stubElement();
    elements.promptEnEdit.value = 'a lone robot';
    elements.prompt = stubElement();
    elements.prompt.value = '孤独机器人';
    vm.runInContext(section("$('manualGo').onclick = async () => {", "$('editCancel').onclick"), context);
    await elements.manualGo.onclick();
    assert.deepEqual(context.__manual, { promptEn: 'a lone robot', mode: 'manual' });
    console.log('manual path: literal text, no usage text applied OK');
  }
})().catch(error => { console.error(error); process.exit(1); });
