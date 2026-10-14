const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(apiFetch) {
    const path = 'app/static/model-settings.js';
    assert.ok(fs.existsSync(path), 'Model settings UI should exist');
    const ids = ['modelBaseUrl', 'modelName', 'modelApiKey', 'modelClearKey',
        'modelSettingsStatus', 'modelSkillStatus', 'modelSettingsResult', 'modelSettingsFields',
        'modelSaveButton', 'modelTestButton', 'modelReloadButton', 'modelSettingsForm'];
    const elements = Object.fromEntries(ids.map(id => [id, { value: '', checked: false,
        disabled: false, hidden: false, textContent: '', placeholder: '', dataset: {},
        reportValidity: () => true, addEventListener: () => {} }]));
    const c = vm.createContext({ document: {getElementById: id => elements[id], addEventListener: () => {}, querySelectorAll: () => []},
        apiFetch, getErrorText: String });
    vm.runInContext(fs.readFileSync(path, 'utf8'), c);
    return { c, elements };
}

test('model settings entry exists and saved keys are never filled into inputs', async () => {
    const html = fs.readFileSync('app/static/index.html', 'utf8');
    assert.match(html, /data-route-target="model-settings"/);
    assert.match(html, /id="modelApiKey"[^>]*type="password"/);
    const {c,elements:e} = setup(async () => ({base_url:'https://model.example/v1',model_name:'fiction',api_key_set:true,configured:true}));
    await c.loadModelSettings();
    assert.equal(e.modelName.value, 'fiction');
    assert.equal(e.modelApiKey.value, '');
    assert.match(e.modelApiKey.placeholder, /留空保留/);
});

test('save clears the entered key and enables the page after completion', async () => {
    let sent;
    const {c,elements:e} = setup(async (url,options) => {
        if (options?.method === 'PUT') sent = JSON.parse(options.body);
        return {base_url:'https://model.example/v1',model_name:'fiction',api_key_set:true,configured:true};
    });
    await c.loadModelSettings();
    e.modelApiKey.value = 'fiction-key';
    await c.handleModelSettingsSave({preventDefault(){}});
    assert.equal(sent.api_key, 'fiction-key');
    assert.equal(e.modelApiKey.value, '');
    assert.equal(e.modelSettingsFields.disabled, false);
    assert.match(e.modelSettingsResult.textContent, /立即生效/);
});

test('connection test reports failure and never saves a draft', async () => {
    const calls=[];
    const {c,elements:e} = setup(async (url,options) => {
        calls.push([url,options?.method]);
        return options ? {ok:false,message:'认证失败'} : {base_url:'https://model.example/v1',model_name:'fiction',api_key_set:false,configured:false};
    });
    await c.loadModelSettings();
    e.modelApiKey.value='fiction-key';
    await c.handleModelSettingsTest();
    assert.equal(calls.at(-1)[0], '/api/v1/settings/model/test');
    assert.ok(!calls.some(call => call[1] === 'PUT'));
    assert.match(e.modelSettingsResult.textContent, /认证失败/);
    assert.equal(e.modelSettingsFields.disabled, false);
});

test('key clear is explicit and a failed load keeps save disabled', async () => {
    const {c,elements:e} = setup(async () => {throw new Error('请先登录');});
    await c.loadModelSettings();
    assert.equal(e.modelSaveButton.disabled, true);
    assert.match(e.modelSettingsResult.textContent, /请先登录/);
    e.modelApiKey.value='fiction-key';
    e.modelClearKey.checked=true;
    c.handleModelKeyClearChange();
    assert.equal(e.modelApiKey.value,'');
    assert.equal(e.modelApiKey.disabled,true);
});


test('empty legacy configuration initially fills DeepSeek without saving', async () => {
    const calls=[];
    const {c,elements:e} = setup(async (...args) => {
        calls.push(args);
        return {base_url:'https://api.openai.com/v1',model_name:'',api_key_set:false,configured:false};
    });
    await c.loadModelSettings();
    assert.equal(e.modelBaseUrl.value,'https://api.deepseek.com');
    assert.equal(e.modelName.value,'deepseek-flash');
    assert.equal(calls.length,1);
    assert.equal(e.modelApiKey.value,'');
});

test('provider presets fill address and model but never save or carry draft secrets', async () => {
    let calls=0;
    const {c,elements:e} = setup(async () => {
        calls++;
        return {base_url:'https://api.deepseek.com',model_name:'deepseek-flash',api_key_set:true,configured:true};
    });
    await c.loadModelSettings();
    e.modelApiKey.value='fiction-draft-key';
    c.applyModelPreset('qwen');
    assert.equal(e.modelBaseUrl.value,'https://dashscope.aliyuncs.com/compatible-mode/v1');
    assert.equal(e.modelName.value,'qwen-plus');
    assert.equal(e.modelApiKey.value,'');
    assert.equal(e.modelClearKey.checked,false);
    assert.match(e.modelSettingsResult.textContent,/密钥/);
    assert.equal(calls,1);
    for (const id of ['deepseek','glm','kimi','siliconflow']) {
        c.applyModelPreset(id);
        assert.ok(e.modelBaseUrl.value.startsWith('https://'));
        assert.ok(e.modelName.value);
    }
    assert.equal(calls,1);
});

test('same endpoint preset retains draft key and failed-load presets do nothing', async () => {
    const {c,elements:e} = setup(async () => ({base_url:'https://api.deepseek.com',model_name:'custom-model',api_key_set:false}));
    await c.loadModelSettings();
    e.modelApiKey.value='fiction-draft-key';
    c.applyModelPreset('deepseek');
    assert.equal(e.modelApiKey.value,'fiction-draft-key');
    c.applyModelPreset('unknown-preset');
    assert.equal(e.modelName.value,'deepseek-flash');
    const failed = setup(async () => {throw new Error('读取失败');});
    await failed.c.loadModelSettings();
    failed.c.applyModelPreset('deepseek');
    assert.equal(failed.elements.modelBaseUrl.value,'');
});


test('skill status distinguishes loaded guidance from unconfigured or unavailable model', async () => {
    const skill = {ready:true,label:'网友红领巾公开方法',revision:'9410041d593d291687861fbfd52257ae9914356e'};
    const {c,elements:e} = setup(async () => ({configured:false,analysis_skill:skill}));
    await c.loadModelSettings();
    assert.match(e.modelSkillStatus.textContent, /网友红领巾/);
    assert.match(e.modelSkillStatus.textContent, /规则分析/);
    c.renderModelSettings({configured:true,model_name:'fiction',analysis_skill:skill});
    assert.match(e.modelSkillStatus.textContent, /推荐|岗位分析/);
    c.renderModelSettings({configured:true,analysis_skill:{ready:false}});
    assert.match(e.modelSkillStatus.textContent, /未加载/);
    assert.doesNotMatch(e.modelSkillStatus.textContent, /已接入/);
    assert.match(fs.readFileSync('app/static/index.html','utf8'), /id="modelSkillStatus"/);
});

test('failed reload does not retain a stale skill-loaded claim', async () => {
    let fail = false;
    const {c,elements:e} = setup(async () => {
        if (fail) throw new Error('offline');
        return {configured:true,analysis_skill:{ready:true,label:'网友红领巾公开方法'}};
    });
    await c.loadModelSettings();
    fail = true;
    await c.loadModelSettings();
    assert.match(e.modelSkillStatus.textContent, /无法确认/);
    assert.doesNotMatch(e.modelSkillStatus.textContent, /已接入/);
});
