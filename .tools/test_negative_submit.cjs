// P1.1 回归：确认生成必须提交用户已看到/确认的完整负面（含空串）。
// touched 只记录编辑来源，不决定 final 文本是否发送；未初始化（从未 ready）才不发送。
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const file = process.argv[2] || path.join(__dirname, '../web/index.html');
const html = fs.readFileSync(file, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].at(-1)[1];
function section(start, end) {
  const a = script.indexOf(start), b = script.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `missing tested function ${start}`);
  return script.slice(a, b);
}

const elements = {
  workflow: { value: 'anima' },
  size: { value: '832x1216' },
  negativeEdit: { value: '', dataset: {}, placeholder: '' },
  'image-fit': { value: 'preserve' },
  'image-crop': { value: 'center' },
  denoise: { value: '0.35' },
};
const context = vm.createContext({
  $: id => { assert.ok(elements[id], `unexpected UI access: ${id}`); return elements[id]; },
  workflows: [{ name: 'anima', quality_prefix: 'Q, ', default_negative: 'DEFAULT-NEG' }],
  completionLevel: 'auto',
  sourceImage: null,
  getSelectedLoraSelections: () => [],
  getDetailer: () => null,
  isImg2Img: () => false,
});
vm.runInContext(section('function setNegativeEditor(', 'function initNegativeEditorForWorkflow'), context);
vm.runInContext(section('function jobsBody(', 'async function doTranslate'), context);

const box = elements.negativeEdit;
const body = () => context.jobsBody('1girl', '少女');

// 1) 自动填入但未手动编辑（ready=true, touched 未设）→ 必须发送显示值。
//    这是核心回归：服务端默认随后改成别的，提交仍是用户看到的 A。
context.setNegativeEditor('NEG-A', true);
delete box.dataset.touched;
assert.equal(body().negative_prompt, 'NEG-A', 'displayed untouched negative must be submitted');

// 2) 显式空串 → 发送 ""（真实清空）。
context.setNegativeEditor('', true);
assert.equal(body().negative_prompt, '', 'explicit empty negative must be sent as empty string');

// 3) 恢复默认后应提交其实际显示值。
context.setNegativeEditor('DEFAULT-NEG', true);
assert.equal(body().negative_prompt, 'DEFAULT-NEG', 'restored default must be submitted verbatim');

// 4) 首次 manual，负面框从未初始化（无 ready）→ 不发送，交由后端使用默认。
delete box.dataset.ready;
box.value = '';
assert.equal(body().negative_prompt, undefined, 'uninitialized negative must not send an empty override');

// 5) 用户手动编辑 → ready，提交其值。
box.dataset.touched = 'true';
box.dataset.ready = 'true';
box.value = 'USER-EDIT';
assert.equal(body().negative_prompt, 'USER-EDIT', 'user edit must be submitted');

// 6) assisted 默认仍是 final 提交语义。
assert.equal(body().prompt_mode, 'assisted');
assert.equal(body().prompt_state, 'final');

console.log('negative submit contract: displayed/confirmed negative is always submitted (incl. empty); uninitialized stays default');
