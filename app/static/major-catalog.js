let majorCatalogData = null;
let majorCatalogLoading = null;

document.addEventListener("DOMContentLoaded", () => {
    for (const prefix of ["profile", "eligibility"]) {
        document.getElementById(prefix + "Major")?.addEventListener("change", () => syncMajorFromName(prefix));
        document.getElementById(prefix + "Major")?.addEventListener("input", () => {
            const name = document.getElementById(prefix + "Major").value.trim();
            if (normalizeArray(majorCatalogData?.entries).some(row => row.name === name)) syncMajorFromName(prefix);
        });
        document.getElementById(prefix + "MajorCode")?.addEventListener("change", () => syncMajorFromCode(prefix));
        document.getElementById(prefix + "MajorEducation")?.addEventListener("change", () => refreshMajorCatalogFields(prefix));
        document.getElementById(prefix + "MajorDiscipline")?.addEventListener("change", () => {
            document.getElementById(prefix + "MajorCategory").value = "";
            document.getElementById(prefix + "Major").value = "";
            document.getElementById(prefix + "MajorCode").value = "";
            refreshMajorCatalogFields(prefix);
        });
        document.getElementById(prefix + "MajorCategory")?.addEventListener("change", () => {
            document.getElementById(prefix + "Major").value = "";
            document.getElementById(prefix + "MajorCode").value = "";
            refreshMajorCatalogFields(prefix);
        });
    }
    document.getElementById("profileEducation")?.addEventListener("change", () => refreshMajorCatalogFields("profile"));
    loadMajorCatalog();
});

function majorFormStage(prefix) {
    const explicit = document.getElementById(prefix + "MajorEducation")?.value;
    return explicit || document.getElementById("profileEducation")?.value || userProfile.education || "";
}

function majorCatalogNotice(prefix, message) {
    const target = document.getElementById(prefix + "MajorCatalogStatus");
    if (target) target.textContent = message;
}

function fillMajorCatalogSelect(prefix, suffix, values, selected) {
    const field = document.getElementById(prefix + suffix);
    if (!field) return;
    const choices = [...new Set(values.filter(Boolean))];
    if (selected && !choices.includes(selected)) choices.push(selected);
    field.innerHTML = '<option value="">未选择</option>' + choices.map(value =>
        `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
    field.value = selected || "";
    if (typeof refreshCustomSelect === "function") refreshCustomSelect(field);
}

function refreshMajorCatalogFields(prefix) {
    const discipline = document.getElementById(prefix + "MajorDiscipline")?.value || "";
    const category = document.getElementById(prefix + "MajorCategory")?.value || "";
    const entries = normalizeArray(majorCatalogData?.entries);
    const undergraduate = ["本科", "普通本科", "大学本科"].includes(majorFormStage(prefix));
    const scoped = undergraduate ? entries : [];
    fillMajorCatalogSelect(prefix, "MajorDiscipline", scoped.map(row => row.discipline_name), discipline);
    fillMajorCatalogSelect(prefix, "MajorCategory", scoped.filter(row => !discipline || row.discipline_name === discipline).map(row => row.category_name), category);
    const options = document.getElementById(prefix + "MajorOptions");
    if (options) options.innerHTML = scoped.filter(row => (!discipline || row.discipline_name === discipline)
        && (!category || row.category_name === category)).map(row =>
        `<option value="${escapeHtml(row.name)}" label="${escapeHtml(row.code)}"></option>`).join("");
    if (majorCatalogData?.status === "ready") {
        majorCatalogNotice(prefix, `已加载 ${majorCatalogData.source_file || majorCatalogData.source_basename || "指定目录"} · ${entries.length} 个专业。${undergraduate ? "仅按本目录匹配。" : "该目录适用于普通本科，其他学历专业保留待核验。"}`);
    } else {
        majorCatalogNotice(prefix, "指定专业目录尚未加载或校验未通过；受专业限制的岗位保留待核验。");
    }
}

function renderMajorCatalogFields(prefix, profile) {
    fillMajorCatalogSelect(prefix, "MajorDiscipline", [], profile.major_discipline || "");
    fillMajorCatalogSelect(prefix, "MajorCategory", [], profile.major_category || "");
    const major = document.getElementById(prefix + "Major");
    if (major) major.value = profile.major || "";
    refreshMajorCatalogFields(prefix);
}

function applyMajorCatalogEntry(prefix, row) {
    document.getElementById(prefix + "Major").value = row.name;
    document.getElementById(prefix + "MajorCode").value = row.code;
    fillMajorCatalogSelect(prefix, "MajorDiscipline", [], row.discipline_name);
    fillMajorCatalogSelect(prefix, "MajorCategory", [], row.category_name);
    refreshMajorCatalogFields(prefix);
    majorCatalogNotice(prefix, `目录精确匹配：${row.discipline_name} → ${row.category_name || "无专业类层级"} → ${row.name}（${row.code}），来源第 ${row.source_page} 页。`);
}

function syncMajorFromName(prefix) {
    refreshMajorCatalogFields(prefix);
    if (majorCatalogData?.status !== "ready" || !["本科", "普通本科", "大学本科"].includes(majorFormStage(prefix))) return;
    const name = document.getElementById(prefix + "Major").value.trim();
    const matches = normalizeArray(majorCatalogData.entries).filter(row => row.name === name);
    if (matches.length === 1) {
        applyMajorCatalogEntry(prefix, matches[0]);
    } else if (name) {
        document.getElementById(prefix + "MajorCode").value = "";
        fillMajorCatalogSelect(prefix, "MajorDiscipline", [], "");
        fillMajorCatalogSelect(prefix, "MajorCategory", [], "");
        majorCatalogNotice(prefix, "目录中未找到唯一的完整专业名称，请核对原目录；相关岗位保留待核验。");
    }
}

function syncMajorFromCode(prefix) {
    refreshMajorCatalogFields(prefix);
    if (majorCatalogData?.status !== "ready" || !["本科", "普通本科", "大学本科"].includes(majorFormStage(prefix))) return;
    const code = document.getElementById(prefix + "MajorCode").value.trim().toUpperCase();
    const matches = normalizeArray(majorCatalogData.entries).filter(row => row.code === code);
    if (matches.length === 1) applyMajorCatalogEntry(prefix, matches[0]);
    else if (code) majorCatalogNotice(prefix, "目录中未找到该完整专业代码，请保留 T/K 后缀并核对原目录。");
}

async function loadMajorCatalog(afterAuthentication = false) {
    if (majorCatalogLoading && afterAuthentication) await majorCatalogLoading;
    if (majorCatalogLoading) return majorCatalogLoading;
    majorCatalogLoading = (async () => {
        try {
            const data = await apiFetch("/api/v1/jobs/major-catalog");
            majorCatalogData = data;
        } catch (_) {
            majorCatalogData = {status: "missing", entries: []};
        }
        for (const prefix of ["profile", "eligibility"]) syncMajorFromName(prefix);
    })();
    try { await majorCatalogLoading; } finally { majorCatalogLoading = null; }
}
