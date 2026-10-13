const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(apiFetch) {
    const path = 'app/static/model-settings.js';
    assert.ok(fs.existsSync(path), 'Model settings UI should exist');
    const ids = ['modelBaseUrl', 'modelName', 'modelApiKey', 'modelClearKey',
        'modelSettingsStatus', 'modelSettingsResult', 'modelSettingsFields',
        'modelSaveButton', 'modelTestButton', 'modelReloadButton', 'modelSettingsForm'];
    const elements = Object.fromEntries(ids.map(id => [id, { value: '', checked: false,
        disabled: false, hidden: false, textContent: '', placeholder: '', dataset: {},
        reportValidity: () => true, addEventListener: () => {} }]));
    const c = vm.createContext({ document: {getElementById: id => elements[id], addEventListener: () => {}},
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
