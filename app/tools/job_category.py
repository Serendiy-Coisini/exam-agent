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
    positive = re.compile(r"推荐|只看|想看|要看|想报|改为|换成|但|而|(?<!不)要(?!求)")
    directive = re.compile(r"不要|不想要|不想报|不考虑|不看|排除")
    for clause in re.split(r"[，。；！？,;!?]", text):
        clause = re.split(r"现在|这次|改为|换成|改看|接下来", clause)[-1]
        if re.search(r"之前|此前|上次|刚才|原来", clause):
            continue
        if re.search(r"没有合适|没合适|没有符合|找不到|没找到", clause) and not re.search(
                r"有没有|有无|是否有|还有|推荐|只看|想看|要看|想报|不考虑|不要|排除", clause):
            continue
        previous_end = 0
        previous_excluded = False
        matches = [match for match in _TERMS.finditer(clause)
                   if not (match.group() == "公安" and re.match(r"县|区|市|乡|镇|(?:类)?专业", clause[match.end():]))]
        for match in matches:
            category = _CATEGORY[match.group()]
            prefix = clause[previous_end:match.start()]
            previous_end = match.end()
            if re.fullmatch(r"(?:我)?(?:不要|别|不用)只(?:看|推荐)?", prefix):
                previous_excluded = False
                continue
            suffix = clause[match.end():]
            only_named = bool(re.match(r"(?:的)?(?:以外|之外|外)", suffix)
                              and re.search(r"不要|不看|不考虑", clause))
            only_named = only_named or bool(re.search(r"除了?$", prefix) and re.match(r"其他都不要", suffix))
            directives = list(directive.finditer(clause[:match.start()]))
            positives = list(positive.finditer(clause[:match.start()]))
            excluded = bool(directives and (not positives or directives[-1].start() >= positives[-1].start()))
            excluded = excluded or bool(re.search(r"(?:不是|非).{0,6}$", prefix))
            until_positive = positive.split(suffix, maxsplit=1)[0]
            excluded = excluded or bool(re.search(r"(?:都|全部|一律)(?:不要|不看|不考虑)", until_positive))
            excluded = excluded or bool(re.match(r"(?:的)?(?:不要|不看|不考虑)", suffix))
            if previous_excluded and re.fullmatch(r"(?:和|或者|或|、|以及|及|与)(?:的)?", prefix):
                excluded = True
            if only_named:
                excluded = False
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
    # Only agency names, titles and an explicit police flag are evidence.
    texts = [str(job.get(field) or "").strip() for field in (
        "department", "department_name", "unit", "unit_name", "position", "position_name", "job_title", "job_name")]
    patterns = {
        "税务": r"税务(?:总局|分局|局|所|岗位|岗|管理|稽查|征管)|税务$",
        "海关": r"海关(?:总署|缉私|局|岗位|岗|业务|管理)|海关$",
        "铁路公安": r"铁路公安",
        "缉私": r"缉私",
        "边检": r"边检|边防检查",
        "公安": r"公安(?:局|厅|部|机关|系统|分局|派出所|岗位|岗)|人民警察|民警|警务|警长|警员|^公安$",
    }
    categories = set()
    for category, pattern in patterns.items():
        if any(not re.search(r"(?:非|不是|不属于|无需)(?:铁路|海关)?$", text[:match.start()])
               for text in texts for match in re.finditer(pattern, text)):
            categories.add(category)
    if categories & {"铁路公安", "缉私", "边检"}:
        categories.add("公安")
    flag = str(job.get("police_position") or "").strip().lower()
    if flag in {"是", "1", "true", "人民警察"}:
        categories.add("公安")
    return categories


def matches_job_category(job: dict, preference: dict) -> bool:
    categories = job_categories(job)
    included = set(preference.get("include") or [])
    excluded = set(preference.get("exclude") or [])
    return not (categories & excluded) and (not included or bool(categories & included))
