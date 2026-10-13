let eligibilityQuery = null;
let eligibilityRequestId = 0;
let eligibilityPage = 1;
let eligibilityPages = 0;

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("eligibilityForm");
    form?.addEventListener("submit", handleEligibilitySubmit);
    form?.addEventListener("input", markEligibilityFiltersDirty);
    form?.addEventListener("change", markEligibilityFiltersDirty);
    document.getElementById("eligibilityPrevious")?.addEventListener("click", () => fetchEligibilityPage(eligibilityPage - 1));
    document.getElementById("eligibilityNext")?.addEventListener("click", () => fetchEligibilityPage(eligibilityPage + 1));
    document.getElementById("eligibilityUseProfile")?.addEventListener("click", initializeEligibilityPage);
});

function readEligibilityProfileFields(prefix) {
    const number = name => {
        const value = getValue(prefix + name);
        return value === "" ? null : Number(value);
    };
    const proof = name => {
        const value = getValue(prefix + name);
        return value === "" ? null : value === "yes";
    };
    return {
        gender: getValue(prefix + "Gender"),
        graduation_year: number("GraduationYear"),
        cet4_score: number("Cet4"), cet6_score: number("Cet6"),
        cet4_passed: proof("Cet4Passed"), cet6_passed: proof("Cet6Passed"),
        certificates: getValue(prefix + "Certificates").split(/[,，;；\n]+/).map(v => v.trim()).filter(Boolean),
        certificates_reviewed: document.getElementById(prefix + "CertificatesKnown")?.checked ? true : null,
    };
}

function renderEligibilityProfileFields(prefix, profile) {
    const values = {Gender: profile.gender, GraduationYear: profile.graduation_year,
        Cet4: profile.cet4_score, Cet6: profile.cet6_score,
        Cet4Passed: profile.cet4_passed == null ? "" : (profile.cet4_passed ? "yes" : "no"),
        Cet6Passed: profile.cet6_passed == null ? "" : (profile.cet6_passed ? "yes" : "no"),
        Certificates: normalizeArray(profile.certificates).join("；")};
    for (const [name,value] of Object.entries(values)) {
        const field = document.getElementById(prefix + name);
        if (field) field.value = value ?? "";
    }
    const known = document.getElementById(prefix + "CertificatesKnown");
    if (known) known.checked = profile.certificates_reviewed === true;
}

function initializeEligibilityPage() {
    const province = document.getElementById("eligibilityProvince");
    province.innerHTML = '<option value="">全部已导入省份</option>'
        + [...new Set([...ALL_PROVINCES, userProfile.province].filter(Boolean))]
            .map(p => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");
    province.value = userProfile.province || "";
    document.getElementById("eligibilityCity").value = userProfile.city || "";
    document.getElementById("eligibilityExam").value = userProfile.exam_type || "";
    renderEligibilityProfileFields("eligibility", userProfile);
    document.getElementById("eligibilityProfileSummary").textContent = [
        userProfile.education || "学历未填写", userProfile.degree || "学位未填写",
        userProfile.major || "专业未填写", userProfile.identity || "身份未填写",
        userProfile.political_status || "政治面貌未填写",
    ].join(" · ");
    refreshCustomSelect(province);
    refreshCustomSelect(document.getElementById("eligibilityExam"));
    initializeCustomSelects(document.getElementById("eligibilityForm"));
    markEligibilityFiltersDirty();
}

function markEligibilityFiltersDirty() {
    eligibilityRequestId += 1;
    eligibilityQuery = null;
    document.getElementById("eligibilityPrevious").disabled = true;
    document.getElementById("eligibilityNext").disabled = true;
    document.getElementById("eligibilitySubmit").disabled = false;
    document.getElementById("eligibilitySummary").textContent = "点击按画像筛选全部岗位；修改条件后请重新筛选。";
    document.getElementById("eligibilityResults").innerHTML = "";
    document.getElementById("eligibilityCounts").textContent = "";
    document.getElementById("eligibilityPageInfo").textContent = "尚未查询";
}

function handleEligibilitySubmit(event) {
    event.preventDefault();
    eligibilityQuery = {
        profile: {...userProfile, ...readEligibilityProfileFields("eligibility"),
            grassroots_years: userProfile.grassroots_years == null || userProfile.grassroots_years === "" ? null : Number(userProfile.grassroots_years)},
        province: getValue("eligibilityProvince"), city: getValue("eligibilityCity"),
        exam_type: getValue("eligibilityExam"), year: getValue("eligibilityYear") === "" ? null : Number(getValue("eligibilityYear")),
        check_remarks: document.getElementById("eligibilityRemarks").checked,
        status: getValue("eligibilityStatus"), keyword: getValue("eligibilityKeyword"),
        remark_include: getValue("eligibilityInclude"), remark_exclude: getValue("eligibilityExclude"),
        page_size: Number(getValue("eligibilityPageSize") || 20),
    };
    fetchEligibilityPage(1);
}

async function fetchEligibilityPage(page) {
    if (!eligibilityQuery || page < 1) return;
    const requestId = ++eligibilityRequestId;
    const payload = {...eligibilityQuery, page};
    document.getElementById("eligibilitySubmit").disabled = true;
    document.getElementById("eligibilityPrevious").disabled = true;
    document.getElementById("eligibilityNext").disabled = true;
    document.getElementById("eligibilitySummary").textContent = "正在扫描全部岗位并核验条件…";
    try {
        const data = await apiFetch("/api/v1/jobs/eligibility", {method:"POST", body:JSON.stringify(payload)});
        if (requestId !== eligibilityRequestId) return;
        renderEligibilityResults(data);
    } catch (error) {
        if (requestId !== eligibilityRequestId) return;
        document.getElementById("eligibilitySummary").textContent = `筛选失败：${getErrorText(error)}`;
        document.getElementById("eligibilityResults").innerHTML = "";
    } finally {
        if (requestId === eligibilityRequestId) document.getElementById("eligibilitySubmit").disabled = false;
    }
}

function renderEligibilityResults(data) {
    eligibilityPage = data.page;
    eligibilityPages = data.total_pages;
    document.getElementById("eligibilitySummary").textContent =
        `扫描范围 ${formatNumber(data.scope_total)} 个岗位，基础条件未发现冲突 ${formatNumber(data.basic_candidate_count)} 个；当前显示条件下共有 ${formatNumber(data.total)} 个结果。`;
    document.getElementById("eligibilityCounts").textContent =
        `条件匹配 ${formatNumber(data.counts.matched)} · 待核验 ${formatNumber(data.counts.pending)} · 明确不符合 ${formatNumber(data.counts.unmet)}`;
    document.getElementById("eligibilityPageInfo").textContent = data.total_pages
        ? `第 ${data.page} / ${data.total_pages} 页 · 每页 ${data.page_size} 条` : "没有符合当前显示条件的结果";
    document.getElementById("eligibilityPrevious").disabled = data.page <= 1;
    document.getElementById("eligibilityNext").disabled = data.page >= data.total_pages;
    document.getElementById("eligibilityResults").innerHTML = data.items.length
        ? data.items.map(renderEligibilityJob).join("")
        : '<p class="surface-description">没有符合当前条件的岗位。可以切换“待核验”或“全部”查看原因，或调整地区与备注关键词。</p>';
}

function renderEligibilityJob(job) {
    const labels = {matched:"条件匹配",pending:"待核验",unmet:"明确不符合"};
    const status = labels[job.status] ? job.status : "pending";
    const checks = normalizeArray(job.checks);
    return `<article class="eligibility-job surface-card">
        <div class="eligibility-job-heading"><h2>${escapeHtml(job.job_name || "未命名岗位")}</h2><span class="eligibility-status ${status}">${labels[status]}</span></div>
        <p>${escapeHtml(job.department_name || job.unit_name || "")} · ${escapeHtml(job.unit_name || "")}</p>
        <p class="eligibility-job-meta">${escapeHtml(job.province || "")} ${escapeHtml(job.region || "")} · ${escapeHtml(job.year || "")} ${escapeHtml(job.exam_type || "")} · 招录 ${escapeHtml(job.recruit_count ?? "未提供")} 人</p>
        <p class="eligibility-job-meta">部门代码 ${escapeHtml(job.department_code || "未提供")} · 职位代码 ${escapeHtml(job.job_code || "未提供")}</p>
        <p>${escapeHtml(job.education_requirement || "学历要求未提供")} · ${escapeHtml(job.major_requirement || "专业要求未提供")}</p>
        <details><summary>查看核验依据与备注原文（${checks.length} 项）</summary>
            <ul>${checks.map(c => `<li><strong>${escapeHtml(c.field)} · ${labels[c.status] || "待核验"}</strong><p>${escapeHtml(c.evidence)}</p><small>${escapeHtml(c.reason)}</small></li>`).join("") || "<li>未发现额外资格限制。</li>"}</ul>
            <h3>备注原文</h3><p class="eligibility-original">${escapeHtml(job.remark || "原表未填写备注")}</p>
            ${job.qualification_requirement ? `<h3>其他资格条件</h3><p class="eligibility-original">${escapeHtml(job.qualification_requirement)}</p>` : ""}
            ${job.additional_requirements ? `<h3>其他公开要求</h3><p class="eligibility-original">${escapeHtml(job.additional_requirements)}</p>` : ""}
        </details>
    </article>`;
}
