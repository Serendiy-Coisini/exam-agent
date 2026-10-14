"""Explicit category preferences constrain candidates before suitability ranking."""
import re

_ALIASES = {
    "铁路公安": ("铁路公安岗位", "铁路公安岗", "铁路公安"),
    "缉私": ("海关缉私岗位", "海关缉私岗", "海关缉私", "缉私岗位", "缉私岗", "缉私"),
    "边检": ("边防检查岗位", "边检岗位", "边检岗", "边防检查", "边检"),
    "公安": ("公安机关岗位", "公安岗位", "公安岗", "人民警察岗位", "人民警察岗",
             "人民警察", "警察岗位", "警察岗", "警务岗位", "警务岗", "民警", "公安"),
    "税务": ("税务局岗位", "税务岗位", "税务岗", "税务局", "税务"),
    "海关": ("海关岗位", "海关岗", "海关"),
}
_CATEGORY = {alias:category for category,aliases in _ALIASES.items() for alias in aliases}
_TERMS = re.compile("|".join(re.escape(alias) for alias in sorted(_CATEGORY,key=len,reverse=True)))


def extract_job_category_preference(question: str) -> dict:
    text = re.sub(r"\s+", "", str(question or ""))
    preference = {"include": [], "exclude": []}
    if re.search(r"(?:不限|不限制)(?:岗位类别|岗位类型|部门|单位)", text):
        return preference
    for clause in re.split(r"[，。；！？,;!?]", text):
        clause = re.split(r"现在|这次|改为|换成|改看|接下来", clause)[-1]
        if re.search(r"之前|此前|上次|刚才|原来", clause):
            continue
        if re.search(r"没有合适|没合适|没有符合|找不到|没找到", clause) and not re.search(
                r"有没有|有无|是否有|还有|推荐|只看|想看|要看|想报|不考虑|不要|排除", clause):
            continue
        previous_end = 0
        previous_excluded = False
        for match in _TERMS.finditer(clause):
            category = _CATEGORY[match.group()]
            prefix = clause[previous_end:match.start()]
            previous_end = match.end()
            # '不要只看公安' broadens the request; it does not exclude police jobs.
            if re.search(r"(?:不要|别|不用)只(?:看|推荐)?$", prefix):
                previous_excluded = False
                continue
            excluded = bool(re.search(r"(?:不要|不想要|不想报|不考虑|不看|排除|不是|非).{0,6}$", prefix))
            if previous_excluded and re.fullmatch(r"(?:和|或者|或|、|以及|及|与)(?:的)?", prefix):
                excluded = True
            items = preference["exclude" if excluded else "include"]
            if category not in items:
                items.append(category)
            previous_excluded = excluded
    return preference


def is_category_job_search(question: str) -> bool:
    text = str(question or "")
    preference = extract_job_category_preference(text)
    return bool(any(preference.values()) and re.search(
        r"有没有|有无|是否有|还有|推荐|找|筛|只看|想看|要看|不考虑|不要|排除", text))


def job_categories(job: dict) -> set[str]:
    # Remarks and descriptions can mention other agencies and are not category evidence.
    text = " ".join(str(job.get(field) or "") for field in (
        "department", "department_name", "unit", "unit_name", "position", "position_name", "job_title", "job_name"))
    categories = set()
    if "税务" in text:
        categories.add("税务")
    if "海关" in text:
        categories.add("海关")
    if "铁路公安" in text:
        categories.add("铁路公安")
    if "缉私" in text:
        categories.add("缉私")
    if re.search(r"边检|边防检查", text):
        categories.add("边检")
    flag = str(job.get("police_position") or "").strip().lower()
    if (re.search(r"公安|人民警察|民警|警务|警长|警员", text)
            or flag in {"是", "1", "true", "人民警察"}):
        categories.add("公安")
    return categories


def matches_job_category(job: dict, preference: dict) -> bool:
    categories = job_categories(job)
    included = set(preference.get("include") or [])
    excluded = set(preference.get("exclude") or [])
    return not (categories & excluded) and (not included or bool(categories & included))
