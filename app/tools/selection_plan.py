"""Interpret the current request without turning job requirements into applicant facts.

This deterministic plan is request-local. It never saves a profile or consults chat
history/current cards to infer a new position code.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.tools import region_resolver
from app.tools.job_category import extract_job_category_preference
from app.tools.province_registry import PROVINCE_NAME_BY_SLUG, get_province_slug

_HISTORY = re.compile(r"之前|此前|上次|刚才|原来|曾经")
_CURRENT = re.compile(r"现在|这次|改为|换成|改看|接下来")
_NEGATION = re.compile(r"不要|不想(?:要|报|看)?|不考虑|不看|排除|不是|非|别看")
_POSITIVE = re.compile(r"推荐|只看|要看|想看|改看|换成|改为|(?<!不)是")
_MAJOR = re.compile("|".join(sorted(("软件工程", "计算机科学与技术", "计算机", "法学", "会计学",
    "会计", "汉语言文学", "土木工程", "临床医学", "经济学", "金融学", "行政管理"), key=len, reverse=True)))
_EDUCATION = re.compile(r"博士(?:研究生)?|硕士(?:研究生)?|研究生|本科|大专")
_IDENTITY = re.compile(r"(?:非应届毕业生|往届(?:毕业)?生?|应届(?:高校)?毕业生|应届生|服务基层项目人员|退役大学生士兵|普通人员)")


def _current_clauses(question: str) -> list[str]:
    clauses = []
    for part in re.split(r"[，。；！？,;!?\n]", str(question or "")):
        part = re.sub(r"\s+", "", part)
        parts = _CURRENT.split(part)
        if len(parts) > 1:
            part = parts[-1]
        elif _HISTORY.search(part) or re.search(r"参加过|考过", part):
            continue
        if part:
            clauses.append(part)
    return clauses


def _negative(clause: str, start: int, end: int) -> bool:
    prefix, suffix = clause[:start], clause[end:]
    negatives = list(_NEGATION.finditer(prefix))
    positives = list(_POSITIVE.finditer(prefix))
    if negatives and (not positives or negatives[-1].start() >= positives[-1].start()):
        return True
    return bool(re.match(r"(?:的)?(?:岗(?:位)?|职位)?(?:都|也|一律|全部)?(?:不要|不看|不考虑)", suffix))


def _is_fact(clause: str, match: re.Match, field: str, shorthand: bool) -> bool:
    if _negative(clause, *match.span()):
        return False
    prefix, suffix = clause[:match.start()], clause[match.end():]
    if re.match(r"(?:专业|类)?(?:岗|职位)|政策|怎么认定|是什么意思|要求|(?:及|或)?以下", suffix):
        return False
    if re.search(r"岗位|职位", prefix) or re.search(r"岗位要求|职位要求|要求|限制|政策|认定", clause):
        return False
    if re.search(r"(?:我(?:是|的(?:专业|学历|身份)(?:是|为)?|学的(?:是)?|读的(?:是)?)|本人(?:是)?|学历(?:是|为)|专业(?:是|为)|身份(?:是|为))", prefix):
        return True
    return shorthand or match.span() == (0, len(clause))


def _code(clauses: list[str]) -> tuple[str, str]:
    found = ("", "")
    for clause in clauses:
        explicit = list(re.finditer(r"(?:职位代码|岗位代码|职位编号|岗位编号|position_code)[:：是为]?([A-Z]{2,}\d{4}[-_]?)?(\d{6,20})(?!\d)", clause, re.I))
        matches = explicit or list(re.finditer(r"(?<!\d)(\d{6,20})(?!\d)", clause))
        for match in matches:
            if not _negative(clause, *match.span()):
                found = (match.group(2) if explicit else match.group(1), match.group())
    return found


def _locations(payload: Any, clauses: list[str], record) -> dict:
    aliases = list(region_resolver._load_region_aliases()) + list(region_resolver._imported_city_aliases())
    for province in PROVINCE_NAME_BY_SLUG.values():
        aliases.append(dict(alias=province, normalized_alias=province, province=province, city="", level="province"))
    current_province = PROVINCE_NAME_BY_SLUG.get(get_province_slug(str(payload.region or "")), str(payload.region or ""))
    if payload.city and payload.city not in {"不限", "全国", "全省"}:
        city = str(payload.city).removesuffix("市")
        aliases.append(dict(alias=city, normalized_alias=city, province=current_province, city=city, level="city"))
    updates = {}
    for clause in clauses:
        matches = []
        for alias in aliases:
            name = alias.get("normalized_alias") or alias.get("alias") or ""
            if name:
                for match in re.finditer(re.escape(name), clause):
                    matches.append((match.start(), match.end(), alias))
        for match in re.finditer(r"全国|不限地区|不限省份|全省|全区|不限城市", clause):
            province = "不限" if match.group() in {"全国", "不限地区", "不限省份"} else ""
            matches.append((match.start(), match.end(), dict(alias=match.group(), province=province, city="不限")))
        covered = -1
        for start, end, alias in sorted(matches, key=lambda item: (item[0], -item[1])):
            if start < covered:
                continue
            covered = end
            if _negative(clause, start, end):
                continue
            prefix = clause[:start]
            backgrounds = list(re.finditer(r"老家|户籍|生源|毕业于|曾在|之前在|家在|住在|来自", prefix))
            destinations = list(re.finditer(r"推荐|只看|想报|报考|要看|想去|岗位在", prefix))
            if backgrounds and (not destinations or backgrounds[-1].start() > destinations[-1].start()):
                continue
            province, city = alias.get("province") or "", alias.get("city") or ""
            previous_province = updates.get("region", current_province)
            if province:
                updates["region"] = province
                record("region", province, alias.get("alias") or province, "scope")
            if city:
                updates["city"] = city
                record("city", city, alias.get("alias") or city, "scope")
            elif province and province != previous_province:
                updates["city"] = ""
    return updates



def build_selection_plan(payload: Any) -> dict:
    """Return safe request updates, job constraints, routing intent and evidence."""
    clauses = _current_clauses(payload.question)
    active = "，".join(clauses)
    evidence, warnings = [], []
    constraints = dict(major_unrestricted=False, excluded_majors=[], education_exclusions=[])

    def record(field, value, text, kind):
        evidence.append(dict(field=field, value=value, text=text, kind=kind))

    updates = _locations(payload, clauses, record)
    for clause in clauses:
        for match in re.finditer(r"国考|国家公务员考试|省考|事业单位|事业编", clause):
            if not _negative(clause, *match.span()):
                value = "国考" if match.group() in {"国考", "国家公务员考试"} else "省考"
                updates["exam_type"] = value if "事业" not in match.group() else ""
                updates["target"] = "事业单位" if "事业" in match.group() else "公务员"
                record("exam_type", updates["exam_type"], match.group(), "scope")
        for match in re.finditer(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?:年|年度)?(?!\d)", clause):
            suffix = clause[match.end():]
            if not _negative(clause, *match.span()) and not re.match(r"(?:届|毕业)", suffix):
                updates["year"] = int(match.group(1))
                record("year", updates["year"], match.group(), "scope")
        unrestricted = re.search(r"不限专业|专业不限|不限制专业", clause)
        if unrestricted and not _negative(clause, *unrestricted.span()):
            constraints["major_unrestricted"] = True
            record("major_unrestricted", True, unrestricted.group(), "job_requirement")
        ceiling = re.search(r"(大专|本科|硕士|博士)(?:及|或)?以下", clause)
        if ceiling and not _negative(clause, *ceiling.span()):
            constraints["education_max"] = ceiling.group(1)
            record("education_max", ceiling.group(1), ceiling.group(), "job_requirement")
        explicit_major = re.search(r"(?:我(?:的)?|本人(?:的)?)?专业(?:是|为|[:：])([\u4e00-\u9fff·]{2,30})", clause)
        explicit_major = explicit_major or re.search(r"(?:我是|我学的是|本人是)([\u4e00-\u9fff·]{2,30}?)专业", clause)
        if explicit_major and not re.search(r"岗位|职位|要求|需要|政策", clause[:explicit_major.start()]):
            value = re.split(r"(?:专业)?(?:学历|本科|大专|硕士|博士|应届|往届|推荐|想|要|能报)", explicit_major.group(1))[0]
            if value not in {"", "不限", "不限制"} and not _negative(clause, *explicit_major.span()):
                updates["major"] = value.removesuffix("专业")
                record("major", updates["major"], explicit_major.group(), "applicant_fact")
        majors, educations, identities = list(_MAJOR.finditer(clause)), list(_EDUCATION.finditer(clause)), list(_IDENTITY.finditer(clause))
        shorthand = (sum(bool(matches) for matches in (majors, educations, identities)) >= 2
                     and not re.search(r"只看|只要|按.*画像|岗位要求|职位要求", clause)
                     and (bool(identities) or not re.search(r"岗位|职位", clause) or "能报" in clause))
        for field, matches in (("major", majors), ("education", educations), ("identity", identities)):
            for match in matches:
                value = match.group()
                if field == "education":
                    value = re.sub(r"(?<=硕士)研究生|(?<=博士)研究生", "", value)
                if field == "identity":
                    value = "往届生" if re.match(r"非应届|往届", value) else "应届生" if "应届" in value else value
                    prefix, suffix = clause[:match.start()], clause[match.end():]
                    nonfresh_self = (value == "往届生" and not _negative(clause, *match.span())
                                     and not re.match(r"(?:的)?(?:岗|职位|政策|怎么|认定|要求)", suffix)
                                     and not re.search(r"政策|要求|认定", clause))
                    negated_fresh_self = value == "应届生" and bool(re.search(r"(?:我|本人)(?:已经)?不是$", prefix))
                    if nonfresh_self or negated_fresh_self:
                        updates[field] = "往届生"
                        record(field, "往届生", match.group(), "applicant_fact")
                        continue
                if _negative(clause, *match.span()):
                    if re.search(r"(?:我|本人)(?:的(?:专业|学历)(?:是)?)?不是$", clause[:match.start()]):
                        if str(getattr(payload, field, "")) == value:
                            updates[field] = ""
                            warnings.append("你已否认当前" + {"major":"专业", "education":"学历", "identity":"身份"}[field] + "，请补充实际条件后再核验资格。")
                        continue
                    key = {"major":"excluded_majors", "education":"education_exclusions"}.get(field)
                    if key and value not in constraints[key]:
                        constraints[key].append(value)
                        record(key, value, match.group(), "job_exclusion")
                elif _is_fact(clause, match, field, shorthand):
                    updates[field] = value
                    record(field, value, match.group(), "applicant_fact")
    position_code, code_text = _code(clauses)
    category = extract_job_category_preference(active)
    continuation = bool(re.search(r"再(?:推荐|找|看|筛)|还有(?:其他|别的|更多)|换一(?:批|些)|继续(?:推荐|找|看|筛)|多推荐", active))
    previous_plan = getattr(payload, "selection_plan", None)
    inherited = False
    if continuation and isinstance(previous_plan, dict):
        category_clear = re.search(r"(?:不限|不限制)(?:岗位类别|岗位类型|部门|单位)|(?:不要|别|不用)只看", active)
        previous_category = previous_plan.get("category_preference")
        if not any(category.values()) and not category_clear and isinstance(previous_category, dict):
            allowed_categories = {"公安", "铁路公安", "海关", "税务", "边检", "缉私"}
            category = {key:[value for value in previous_category.get(key, []) if isinstance(value, str) and value in allowed_categories]
                        for key in ("include", "exclude") if isinstance(previous_category.get(key, []), list)}
            category = {"include":category.get("include", []), "exclude":category.get("exclude", [])}
            if any(category.values()):
                inherited = True
                record("category_preference", deepcopy(category), active, "inherited_constraint")
        explicit_role = any(item["kind"] in {"job_requirement", "job_exclusion"} for item in evidence)
        explicit_role = explicit_role or bool(re.search(r"不限专业|专业不限|不限制专业", active))
        previous_constraints = previous_plan.get("job_constraints")
        if not explicit_role and isinstance(previous_constraints, dict):
            for key in ("major_unrestricted", "excluded_majors", "education_exclusions", "education_max"):
                value = previous_constraints.get(key)
                valid = isinstance(value, bool) if key == "major_unrestricted" else (
                    isinstance(value, list) and all(isinstance(item, str) for item in value)
                    if key.endswith("s") else isinstance(value, str) and value in {"大专", "本科", "硕士", "博士"})
                if valid:
                    constraints[key] = deepcopy(value)
                    inherited = True
                    record(key, deepcopy(value), active, "inherited_constraint")
    constraints["category_preference"] = deepcopy(category)
    if any(category.values()):
        record("category_preference", category, active, "job_requirement")
    if position_code:
        intent = "detail"
        record("position_code", position_code, code_text, "scope")
    elif continuation or re.search(r"推荐|筛选|找(?:一找|找)?|有没有|有无|是否有|只看|要看|想看|能报.*岗|可以报.*岗|不要.*岗", active):
        intent = "recommendation"
    elif re.search(r"进面分|分数线|历年.*分|最低.*分|查分", active):
        intent = "score"
    elif re.search(r"政策|认定|公告|报考条件|资格|要求.*吗", active):
        intent = "policy"
    else:
        intent = {"job_recommendation":"recommendation", "single_job_query":"detail", "score_query":"score", "policy_query":"policy"}.get(str(getattr(payload, "mode", "") or ""))
    return dict(intent=intent, position_code=position_code, updates=updates,
                job_constraints=constraints, category_preference=category, evidence=evidence, warnings=warnings, inherited=inherited)


def apply_selection_plan(payload: Any) -> Any:
    """Apply the plan to a new request object; keep saved profile and text intact."""
    plan = build_selection_plan(payload)
    updates = dict(plan["updates"], selection_plan=plan, selection_constraints=plan["job_constraints"])
    return payload.model_copy(update=updates)
