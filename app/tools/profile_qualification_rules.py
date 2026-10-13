"""Pure, conservative three-valued checks for reusable profile qualifications.

Only recognized spans are consumed. Unknown text is returned to the existing
remark parser; ambiguous alternatives/negations yield one pending whole clause.
"""
import math
import re

from app.tools.province_registry import PROVINCE_NAME_BY_SLUG


PROJECT_ALIASES = {
    '大学生村官': '大学生村官',
    '农村义务教育阶段学校教师特设岗位计划': '特岗教师', '特岗教师计划': '特岗教师', '特岗教师': '特岗教师',
    '三支一扶计划': '三支一扶', '三支一扶': '三支一扶',
    '大学生志愿服务西部计划': '西部计划', '西部计划': '西部计划',
}
PROJECTS = tuple(set(PROJECT_ALIASES.values()))
PROJECT_PATTERN = re.compile('|'.join(re.escape(name) for name in sorted(PROJECT_ALIASES, key=len, reverse=True)))
PROVINCE_START = re.compile('|'.join(re.escape(name) + r'(?:壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区|省|市)' for name in PROVINCE_NAME_BY_SLUG.values()))


def _project_values(profile):
    value = profile.get('service_projects')
    return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()


def _soldier_project_status(profile):
    values = ['matched' if profile.get(key) is True else ('unmet' if profile.get(key) is False else 'pending')
              for key in ('retired_soldier', 'retired_college_graduate')]
    years = _number(profile.get('military_years'))
    values.append('pending' if years is None else ('matched' if years >= 5 else 'unmet'))
    return _combine(values)
NUMBER = r'(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]+)'
OR = re.compile(r'或者|或|任一|任选|其中一|之一|也可')
HOUSE = re.compile(r'(?:本省|本市|本县|本区|本地|当地|(?:(?!户籍|户口|且|并且|同时)[\u4e00-\u9fff]){1,16}(?:自治区|省|市|县|区))(?:户籍|户口)')
FRESH = re.compile(r'(?:(?:20\d{2})年?|当年|本年度|本年|当年度)?(?:应届(?:普通)?(?:高校|大学|高等院校)?毕业生|应届生|应届)|20\d{2}届(?:高校)?毕业生')
LAW_A = re.compile(r'(?:A\s*类(?:法律职业资格|司法考试)(?:资格)?(?:证书|证)?|(?:法律职业资格|司法考试)(?:资格)?(?:证书|证)?\s*[（(]?\s*A\s*类?\s*[）)]?(?:资格证书|证书|证)?)', re.I)
BOILERPLATE = re.compile(r'满足其中一项即可|满足任一项即可|(?:高校)?毕业生人员|(?:项目)?人员|报考人员|仅限|只限|限|须|必须|需|应|持有|取得|获得|具备|具有|有|要求|报考|考生|且|同时|并且|并|和|及|的|可报|[\s，,、:：;；。()（）]+')


def _check(field, status, evidence, reason):
    return dict(field=field, status=status, evidence=evidence, reason=reason)


def _combine(statuses):
    statuses = list(statuses)
    return 'unmet' if 'unmet' in statuses else ('pending' if 'pending' in statuses else 'matched')


def _number(value):
    if isinstance(value, bool) or value is None or str(value).strip() == '':
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 60 else None


def _chinese_number(value):
    if re.fullmatch(r'\d+(?:\.\d+)?', value):
        return float(value)
    digits = dict(zip('一二两三四五六七八九', (1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if value in digits:
        return digits[value]
    if value == '十':
        return 10
    if value.count('十') == 1:
        tens, units = value.split('十')
        if (not tens or tens in digits) and (not units or units in digits):
            return digits.get(tens, 1) * 10 + digits.get(units, 0)
    return None


def _duration(required, value, field):
    actual = _number(value)
    residue = re.sub(NUMBER, '', required)
    residue = re.sub(r'基层工作(?:经历|经验|年限)?|基层经历|(?:在)?(?:军队)?服役|服务|不得少于|不低于|不少于|不小于|不超过|不高于|不满|大于|高于|超过|少于|低于|小于|至少|恰好|正好|等于|及以上|以上|以下|以内|年|满|至|到|含|[\s（()）,，≥≤<>—~～-]', '', residue)
    if residue:
        return _check(field, 'pending', required, '年限要求存在未识别的限定，需人工核验')
    numbers = re.findall(NUMBER, required)
    if not numbers or actual is None:
        return _check(field, 'pending', required, '年限未填写或要求无法明确解析')
    bounds = [_chinese_number(n) for n in numbers]
    if None in bounds or len(bounds) > 2:
        return _check(field, 'pending', required, '年限表达需人工核验')
    if len(bounds) == 2:
        if re.search(r'至|到|[-—~～]', required) and bounds[0] <= bounds[1]:
            accepted = bounds[0] <= actual <= bounds[1]
        else:
            return _check(field, 'pending', required, '多重年限表达需人工核验')
    else:
        cleaned = re.sub(r'不得少于|不少于|不低于|不小于|不满|不超过|不高于', '', required)
        if re.search(r'恰好|正好|等于', required):
            accepted = actual == bounds[0]
        elif re.search(r'不超过|不高于|以内|以下|≤', required):
            accepted = actual <= bounds[0]
        elif re.search(r'不满|少于|小于|低于|<', cleaned) or '不满' in required:
            accepted = actual < bounds[0]
        elif re.search(r'大于|超过|高于|>', cleaned) and not re.search(r'含|≥', required):
            accepted = actual > bounds[0]
        else:
            accepted = actual >= bounds[0]
    return _check(field, 'matched' if accepted else 'unmet', required, f'画像填写：{actual:g}年')


def evaluate_grassroots_requirement(required, profile):
    """Evaluate the dedicated job grassroots-years field, including zero."""
    required = str(required or '').strip()
    if required in {'不限', '无限制', '无', '无要求', '不限制', '不要求'}:
        return _check('基层经历', 'matched', required, '岗位未限制基层工作年限')
    if OR.search(required) or re.search(r'除|非|优先|原则上', required):
        return _check('基层经历', 'pending', required, '附加或替代关系需人工核验')
    return _duration(required, profile.get('grassroots_years'), '基层经历')


def _year(value):
    try:
        year = int(value)
    except (ValueError, TypeError):
        return None
    return year if 2000 <= year <= 2100 else None


def evaluate_basic_identity(required, profile, job):
    """Explicit current-year status differs from retained fresh-graduate status."""
    required = str(required or '').strip()
    scope = profile.get('fresh_graduate_scope') or ''
    identity = str(profile.get('identity') or '').strip()
    if required in {'不限', '无限制', '身份不限', '无', '无要求', '不限制'}:
        return _check('身份', 'matched', required, '岗位未限制毕业生身份')
    if re.search(r'服务基层项目|三支一扶|特岗教师|大学生村官|西部计划|退役.*士兵', required):
        checks, remaining = consume_qualification_clause(required, profile, job)
        return _check('身份', _combine(item['status'] for item in checks) if checks and not remaining else 'pending', required, '依据明确的项目或退役身份核验')
    if OR.search(required) or re.search(r'非|不得|不接受|除|优先|原则上', required):
        return _check('身份', 'pending', required, '身份替代或否定关系需人工核验')
    is_fresh = bool(FRESH.fullmatch(required))
    if not is_fresh:
        if required in {'往届', '往届生', '非应届毕业生'} and scope:
            status = 'matched' if scope == 'not_fresh' else 'unmet'
        else:
            status = 'matched' if identity and identity == required else 'pending'
        return _check('身份', status, required, f'画像填写：{identity or scope or "未填写"}')
    explicit = re.search(r'20\d{2}', required)
    current_only = bool(explicit or re.search(r'当年|本年|本年度|当年度', required))
    if not scope:
        if identity in {'应届', '应届生', '应届毕业生', '应届高校毕业生'}:
            scope = 'current_year'
        elif identity in {'往届', '往届生', '非应届毕业生'}:
            scope = 'not_fresh'
    if scope == 'not_fresh' or (current_only and scope == 'retained'):
        status = 'unmet'
    elif scope == 'retained':
        status = 'matched'
    elif scope != 'current_year':
        status = 'pending'
    else:
        graduation = _year(profile.get('graduation_year'))
        job_year = _year(job.get('year'))
        required_year = int(explicit[0]) if explicit else job_year
        if graduation is None or required_year is None:
            status = 'pending'
        elif graduation != required_year or (job_year is not None and graduation != job_year):
            status = 'unmet'
        else:
            status = 'matched'
    return _check('身份', status, required, f'毕业生范围：{scope or "未填写"}；毕业年份：{profile.get("graduation_year") or "未填写"}')


def _location_name(value, level):
    value = str(value or '').strip()
    if level == 'province':
        value = PROVINCE_NAME_BY_SLUG.get(value, value)
    if level == 'county':
        return value
    endings = {'province': r'(?:省|市|壮族自治区|回族自治区|维吾尔自治区|自治区)$',
               'city': r'市$'}
    return re.sub(endings[level], '', value)


def _explicit_job_locations(job):
    result = dict(job)
    for key in ('province', 'city', 'county', 'district'):
        if str(result.get(key) or '') in {'全国', '国家', '不限', '不限制'}:
            result[key] = ''
    region = str(job.get('region') or '').strip()
    if not region or re.search(r'[、,，;；/]|或|及|全国', region) or len(re.findall(r'市', region)) > 1 or len(re.findall(r'(?:县|区|旗)(?!域)', re.sub(r'自治区', '', region))) > 1:
        return result
    province = PROVINCE_START.match(region)
    if province and not result.get('province'):
        result['province'] = province[0]
    city = re.match(r'^(?:.*?(?:省|自治区))?([^省市县区]+市)', region)
    if city:
        if not result.get('city'): result['city'] = city[1]
        tail = region[city.end():]
    else:
        tail = re.sub(r'^.*?(?:省|自治区)', '', region)
    county = re.match(r'([^市县区]+(?:自治县|县|区|旗))', tail)
    if county and county[1] not in {'市辖区', '城区', '辖区'}:
        if not result.get('county'): result['county'] = county[1]
    return result


def _household(required, profile, job, negative=False):
    job = _explicit_job_locations(job)
    location = re.sub(r'(?:户籍|户口)$', '', required)
    location = re.sub(r'^(?:必须|仅限|只限|具有|具备|须|需|限|为|非|有)+', '', location)
    if re.search(r'工作|经历|资格|证书|要求|人员|取得|具有|需|须|基层', location):
        return _check('户籍', 'pending', required, '户籍地点与其他条件的边界未明确，需人工核验')
    if location in {'本省', '本市', '本县', '本区', '本地', '当地'}:
        if location == '本省':
            level, expected = 'province', job.get('province') or job.get('region')
        elif location == '本市':
            level, expected = 'city', job.get('city')
        elif location in {'本县', '本区'}:
            level, expected = 'county', job.get('county') or job.get('district')
        else:
            options = [('county', job.get('county') or job.get('district')), ('city', job.get('city'))]
            level, expected = next(((key, value) for key, value in options if value), ('province', ''))
    else:
        level = 'province' if location.endswith(('省', '自治区')) else ('city' if location.endswith('市') else 'county')
        expected = location
    if location not in {'本省', '本市', '本县', '本区', '本地', '当地'}:
        parts = re.findall(r'.+?(?:自治区|省|市|县|区)', location)
        if len(parts) > 1 and ''.join(parts) == location:
            statuses = []
            for part in parts:
                part_level = 'province' if part.endswith(('省', '自治区')) else ('city' if part.endswith('市') else 'county')
                supplied = profile.get('hukou_' + part_level)
                statuses.append('pending' if not supplied else ('matched' if _location_name(supplied, part_level) == _location_name(part, part_level) else 'unmet'))
            combined = _combine(statuses)
            if negative and combined != 'pending':
                combined = 'unmet' if combined == 'matched' else 'matched'
            return _check('户籍', combined, required, '按明确的省、市、县层级逐项核验')
    if level == 'province' and not job.get('province') and location in {'本省', '本地', '当地'}:
        region = str(expected or '')
        if not region.endswith(('省', '自治区')) and _location_name(region, 'province') not in PROVINCE_NAME_BY_SLUG.values():
            expected = ''
    actual = profile.get('hukou_' + level)
    # A nation-wide region is not a local household-registration requirement.
    if not expected or str(expected) in {'全国', '国家', '不限'} or not actual:
        status = 'pending'
    else:
        scopes = [(level, actual, expected)]
        if location in {'本省', '本市', '本县', '本区', '本地', '当地'}:
            parents = ['province', 'city'] if level == 'county' else (['province'] if level == 'city' else [])
            scopes.extend((parent, profile.get('hukou_' + parent), job.get(parent)) for parent in parents if job.get(parent))
        status = _combine('pending' if not supplied else ('matched' if _location_name(supplied, key) == _location_name(wanted, key) else 'unmet')
                          for key, supplied, wanted in scopes)
        if negative and status != 'pending':
            status = 'unmet' if status == 'matched' else 'matched'
    return _check('户籍', status, required, f'要求{level}：{expected or "地点未明确"}；户籍：{actual or "未填写"}')


def _tri_state(field, value, evidence, label):
    status = 'matched' if value is True else ('unmet' if value is False else 'pending')
    return _check(field, status, evidence, label)


def _law_value(profile):
    value = profile.get('law_a_certificate')
    if value is not None:
        return value if isinstance(value, bool) else None
    supplied = profile.get('certificates') or []
    if any(LAW_A.fullmatch(str(cert).strip()) for cert in supplied):
        return True
    if profile.get('certificates_reviewed') is True:
        return False
    return None


def consume_qualification_clause(clause, profile, job):
    """Return recognized checks and exact unconsumed text for legacy rules."""
    clause = str(clause or '').strip()
    structured = re.fullmatch(r'(服务基层项目工作经历|服务基层项目|户籍要求|户口要求|户籍|户口|基层工作最低年限|应届要求|法律职业资格)[:：]\s*(.+)', clause)
    if structured:
        label, value = structured[1], structured[2].strip()
        if value in {'不限', '无限制', '无', '无要求', '不限制', '不要求'}:
            return [_check(label, 'matched', clause, '岗位未限制该项资格')], ''
        if label == '基层工作最低年限':
            return [evaluate_grassroots_requirement(value, profile)], ''
        if label in {'户籍要求', '户口要求', '户籍', '户口'} and not re.search(r'户籍|户口', value):
            value += '户籍'
        if label == '法律职业资格' and re.fullmatch(r'[ABC]类?', value, re.I):
            value = '法律职业资格' + value
        checks, remaining = consume_qualification_clause(value, profile, job)
        if checks and not remaining:
            return checks, ''
        # Keep the original label when interpretation remains incomplete.
        return [], clause
    supported = re.search(r'户籍|户口|基层工作|基层经历|三支一扶|特岗教师|教师特设岗位计划|大学生村官|西部计划|服务基层项目|退役(?:大学生|高校毕业生)?士兵|应届|20\d{2}届|法律职业资格|司法考试|服务期满|考核合格|服务满|服役', clause)
    if not supported:
        return [], clause
    pending = lambda: ([_check('资格附加要求', 'pending', clause, '存在未明确的否定、作用范围或跨条件替代关系，需人工核验')], '')
    # These words can change the scope of every otherwise recognizable token.
    if re.search(r'优先|建议|原则上|除|不得报|不接受|不能|生源|若|如果|对于|本科生|研究生|可免|免于|非(?!本(?:省|市|县|区|地)(?:户籍|户口))', clause):
        return pending()
    if re.search(r'无需|不需|不要求|不得|不|未|没有|无', clause):
        safe = re.sub(r'(?:户籍|户口)不限|不限(?:户籍|户口)|不限制(?:户籍|户口)|(?:户籍|户口)不限制|不低于|不少于|不得少于|不小于|不高于|不超过|不满|无需(?=(?:取得|持有)?(?:法律职业资格|司法考试))|(?:未取得|没有)(?=(?:法律职业资格|司法考试))', '', clause)
        if re.search(r'无需|不需|不要求|不得|不|未|没有|无', safe):
            return pending()
    project_matches = list(PROJECT_PATTERN.finditer(clause))
    if OR.search(clause):
        without_projects = clause
        for match in sorted(project_matches, key=lambda m: m.start(), reverse=True):
            without_projects = without_projects[:match.start()] + without_projects[match.end():]
        residue = BOILERPLATE.sub('', OR.sub('', without_projects))
        if len(project_matches) < 2 or residue:
            return pending()
    checks, spans = [], []

    def consume(match, check):
        checks.append(check)
        spans.append(match.span())

    for match in re.finditer(r'(?:户籍|户口)(?:不限|不限制)|(?:不限|不限制)(?:户籍|户口)', clause):
        consume(match, _check('户籍', 'matched', match[0], '岗位未限制户籍'))
    for match in HOUSE.finditer(clause):
        # Keep unknown text preceding a local scope out of the consumed span.
        local = re.search(r'(?:本省|本市|本县|本区|本地|当地)(?:户籍|户口)$', match[0])
        province = PROVINCE_START.search(match[0])
        start = match.start() + (local.start() if local else (province.start() if province else 0))
        evidence = clause[start:match.end()]
        negative = clause[max(0, start-1):start] == '非' or evidence.startswith('非')
        if negative and not evidence.startswith('非'):
            start -= 1
        checks.append(_household(evidence, profile, job, negative))
        spans.append((start, match.end()))
    grassroots_patterns = [
        rf'(?:基层工作经历|基层工作经验|基层经历|基层工作)(?:满|经验|经历)?(?:不得少于|不低于|不少于|不小于|不超过|不高于|大于|高于|超过|少于|低于|小于|不满|至少)?{NUMBER}(?:至{NUMBER}|到{NUMBER})?年(?:[（(]含[）)])?(?:及以上|以上|以下|以内)?',
        rf'(?:不得少于|不低于|不少于|不小于|不超过|大于|高于|超过|至少)?{NUMBER}(?:至{NUMBER}|到{NUMBER})?年(?:[（(]含[）)])?(?:及以上|以上|以下|以内)?(?:的)?基层工作(?:经历|经验)',
    ]
    for pattern in grassroots_patterns:
        for match in re.finditer(pattern, clause):
            consume(match, evaluate_grassroots_requirement(match[0], profile))
    if project_matches:
        allowed = {PROJECT_ALIASES[match[0]] for match in project_matches}
        supplied = _project_values(profile)
        project_connectors = ''.join(clause[left.end():right.start()] for left, right in zip(project_matches, project_matches[1:]))
        explicit_and = bool(re.search(r'且|并且|同时|和', project_connectors)) and len(allowed) > 1
        if explicit_and:
            status = _combine('matched' if name in supplied else ('unmet' if profile.get('service_projects_reviewed') is True else 'pending') for name in allowed)
        else:
            status = 'matched' if allowed & supplied else ('unmet' if profile.get('service_projects_reviewed') is True else 'pending')
        checks.append(_check('服务基层项目', status, '、'.join(match[0] for match in project_matches), '按明确填报的服务项目名单核验'))
        spans.extend(match.span() for match in project_matches)
        if OR.search(clause):
            spans.extend(match.span() for match in OR.finditer(clause))
    for match in re.finditer(r'服务基层项目(?:人员)?', clause):
        supplied = _project_values(profile) & set(PROJECTS)
        status = 'matched' if supplied else ('unmet' if profile.get('service_projects_reviewed') is True else 'pending')
        if not supplied and '高校毕业生退役士兵' in _project_values(profile):
            status = _soldier_project_status(profile)
        consume(match, _check('服务基层项目', status, match[0], '四类服务项目或服役满5年的高校毕业生退役士兵，分别核对填报证据'))
    for pattern, key, label in [(r'服务期满', 'service_completed', '服务期满'), (r'考核合格', 'service_assessment_passed', '服务考核')]:
        for match in re.finditer(pattern, clause):
            consume(match, _tri_state(label, profile.get(key), match[0], '依据画像明确填写的完成/考核状态'))
    for match in re.finditer(rf'服务(?:满|不少于|不低于|至少)?{NUMBER}年(?:[（(]含[）)])?(?:及以上|以上)?', clause):
        consume(match, _duration(match[0], profile.get('service_years'), '服务年限'))
    for match in re.finditer(r'(?:高校毕业生|大学生|大学毕业生)退役士兵|退役(?:大学生|高校毕业生)士兵', clause):
        status = _combine('matched' if profile.get(key) is True else ('unmet' if profile.get(key) is False else 'pending') for key in ('retired_soldier', 'retired_college_graduate'))
        consume(match, _check('高校毕业生退役士兵', status, match[0], '须同时明确退役士兵和高校毕业生身份'))
    for match in re.finditer(rf'(?:在)?(?:军队)?服役(?:满|不少于|不低于|至少)?{NUMBER}年(?:[（(]含[）)])?(?:及以上|以上)?', clause):
        consume(match, _duration(match[0], profile.get('military_years'), '服役年限'))
    for match in FRESH.finditer(clause):
        consume(match, evaluate_basic_identity(match[0], profile, job))
    for match in LAW_A.finditer(clause):
        value = _law_value(profile)
        if profile.get('law_a_certificate') is False and any(LAW_A.fullmatch(str(cert).strip()) for cert in (profile.get('certificates') or [])):
            consume(match, _check('法律职业资格A类', 'pending', match[0], '画像A类状态与已填写的A类证书冲突，需核对'))
            continue
        prefix = clause[:match.start()]
        no_requirement = re.search(r'无需(?:取得|持有)?$', prefix)
        negative = re.search(r'(?:未取得|没有)$', prefix)
        if no_requirement:
            checks.append(_check('法律职业资格A类', 'matched', clause[no_requirement.start():match.end()], '岗位明确无需该证书'))
            spans.append((no_requirement.start(), match.end()))
        elif value is not None:
            status = 'matched' if value != bool(negative) else 'unmet'
            checks.append(_check('法律职业资格A类', status, match[0], '依据明确的A类状态或已核对的证书名单；B/C类不等同A类'))
            spans.append((negative.start() if negative else match.start(), match.end()))
    if not spans:
        return [], clause
    remaining = list(clause)
    for start, end in spans:
        remaining[start:end] = ' ' * (end-start)
    remaining = ''.join(remaining)
    # Remove wrappers only from gaps of supported predicates. Do not rewrite
    # qualification values still destined for the English/certificate parser.
    gaps = re.split(r' {2,}', remaining)
    remaining = ' '.join(gap.strip(' ，,、。；;') for gap in gaps if gap.strip(' ，,、。；;'))
    remaining = re.sub(r'^(?:(?:具有|具备|仅限|须|需|必须|要求|限|且|并且|并|和|同时|人员|的)\s*)+', '', remaining)
    remaining = re.sub(r'(?:(?:人员|考生|报考|的|且|并且|并|和|同时)\s*)+$', '', remaining)
    if not BOILERPLATE.sub('', remaining):
        remaining = ''
    return checks, remaining.strip()
