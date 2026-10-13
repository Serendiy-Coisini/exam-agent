let modelSettingsBusy = false;
let modelSettingsLoaded = false;

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("modelSettingsForm")?.addEventListener("submit", handleModelSettingsSave);
    document.getElementById("modelTestButton")?.addEventListener("click", handleModelSettingsTest);
    document.getElementById("modelReloadButton")?.addEventListener("click", loadModelSettings);
    document.getElementById("modelClearKey")?.addEventListener("change", handleModelKeyClearChange);
});

function setModelSettingsBusy(busy) {
    modelSettingsBusy = busy;
    document.getElementById("modelSettingsFields").disabled = busy || !modelSettingsLoaded;
    document.getElementById("modelSaveButton").disabled = busy || !modelSettingsLoaded;
    document.getElementById("modelTestButton").disabled = busy || !modelSettingsLoaded;
    document.getElementById("modelReloadButton").disabled = busy;
}

function showModelSettingsResult(message, kind = "info") {
    const element = document.getElementById("modelSettingsResult");
    element.textContent = message;
    element.dataset.kind = kind;
    element.hidden = !message;
}

function resetModelSettingsSecrets() {
    const input = document.getElementById("modelApiKey");
    if (input) {
        input.value = "";
        input.disabled = false;
    }
    const clear = document.getElementById("modelClearKey");
    if (clear) clear.checked = false;
}

function renderModelSettings(data) {
    document.getElementById("modelBaseUrl").value = data.base_url || "";
    document.getElementById("modelName").value = data.model_name || "";
    resetModelSettingsSecrets();
    document.getElementById("modelApiKey").placeholder = data.api_key_set
        ? "已保存，留空保留；填写新密钥可替换" : "填写服务商提供的 API Key";
    document.getElementById("modelSettingsStatus").textContent = data.configured
        ? `已配置模型：${data.model_name}。可点击测试连接确认是否可用。`
        : "尚未完整配置模型，分析师当前使用规则分析。";
}

async function loadModelSettings() {
    if (modelSettingsBusy) return;
    modelSettingsLoaded = false;
    resetModelSettingsSecrets();
    setModelSettingsBusy(true);
    showModelSettingsResult("正在读取模型设置…");
    try {
        const data = await apiFetch("/api/v1/settings/model");
        renderModelSettings(data);
        modelSettingsLoaded = true;
        showModelSettingsResult("");
    } catch (error) {
        document.getElementById("modelSettingsStatus").textContent = "暂时无法读取配置。";
        showModelSettingsResult(`读取失败：${getErrorText(error)}。处理后点击重新读取。`, "error");
    } finally {
        setModelSettingsBusy(false);
    }
}

function readModelSettingsForm() {
    return {
        base_url: document.getElementById("modelBaseUrl").value.trim(),
        model_name: document.getElementById("modelName").value.trim(),
        api_key: document.getElementById("modelApiKey").value.trim(),
        clear_api_key: document.getElementById("modelClearKey").checked,
    };
}

function handleModelKeyClearChange() {
    const clear = document.getElementById("modelClearKey").checked;
    const input = document.getElementById("modelApiKey");
    if (clear) input.value = "";
    input.disabled = clear;
}

async function handleModelSettingsSave(event) {
    event.preventDefault();
    if (modelSettingsBusy || !modelSettingsLoaded
        || !document.getElementById("modelSettingsForm").reportValidity()) return;
    const payload = readModelSettingsForm();
    setModelSettingsBusy(true);
    showModelSettingsResult("正在保存…");
    try {
        const data = await apiFetch("/api/v1/settings/model", {method: "PUT", body: JSON.stringify(payload)});
        renderModelSettings(data);
        showModelSettingsResult(data.configured
            ? "设置已保存并立即生效。可以返回分析师开始咨询。"
            : "设置已保存并立即生效。未配置密钥，分析师将使用规则分析。", "success");
    } catch (error) {
        showModelSettingsResult(`保存失败：${getErrorText(error)}`, "error");
    } finally {
        setModelSettingsBusy(false);
    }
}

async function handleModelSettingsTest() {
    if (modelSettingsBusy || !modelSettingsLoaded
        || !document.getElementById("modelSettingsForm").reportValidity()) return;
    const payload = readModelSettingsForm();
    setModelSettingsBusy(true);
    showModelSettingsResult("正在测试连接，请稍候…");
    try {
        const data = await apiFetch("/api/v1/settings/model/test", {method: "POST", body: JSON.stringify(payload)});
        showModelSettingsResult(data.ok
            ? `${data.message} 本次测试未保存设置，修改后请点击保存。`
            : data.message || "连接失败，请检查模型配置。", data.ok ? "success" : "error");
    } catch (error) {
        showModelSettingsResult(`测试失败：${getErrorText(error)}`, "error");
    } finally {
        setModelSettingsBusy(false);
    }
}
