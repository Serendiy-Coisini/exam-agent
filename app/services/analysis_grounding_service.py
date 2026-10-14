"""Validate visible analysis against structured evidence for the referenced job.

``ground_card(payload, job, analysis, fallback)`` returns a new dictionary and
``ground_report(payload, jobs, report, fallback)`` returns text. Both also return
JSON-safe metadata: source (model/rules/mixed), accepted (some model prose was
retained), reasons (stable reason codes), and repaired_fields. Neither mutates
inputs, queries providers, nor imposes a report length or heading template.

This boundary checks factual claims; it deliberately keeps cautious opinions.
Qualification decisions come from backend checks, never from model wording.
"""
import re
from decimal import Decimal, InvalidOperation


CARD_FIELDS = (
    "why_recommended", "qualification_match", "major_match", "direction_fit",
    "recruit_risk", "competition_data", "verify_before_apply",
)
_METADATA_FIELDS = {"llm_used", "model", "error", "failure_reason", "model_metadata"}
_NUMBER = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百千]+)"
_SCORE_LABEL = r"(?:最低进面分|最高进面分|平均进面分|最低分|最高分|平均分|进面分数|进面分|进面线|入面分|分数线|笔试分|总分|成绩|分数|参考分)"
_SCORE_PREFIX = re.compile(_SCORE_LABEL + r"[^\d，,。；;：:\n零一二两三四五六七八九十百千]{0,12}[：:∶]?\s*(?P<value>" + _NUMBER + r")")
_SCORE_SUFFIX = re.compile(r"(?P<value>" + _NUMBER + r")\s*分(?!钟|析|配|工|别)")
_COUNT_PATTERNS = {
    "applicants": re.compile(r"(?:已报名|报名(?:人数|者)?|报考(?:人数)?|申请人数)[^\d，,。；;\n零一二两三四五六七八九十百千]{0,10}(?P<value>" + _NUMBER + r")\s*(?:人|名)?"),
    "payment": re.compile(r"(?:缴费(?:人数)?)[^\d，,。；;\n零一二两三四五六七八九十百千]{0,10}(?P<value>" + _NUMBER + r")\s*(?:人|名)?"),
    "recruit": re.compile(r"(?:招录(?:人数)?|招考(?:人数)?|招(?:聘|收)?(?:人数)?|计划人数|录用人数|名额)[^\d，,。；;\n零一二两三四五六七八九十百千]{0,8}(?P<value>" + _NUMBER + r")\s*(?:人|名)?"),
    "interview": re.compile(r"(?:进面人数|入围人数|面试人数)[^\d，,。；;\n零一二两三四五六七八九十百千]{0,8}(?P<value>" + _NUMBER + r")\s*(?:人|名)?"),
}
_RATIO = re.compile(r"(?<![A-Za-z0-9_.])(?P<a>\d+(?:\.\d+)?)\s*[：:∶]\s*(?P<b>\d+(?:\.\d+)?)(?![\d.])")
_PROMISE = re.compile(r"稳上|稳进|保录|保证(?:录取|上岸|进面)|承诺(?:录取|上岸)|一定(?:上岸|录取|进面)|必然(?:上岸|录取|进面)|百分百(?:录取|上岸)|100\s*%\s*(?:录取|上岸)")
_BENEFIT = re.compile(r"工资(?:很|更|较)?高|待遇(?:很|更|较)?高|收入(?:很|更|较)?高|福利(?:很好|好)|待遇丰厚|高薪")
_LOW_COMPETITION = re.compile(r"竞争(?:很小|小|较小|较低|低|不激烈)|压力(?:很小|较小|小|较低)|低竞争|容易上岸")
_OVERALL_MATCH = re.compile(r"完全符合|整体符合|符合全部|满足全部|全部满足|全部资格.{0,3}满足|均满足|都满足|全部匹配|资格(?:完全|初步|已经|已)?(?:匹配|符合)|可以(?:直接)?报(?:考|名)?|可(?:直接)?报(?:考|名)|具备报考资格|满足报考条件|符合报考条件|资格条件已满足")
_FIELD_MATCH = {
    "专业": re.compile(r"(?:专业[^，,。；;\n]{0,16}(?:完全符合|符合|完全匹配|匹配明确|匹配|满足|已匹配)|(?:完全符合|完全匹配)[^，,。；;\n]{0,6}专业)"),
    "学历": re.compile(r"学历[^，,。；;\n]{0,12}(?:符合|匹配|满足)"),
    "身份": re.compile(r"身份[^，,。；;\n]{0,12}(?:符合|匹配|满足)"),
}


def ground_card(payload, job, analysis, fallback):
    """Repair unsupported fields; accepted means retained model prose, not a call."""
    job = job if isinstance(job, dict) else {}
    analysis = analysis if isinstance(analysis, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    rules = _rule_card(job)
    fields = list(dict.fromkeys([*CARD_FIELDS, *fallback, *analysis]))
    result, reasons, repaired, model_fields, rule_fields = {}, [], [], [], []
    for field in fields:
        if field in _METADATA_FIELDS:
            continue
        candidate = _text(analysis.get(field))
        rule = _text(fallback.get(field)) or rules.get(field, "请按官方资料进一步核验。")
        if _violations(rule, [job], payload):
            rule = rules.get(field, _rule_summary(job))
        issues = _violations(candidate, [job], payload) if candidate else ["missing_or_invalid_field"]
        if issues:
            result[field] = rule
            repaired.append(field)
            reasons.extend(issues)
            rule_fields.append(field)
        else:
            result[field] = candidate
            (model_fields if candidate != _text(fallback.get(field)) else rule_fields).append(field)
    return result, _metadata(bool(model_fields), bool(rule_fields), reasons, repaired)


def ground_report(payload, jobs, report, fallback):
    """Keep supported sentences and replace invalid claims with scoped rule facts."""
    jobs = [job for job in (jobs or []) if isinstance(job, dict)]
    report, fallback = _text(report), _text(fallback)
    if not report or report == fallback:
        return _safe_fallback(payload, jobs, fallback), _metadata(False, True, [], [])
    kept, reasons, repaired, affected = [], [], [], []
    scope = jobs
    substantive = False
    for index, unit in enumerate(_units(report)):
        explicit = _referenced_jobs(unit, jobs)
        if explicit:
            scope = explicit
        issues = _violations(unit, scope, payload)
        if issues:
            reasons.extend(issues)
            repaired.append("report.sentence[" + str(index) + "]")
            for job in scope:
                if not any(job is existing for existing in affected):
                    affected.append(job)
        else:
            kept.append(unit)
            substantive = substantive or _has_prose(unit, jobs)
    if not repaired:
        return report, _metadata(True, False, [], [])
    if not substantive:
        return _safe_fallback(payload, jobs, fallback), _metadata(False, True, reasons, repaired)
    repair_text = "\n".join(_rule_summary(job) for job in affected)
    if not repair_text:
        repair_text = _safe_fallback(payload, jobs, fallback)
    cleaned = "".join(kept).strip()
    if repair_text:
        cleaned += "\n\n" + repair_text
    return cleaned.strip(), _metadata(True, True, reasons, repaired)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _metadata(model, rules, reasons, repaired):
    return {"source": "mixed" if model and rules else "model" if model else "rules",
            "accepted": bool(model), "reasons": list(dict.fromkeys(reasons)),
            "repaired_fields": list(dict.fromkeys(repaired))}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip().replace(",", "")
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if value and all(c in digits or c in "十百千" for c in value):
        total, current = 0, 0
        for char in value:
            if char in digits:
                current = digits[char]
            else:
                total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                current = 0
        value = str(total + current)
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def _first(job, *fields):
    return next((job[field] for field in fields if job.get(field) is not None and str(job[field]).strip() != ""), None)


def _negated(text, start):
    prefix = re.split(r"[，,。；;\n]|但是|然而|不过|但", text[:start])[-1][-24:]
    return bool(re.search(r"不能|无法|不可|不应|不足以|不要|不宜|不代表|不等于|并非|不是|尚不能|未能|不保证|不承诺|避免|勿", prefix))


def _positive_matches(pattern, text):
    positive = []
    for match in pattern.finditer(text):
        # A qualification phrase may put its negation inside the matched span.
        cautious = re.search(r"不符合|不满足|不匹配|未匹配|未满足|未符合|待核验|是否|不能确认|无法确认|尚未确认", match.group(0))
        tail = re.split(r"[，,。；;\n]", text[match.end():match.end() + 16])[0]
        pending = re.match(r"(?:情况)?(?:仍|尚)?(?:待|需|需要).{0,4}核验", tail)
        prefix = re.split(r"[，,。；;\n]", text[:match.start()])[-1]
        conditional = re.search(r"(?:核验|审核|确认)通过后|(?:若|如果|待).{0,16}(?:符合|满足|通过).{0,6}(?:后|则|才)$", prefix)
        if not cautious and not pending and not conditional and not _negated(text, match.start()):
            positive.append(match)
    return positive


def _status(job, field=None):
    checks = job.get("eligibility_checks")
    checks = checks if isinstance(checks, list) else []
    if field:
        relevant = [item for item in checks if isinstance(item, dict) and item.get("field") == field]
        if relevant:
            states = [item.get("status") for item in relevant]
            return "unmet" if "unmet" in states else "matched" if all(s == "matched" for s in states) else "pending"
        # A field claim requires its own check, not another field\'s overall status.
        return "pending"
    states = [item.get("status") for item in checks if isinstance(item, dict)]
    if job.get("eligibility_status") == "unmet" or "unmet" in states:
        return "unmet"
    if job.get("eligibility_status") == "pending" or "pending" in states:
        return "pending"
    return "matched" if job.get("eligibility_status") == "matched" else "pending"


def _profile_present(payload, field):
    key = {"专业": "major", "学历": "education", "身份": "identity"}[field]
    value = payload.get(key) if isinstance(payload, dict) else getattr(payload, key, None)
    profile = payload.get("profile", {}) if isinstance(payload, dict) else getattr(payload, "profile", {})
    if value is None and isinstance(profile, dict):
        value = profile.get(key)
    return str(value or "").strip() not in {"", "不限", "未设置", "未填写", "请选择", "未知"}


def _metric_values(job, metric):
    aliases = {
        "score_min": ("min_interview_score", "min_score", "review_written_score_min", "candidate_written_score_min"),
        "score_max": ("max_interview_score", "max_score"),
        "score_avg": ("avg_score", "review_written_score_avg"),
        "applicants": ("applicants_count", "applicant_count"),
        "payment": ("payment_count",), "recruit": ("recruit_count", "headcount"),
        "interview": ("interview_count",),
    }
    value = _number(_first(job, *aliases[metric]))
    return set() if value is None else {value}


def _score_metric(text, start):
    prefix = re.split(r"[，,。；;\n]", text[:start])[-1]
    if re.search(r"平均(?:进面)?分|平均成绩", prefix):
        return "score_avg"
    if re.search(r"最高(?:进面)?分|最高成绩", prefix):
        return "score_max"
    return "score_min"


def _ratio(value):
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:[：:∶]\s*(\d+(?:\.\d+)?))?\s*", str(value))
    if not match:
        return None
    denominator = _number(match.group(2) or 1)
    return _number(match.group(1)) / denominator if denominator else None


def _violations(text, jobs, payload):
    if not text:
        return []
    reasons = _unknown_job_references(text, jobs) + _core_fact_violations(text, jobs)
    if _positive_matches(_PROMISE, text):
        reasons.append("unsupported_outcome_promise")
    if _positive_matches(_BENEFIT, text):
        reasons.append("unsupported_salary_or_benefit")
    reasons.extend(_qualification_violations(text, jobs, payload))
    reasons.extend(_requirement_violations(text, jobs))
    if re.search(r"(?:工资|月薪|收入|薪资|待遇)[^，,。；;\n]{0,12}\d+(?:\.\d+)?\s*(?:元|万元|万)", text):
        reasons.append("unsupported_salary_amount")
    occupied = []
    for job in jobs:
        for key in ("position_code", "display_position_code", "full_position_code", "job_id", "year", "score_source_year"):
            token = str(job.get(key) or "").strip()
            if token:
                occupied.extend(match.span() for match in re.finditer(re.escape(token), text))
    for metric, pattern in _COUNT_PATTERNS.items():
        for match in pattern.finditer(text):
            occupied.append(match.span("value"))
            value = _number(match.group("value"))
            scope = _claim_jobs(text, match.start("value"), jobs)
            if not scope or any(value not in _metric_values(job, metric) for job in scope):
                reasons.append("unsupported_" + metric + "_count")
    reverse_counts = {
        "applicants": re.compile(r"(?P<value>" + _NUMBER + r")\s*(?:人|名)(?:考生|人员)?(?:已)?(?:报名|报考)"),
        "recruit": re.compile(r"(?P<value>" + _NUMBER + r")\s*(?:个|名|人)?招录名额"),
    }
    for metric, pattern in reverse_counts.items():
        for match in pattern.finditer(text):
            occupied.append(match.span("value"))
            scope = _claim_jobs(text, match.start("value"), jobs)
            if not scope or any(_number(match.group("value")) not in _metric_values(job, metric) for job in scope):
                reasons.append("unsupported_" + metric + "_count")
    ratio_claims = [(match.span(), match.group(0)) for match in _RATIO.finditer(text)]
    labelled_ratio = re.compile(r"(?:竞争比|报录比|竞争比例)[^\d，,。；;\n]{0,8}(?P<value>\d+(?:\.\d+)?(?:\s*[：:∶]\s*\d+(?:\.\d+)?)?)")
    ratio_claims.extend((match.span("value"), match.group("value")) for match in labelled_ratio.finditer(text))
    job_codes = {str(job.get(key) or "") for job in jobs for key in ("position_code", "display_position_code", "full_position_code", "job_id")}
    for span, claimed_text in ratio_claims:
        # A numeric position code followed by a heading colon is not a ratio.
        if re.split(r"[：:∶]", claimed_text)[0].strip() in job_codes:
            continue
        occupied.append(span)
        claimed = _ratio(claimed_text)
        scope = _claim_jobs(text, span[0], jobs)
        if not scope or any(claimed is None or claimed != _ratio(job.get("competition_ratio")) for job in scope):
            reasons.append("unsupported_competition_ratio")
    for match in re.finditer(r"(?P<value>" + _NUMBER + r")\s*人岗", text):
        scope = _claim_jobs(text, match.start("value"), jobs)
        if not scope or any(_number(match.group("value")) not in _metric_values(job, "recruit") for job in scope):
            reasons.append("unsupported_recruit_count")
    score_claims = {}
    for pattern in (_SCORE_PREFIX, _SCORE_SUFFIX):
        for match in pattern.finditer(text):
            score_claims[match.span("value")] = match
    if re.search(_SCORE_LABEL, text):
        for match in re.finditer(r"(?<![A-Za-z0-9])(?P<value>\d+(?:\.\d+)?)(?![A-Za-z0-9])", text):
            span = match.span("value")
            covered = any(start <= span[0] and span[1] <= end for start, end in [*occupied, *score_claims])
            suffix = text[span[1]:span[1] + 4]
            list_marker = not text[:span[0]].strip(" \t-*") and re.match(r"[.)、．]", suffix)
            if not covered and not list_marker and not re.match(r"\s*(?:年|届|岁|天|次|个月|小时|级|条|页)", suffix):
                score_claims[span] = match
    for match in score_claims.values():
        value = _number(match.group("value"))
        metric = _score_metric(text, match.start("value"))
        scope = _claim_jobs(text, match.start("value"), jobs)
        if not scope or any(value not in _metric_values(job, metric) for job in scope):
            reasons.append("unsupported_" + metric)
        elif any(job.get("score_match_type") not in {None, "", "exact_position_code"} for job in scope):
            start = max((text.rfind(mark, 0, match.start("value")) for mark in "，,。；;\n"), default=-1) + 1
            ends = [text.find(mark, match.end("value")) for mark in "，,。；;\n"]
            end = min((index for index in ends if index >= 0), default=len(text))
            clause = text[start:end]
            official = _positive_matches(re.compile(r"官方|精确匹配|精确进面分"), clause)
            if official or not re.search(r"参考|样本|相近|类似|不等同|不是|非精确", clause):
                reasons.append("score_reference_scope_conflict")
    if _positive_matches(_LOW_COMPETITION, text) and (not jobs or any(
        not _metric_values(job, "applicants") or _ratio(job.get("competition_ratio")) is None for job in jobs
    )):
        reasons.append("competition_data_missing")
    return list(dict.fromkeys(reasons))




def _qualification_subject(text, start, jobs):
    prefix = re.split(r"[，,。；;\n]", text[:start])[-1]
    labels = set(_FIELD_MATCH)
    labels.update(str(check.get("field")) for job in jobs
                  for check in (job.get("eligibility_checks") or [])
                  if isinstance(check, dict) and check.get("field"))
    mentions = [(prefix.rfind(label), label) for label in labels if label in prefix]
    if not mentions:
        return None
    position, field = max(mentions)
    if re.search(r"整体|全部|所有|各项|总体|因此", prefix[position + len(field):]):
        return None
    return field


def _qualification_violations(text, jobs, payload):
    reasons = []
    aliases = {"专业": "major", "学历": "education", "身份": "identity"}
    for match in _positive_matches(_OVERALL_MATCH, text):
        scope = _claim_jobs(text, match.start(), jobs)
        if _qualification_subject(text, match.start(), scope) is None and (
                not scope or any(_status(job) != "matched" for job in scope)):
            reasons.append("qualification_status_conflict")
    labels = set(_FIELD_MATCH)
    labels.update(str(check.get("field")) for job in jobs
                  for check in (job.get("eligibility_checks") or [])
                  if isinstance(check, dict) and check.get("field"))
    for field in labels:
        pattern = _FIELD_MATCH.get(field) or re.compile(
            re.escape(field) + r"[^，,。；;\n]{0,16}(?:符合|匹配|满足)")
        for match in _positive_matches(pattern, text):
            scope = _claim_jobs(text, match.start(), jobs)
            known_profile = field not in aliases or _profile_present(payload, field)
            if not known_profile or not scope or any(_status(job, field) != "matched" for job in scope):
                reasons.append("qualification_" + aliases.get(field, "field") + "_unverified")
    subjects = "|".join(re.escape(field) for field in sorted(labels, key=len, reverse=True))
    state_pattern = re.compile(
        r"(?P<field>" + subjects + r"|整体资格|全部资格|资格)"
        r"(?P<middle>[^，,。；;\n]{0,16}?)"
        r"(?P<state>不符合|不满足|不匹配|未满足|未匹配|未符合|待核验|待确认|尚未确认|需要核验|需核验)")
    for match in state_pattern.finditer(text):
        middle = match.group("middle")
        if (re.search(r"是否|匹配情况|符合情况|满足情况|官方|最终", middle)
                or _negated(text, match.start())):
            continue
        field = match.group("field")
        scope = _claim_jobs(text, match.start(), jobs)
        expected = "unmet" if re.match(r"不|未", match.group("state")) else "pending"
        check_field = field if field in labels else None
        if not scope or any(_status(job, check_field) != expected for job in scope):
            reasons.append("qualification_" + aliases.get(field, "field" if check_field else "status") + "_state_conflict")
    for match in re.finditer(r"(?:没有|无|不存在)(?:任何|明确|已知|资格)?(?:的)?(?:不符合|不满足|不匹配)(?:项|条件)?", text):
        scope = _claim_jobs(text, match.start(), jobs)
        if not _negated(text, match.start()) and any(_status(job) == "unmet" for job in scope):
            reasons.append("qualification_status_conflict")
    return reasons


def _unknown_job_references(text, jobs):
    known = {str(job.get(key) or "").strip() for job in jobs
             for key in ("position_code", "display_position_code", "full_position_code", "job_id")}
    patterns = (
        r"(?:职位|岗位)(?:代码|编号|编码)?[为是：:\s]*(?P<code>[A-Za-z]*\d[A-Za-z0-9_-]{2,})",
        r"(?:推荐|选择|优先考虑)[\s]*(?P<code>[A-Za-z]*\d[A-Za-z0-9_-]{2,})",
        r"(?:^|\n)\s*[-*#]*\s*(?P<code>[A-Za-z]*\d[A-Za-z0-9_-]{2,})(?=[：:\s])",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            if text[match.end("code"):].startswith("年"):
                continue
            if match.group("code") not in known:
                return ["unknown_job_reference"]
    return []


def _asserted_fact(text, match):
    prefix = re.split(r"[，,。；;\n]", text[:match.start()])[-1]
    return not (_negated(text, match.start()) or
                re.search(r"是否|可能|如果|假如|例如|比如|(?:需|请|再|要)(?:核对|核验)", prefix) or
                re.search(r"未知|不详|待核验|待确认|需要核对|以官方", match.group(0)))


def _location_key(value):
    return re.sub(r"(?:壮族|回族|维吾尔)?自治区|特别行政区|省|市|\s", "", str(value or ""))


def _core_fact_violations(text, jobs):
    patterns = {
        "location": r"(?:位于|工作(?:地点|地址)[为是：:]*|(?:所在|工作)(?:省份|城市|地区)[为是：:]*)\s*[“\"]?(?P<value>[^，,。；;\n”\"]+)",
        "year": r"(?P<value>20\d{2})\s*年(?:的)?(?:国考|省考|事业单位|公务员)?(?:招考|招录|招聘|岗位|职位)",
        "exam_type": r"(?P<value>国考|省考|事业单位)(?:岗位|职位|招考|招录)",
        "duties": r"(?:主要职责|岗位职责|工作职责|职位简介)(?:是|为|包括|包含|[：:])\s*[“\"]?(?P<value>[^，,。；;\n”\"]+)",
    }
    extra_patterns = {
        "year": r"(?:招考|招录|招聘|岗位|职位)(?:年度|年份)[为是：:\s]*(?P<value>20\d{2})",
        "exam_type": r"(?:属于|(?:岗位|职位|岗)(?:是|为)|(?<=[A-Za-z0-9])(?:是|为)|考试类型[为是：:]*|招考类型[为是：:]*)\s*(?P<value>国考|省考|事业单位)",
    }
    reasons = []
    for field, pattern in [*patterns.items(), *extra_patterns.items()]:
        for match in re.finditer(pattern, text):
            if not _asserted_fact(text, match):
                continue
            scope = _claim_jobs(text, match.start("value"), jobs)
            claim = match.group("value").strip()
            supported = bool(scope)
            for job in scope:
                if field == "location":
                    evidence = "".join(_location_key(job.get(key)) for key in
                                       ("province", "region", "city", "district", "work_address"))
                    valid = bool(_location_key(claim)) and _location_key(claim) in evidence
                elif field == "year":
                    valid = str(job.get("year") or "") == claim
                elif field == "exam_type":
                    valid = claim in str(job.get("exam_type") or job.get("target") or "")
                else:
                    evidence = str(_first(job, "job_description", "position_description", "duties") or "")
                    valid = bool(claim) and claim in evidence
                supported = supported and valid
            if not supported:
                reasons.append("unsupported_job_" + field)
    return reasons


def _requirement_violations(text, jobs):
    patterns = {
        "major": (r"(?:岗位)?(?:专业要求|要求专业)(?:为|是|[：:])?\s*[“\"]?([^，,。；;\n”\"]+)", ("major_required", "major_requirement")),
        "education": (r"(?:岗位)?(?:学历要求|要求学历)(?:为|是|[：:])?\s*[“\"]?([^，,。；;\n”\"]+)", ("education_required", "education_requirement")),
        "identity": (r"(?:身份要求(?:为|是|[：:])?|岗位(?:只|仅)?限)\s*[“\"]?([^，,。；;\n”\"]+)", ("identity_required", "identity_requirement")),
    }
    reasons = []
    for field, (pattern, aliases) in patterns.items():
        for match in re.finditer(pattern, text):
            if _negated(text, match.start()):
                continue
            claim = re.sub(r"^(?:直接)?(?:包含|涵盖|包括)", "", match.group(1)).strip(" 为是：:“”\" ")
            if re.search(r"核对|核验|未知|不详|待确认|需要|以官方|不限专业不代表", claim):
                continue
            scope = _claim_jobs(text, match.start(), jobs)
            if not scope or any(not claim or claim not in str(_first(job, *aliases) or "") for job in scope):
                reasons.append("unsupported_" + field + "_requirement")
    return reasons


def _format(value):
    parsed = _number(value)
    return format(parsed, "f").rstrip("0").rstrip(".") if parsed is not None and "." in format(parsed, "f") else str(parsed) if parsed is not None else ""


def _rule_card(job):
    status = _status(job)
    qualification = ("当前已知资格存在不满足项，不能确认可报，请核对具体核验结果。" if status == "unmet" else
                     "当前资格仍有待核验项，不能确认整体可报。" if status != "matched" else
                     "后端已知资格初步匹配，最终以官方资格审查为准。")
    major_status = _status(job, "专业")
    major = ("专业条件不满足当前岗位要求。" if major_status == "unmet" else
             "专业资格待核验，请逐字核对官方专业目录。" if major_status != "matched" else
             "专业条件已通过后端初步核验，仍需官方审核。")
    count = _first(job, "recruit_count", "headcount")
    recruit = ("招录" + _format(count) + "人，录取仍有波动风险。") if _number(count) is not None else "招录人数未知，请核对职位表。"
    if _number(count) == 1:
        recruit += "单人岗位波动较大，建议保留备选。"
    score = _first(job, "min_interview_score", "min_score", "review_written_score_min", "candidate_written_score_min")
    score_text = "暂无该岗位精确进面分。"
    if _number(score) is not None:
        if job.get("score_match_type") == "exact_position_code":
            score_text = "历史最低进面分" + _format(score) + "，仅作往年参考。"
        else:
            score_text = "历史或样本参考分" + _format(score) + "，不能认定为该岗位官方最低进面分。"
    applicants = _first(job, "applicants_count", "applicant_count")
    competition = score_text + ("报名人数" + _format(applicants) + "人。" if _number(applicants) is not None else "报名人数未知。")
    competition += "竞争比" + str(job["competition_ratio"]) + "。" if _ratio(job.get("competition_ratio")) is not None else "竞争比未知，不能据此判断竞争强弱。"
    return {"why_recommended": "请先核对资格，再结合岗位职责决定是否保留为候选。", "qualification_match": qualification,
            "major_match": major, "direction_fit": "结合岗位职责和个人职业偏好进一步比较。",
            "recruit_risk": recruit, "competition_data": competition, "verify_before_apply": "核对官方公告、职位表和资格审查材料。"}


def _rule_summary(job):
    rules = _rule_card(job)
    label = str(job.get("position_code") or job.get("position_name") or "当前岗位")
    return label + "：" + rules["qualification_match"] + rules["recruit_risk"] + rules["competition_data"]


def _units(text):
    return re.findall(r"[^\n。！？；;]+[。！？；;]*(?:\n|$)?|\n", text)



def _reference_occurrences(text, jobs):
    """Prefer explicit IDs over shared job titles when binding a claim."""
    for keys in (("position_code", "display_position_code", "full_position_code", "job_id"),
                 ("position_name", "position")):
        occurrences, seen = [], set()
        for index, job in enumerate(jobs):
            for key in keys:
                token = str(job.get(key) or "").strip()
                if not token:
                    continue
                pattern = (r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])"
                           if token.isascii() else re.escape(token))
                for match in re.finditer(pattern, text):
                    identity = (*match.span(), index)
                    if identity not in seen:
                        seen.add(identity)
                        occurrences.append((match.start(), match.end(), job))
        if occurrences:
            return sorted(occurrences, key=lambda item: (item[0], item[1]))
    return []


def _claim_jobs(text, start, jobs):
    """Bind local claims; coordinated IDs retain their collective scope."""
    clause_start = max((text.rfind(mark, 0, start) for mark in "，,。；;\n"), default=-1) + 1
    ends = [text.find(mark, start) for mark in "，,。；;\n"]
    clause_end = min((end for end in ends if end >= 0), default=len(text))
    local = _referenced_jobs(text[clause_start:clause_end], jobs)
    if len(local) == 1:
        return local
    before = [item for item in _reference_occurrences(text, jobs) if item[1] <= start]
    if not before:
        return local or jobs
    group = [before[-1]]
    for item in reversed(before[:-1]):
        between = text[item[1]:group[0][0]]
        if item[:2] == group[0][:2] or re.fullmatch(r"[\s、,，/和与及的]*(?:以及)?[\s、/和与及的]*", between):
            group.insert(0, item)
        else:
            break
    return [job for job in jobs if any(job is item[2] for item in group)]

def _referenced_jobs(text, jobs):
    references = _reference_occurrences(text, jobs)
    return [job for job in jobs if any(job is item[2] for item in references)]


def _has_prose(text, jobs):
    for job in jobs:
        for key in ("position_code", "display_position_code", "full_position_code", "job_id", "position_name", "position"):
            token = str(job.get(key) or "")
            if token:
                text = text.replace(token, "")
    return bool(re.search(r"[\u4e00-\u9fff]{4}", text))


def _safe_fallback(payload, jobs, fallback):
    scope = jobs
    for unit in _units(fallback):
        scope = _referenced_jobs(unit, jobs) or scope
        if _violations(unit, scope, payload):
            break
    else:
        if fallback:
            return fallback
    return "\n".join(_rule_summary(job) for job in jobs) or "现有证据不足，请核对官方公告和资格条件。"
