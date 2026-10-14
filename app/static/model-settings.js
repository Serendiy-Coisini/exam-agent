const MODEL_PRESETS = Object.freeze({
    deepseek: {label: "DeepSeek", base_url: "https://api.deepseek.com", model_name: "deepseek-flash"},
    qwen: {label: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model_name: "qwen-plus"},
    glm: {label: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", model_name: "glm-4.7"},
    kimi: {label: "Kimi", base_url: "https://api.moonshot.cn/v1", model_name: "kimi-k2.6"},
    siliconflow: {label: "硅基流动", base_url: "https://api.siliconflow.cn/v1", model_name: "Qwen/Qwen3-32B"},
});

let modelSettingsBusy = false;
let modelSettingsLoaded = false;

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-model-preset]").forEach(button => {
        button.addEventListener("click", () => applyModelPreset(button.dataset.modelPreset));
    });

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

function applyModelPreset(id) {
    const preset = Object.hasOwn(MODEL_PRESETS, id) ? MODEL_PRESETS[id] : null;
    if (!preset || modelSettingsBusy || !modelSettingsLoaded) return;
    const previous = document.getElementById("modelBaseUrl").value.trim().replace(/\/+$/, "");
    const endpointChanged = previous !== preset.base_url;
    document.getElementById("modelBaseUrl").value = preset.base_url;
    document.getElementById("modelName").value = preset.model_name;
    if (endpointChanged) {
        resetModelSettingsSecrets();
        document.getElementById("modelApiKey").placeholder = "请填写该服务商的 API Key";
    }
    showModelSettingsResult(`已填入 ${preset.label} 配置。${endpointChanged ? "已清空输入框中的密钥，请填写该服务商的 API Key。" : ""}保存后生效；可继续手动修改模型名称。`);
}

function renderModelSettings(data) {
    const emptyLegacy = !data.model_name && !data.api_key_set
        && (!data.base_url || data.base_url.replace(/\/+$/, "") === "https://api.openai.com/v1");
    const display = emptyLegacy ? MODEL_PRESETS.deepseek : data;
    document.getElementById("modelBaseUrl").value = display.base_url || "";
    document.getElementById("modelName").value = display.model_name || "";
    resetModelSettingsSecrets();
    document.getElementById("modelApiKey").placeholder = data.api_key_set
        ? "已保存，留空保留；填写新密钥可替换" : "填写服务商提供的 API Key";
    document.getElementById("modelSettingsStatus").textContent = data.configured
        ? `已配置模型：${data.model_name}。可点击测试连接确认是否可用。`
        : "尚未完整配置模型，分析师当前使用规则分析。";
    const skill = data.analysis_skill;
    document.getElementById("modelSkillStatus").textContent = skill?.ready
        ? `已接入：${skill.label}（版本 ${(skill.revision || "").slice(0, 7)}）。${data.configured
            ? "模型推荐报告、岗位卡片、选岗策略与复盘咨询默认使用此方法；接口不可用时回退规则分析。"
            : "模型未配置，当前仍为规则分析；配置模型后使用此方法。"}`
        : "公开选岗方法未加载，当前使用通用分析规则。请检查项目中的 skill 文件后重新读取。";
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
        document.getElementById("modelSkillStatus").textContent = "暂时无法确认分析方法状态，请重新读取。";
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
