"""Conservative three-valued eligibility checks, with original evidence."""
import re
import unicodedata

from app.tools.job_tool import _score_education
from app.tools.major_hierarchy import check_major, MAJOR_EXTRA_QUALIFIER
from app.tools.profile_qualification_rules import consume_qualification_clause, evaluate_basic_identity, evaluate_grassroots_requirement

ALTERNATIVE = re.compile(r'或|任一|其中一|任选|任意一|之一|也可')
ENGLISH = re.compile(r'(?:全国大学英语|大学英语|英语)?[四六46]级|CET\s*-?\s*[46]', re.I)
CERTIFICATES = [
    ('法律职业资格', re.compile(r'(?:法律职业资格|司法考试)(?:证书|证)?\s*(?:\(?([ABC])(?:类|证)?\)?)?',re.I)),
    ('注册会计师', re.compile(r'注册会计师(?:资格证书|资格证|证书|资格)?')),
    ('会计专业技术', re.compile(r'(?:会计专业技术|会计)(?:资格)?(?:\(?((?:初|中|高)级)(?:及以上)?\)?)?(?:资格证书|资格证|职称|资格)')),
    ('软考', re.compile(r'(?:计算机技术与软件专业技术资格(?:\(水平\))?|软考)(?:考试)?(?:\(?((?:初|中|高)级)(?:及以上)?\)?)?(?:资格证书|证书|资格)?')),
    ('教师资格', re.compile(r'教师资格(?:证书|证)?')),
    ('护士执业', re.compile(r'护士执业(?:资格证书|资格证|证书|资格|证)?')),
    ('医师资格', re.compile(r'(?:执业医师|医师资格)(?:资格证书|资格证|证书|证)?')),
    ('普通话', re.compile(r'普通话(?:水平测试)?(?:\(?([一二三]级[甲乙]等)\)?)?(?:等级证书|证书|资格)?')),
    ('驾驶证', re.compile(r'(?:机动车)?驾驶证\s*(?:\(?([ABC][123]?)\)?)?', re.I)),
]


def normalized(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip()


def combine(statuses, either=False):
    statuses=list(statuses)
    if not statuses:
        return 'matched'
    if either:
        return 'matched' if 'matched' in statuses else ('pending' if 'pending' in statuses else 'unmet')
    return 'unmet' if 'unmet' in statuses else ('pending' if 'pending' in statuses else 'matched')


def check(field, status, evidence, reason):
    return dict(field=field,status=status,evidence=evidence,reason=reason)


def _empty(value):
    return value is None or str(value).strip() in {'','不限','未填写','未设置'}


def _unrestricted(value):
    return bool(re.fullmatch(r'(?:不限|不限制|无|无要求|不要求|专业不限|学历不限|身份不限)',value))


def _corresponding_degree(required, profile):
    actual = normalized(profile.get('degree'))
    education = normalized(profile.get('education') or profile.get('education_level'))
    expected = {'本科':'学士', '大学本科':'学士', '普通本科':'学士',
                '硕士':'硕士', '硕士研究生':'硕士', '博士':'博士', '博士研究生':'博士'}.get(education)
    degree = re.fullmatch(r'(?:哲学|经济学|法学|教育学|文学|历史学|理学|工学|农学|医学|管理学|艺术学)?(学士|硕士|博士)(?:学位)?',actual)
    if actual in {'无','没有','无学位','没有学位'}:
        status = 'unmet'
    elif not expected or not degree:
        status = 'pending'
    elif degree[1] == expected:
        status = 'matched'
    elif ['学士','硕士','博士'].index(degree[1]) < ['学士','硕士','博士'].index(expected):
        status = 'unmet'
    else:
        status = 'pending'
    return check('学位',status,required,f'画像学历：{education or "未填写"}；学位：{actual or "未填写"}；对应学位：{expected or "待核验"}')


def _basic(job, profile):
    checks=[]
    for field,user_field,label in [('education_requirement','education','学历'),
                                  ('degree_requirement','degree','学位')]:
        required=normalized(job.get(field))
        actual=normalized(profile.get(user_field) or (profile.get('education_level') if user_field=='education' else ''))
        if _unrestricted(required) or (not required and field=='degree_requirement'):
            continue
        if field=='degree_requirement' and re.fullmatch(r'(?:与)?(?:最高|报考|所报考)学历(?:相)?对应(?:的)?学位', required):
            checks.append(_corresponding_degree(required, profile))
            continue
        if not required or _empty(actual):
            checks.append(check(label,'pending',required or '原表未提供明确要求','画像或岗位要求不完整'))
        else:
            accepted=_score_education(actual.replace('学士','本科'),required.replace('学士','本科'))
            if re.search(r'不含|不接受|不包括|不得|除',required):
                status='pending'
            elif accepted and re.search(r'全日制|普通高校|国民教育|最高|第一|阶段|专业学位|学术|双学位|专升本',required):
                status='pending'
            else:
                status='matched' if accepted else 'unmet'
            checks.append(check(label,status,required,f'画像填写：{actual}；附加学历性质须核验'))
    checks.append(check_major(job, profile))
    required=normalized(job.get('identity_requirement'))
    if required and not _unrestricted(required):
        checks.append(evaluate_basic_identity(required, profile, job))
    required=normalized(job.get('political_requirement'))
    actual=normalized(profile.get('political_status'))
    if required and not _unrestricted(required):
        known=['中共党员','中共预备党员','共青团员','群众','民主党派']
        if _empty(actual):
            status='pending'
        elif re.search(r'非|不|除',required):
            status='pending'
        elif actual=='中共预备党员' and '预备党员' in required:
            status='matched'
        elif actual in known and actual in required:
            status='matched'
        elif actual in known and any(x in required for x in known):
            status='unmet'
        else:
            status='pending'
        checks.append(check('政治面貌',status,required,f'画像填写：{actual or "未填写"}'))
    required=normalized(job.get('grassroots_requirement'))
    if required and not _unrestricted(required):
        checks.append(evaluate_grassroots_requirement(required, profile))
    return checks


def _english(clause, profile, matches):
    results=[]
    numbers=re.findall(r'(?<!\d)(\d{3})(?!\d)',clause)
    for i,match in enumerate(matches):
        level='4' if re.search(r'四|4',match[0]) else '6'
        tail=clause[match.end():matches[i+1].start() if i+1<len(matches) else len(clause)]
        prefix=re.split(r'[或、，,]',clause[:match.start()])[-1]
        proof_context=prefix+tail
        score=re.search(r'(?<!\d)(\d{3})(?!\d)',tail)
        threshold=int(score[1]) if score else (int(numbers[0]) if len(numbers)==1 and len(matches)>1 and ALTERNATIVE.search(clause) and not re.search(r'通过|合格|证书|证明',proof_context) else None)
        actual=profile.get('cet'+level+'_score')
        passed=profile.get('cet'+level+'_passed')
        if re.search(r'低于|以下|不超过|不高于|≤|<',re.sub(r'不低于|不少于|不小于','',tail)):
            status='pending'
        elif threshold is not None:
            if _empty(actual):
                status='matched' if passed is True and re.search(r'(通过|合格).*或',tail) else 'pending'
            else:
                strict=bool(re.search(r'高于|超过|大于|(?<![<≥])>',tail if score else clause))
                status='matched' if (float(actual)>threshold if strict else float(actual)>=threshold) else 'unmet'
        elif re.search(r'通过|合格|证明|证书',proof_context):
            status='pending' if passed is None else ('matched' if passed else 'unmet')
        else:
            status='pending'
        results.append(status)
    return check('英语要求',combine(results,either=bool(ALTERNATIVE.search(clause))),clause,'按四/六级各自成绩或合格证明核验；未填项保留待核验')


def _certificates(clause, profile, found):
    supplied=[normalized(c) for c in (profile.get('certificates') or [])]
    reviewed=profile.get('certificates_reviewed')
    statuses=[]
    for name,pattern,match in found:
        level=match.group(1) if match.lastindex else None
        offered=[]
        for cert in supplied:
            m=pattern.search(cert)
            if m:
                offered.append(m.group(1) if m.lastindex else '')
        if offered:
            if not level or level in offered:
                status='matched'
            elif any(not value for value in offered):
                status='pending'
            else:
                # Grade hierarchies are not automatically equivalent unless explicitly allowed.
                status='pending' if '以上' in clause else 'unmet'
        else:
            status='unmet' if reviewed is True else 'pending'
        statuses.append(status)
    return check('证书要求',combine(statuses,either=bool(ALTERNATIVE.search(clause))),clause,'仅按已填证书名称和明确等级核验；替代关系需人工核验')


def _any_certificate_option(option, profile):
    supplied = [normalized(value) for value in profile.get('certificates') or []]
    # Keep professional names exact: international CPA qualifications are not
    # interchangeable with the explicitly named domestic qualification.
    law_a = re.fullmatch(r'(?:国家统一)?(?:法律职业资格|司法考试)(?:考试)?(?:证书|证)?\(?A(?:类证书|类|证书|证)?\)?', option, re.I)
    if law_a:
        checks, _ = consume_qualification_clause('法律职业资格A类', profile, {})
        return combine(c['status'] for c in checks) if checks else 'pending'
    for name, pattern in CERTIFICATES:
        required = pattern.fullmatch(option)
        if required:
            recognized = [value for value in supplied if pattern.fullmatch(value)]
            return _certificates(option, {**profile, 'certificates': recognized}, [(name, pattern, required)])['status']
    aliases = {option}
    parenthetical = re.findall(r'\(([^()]*)\)', option)
    if parenthetical and all(re.fullmatch(r'[A-Za-z][A-Za-z0-9]{1,12}|[^()]+师', value) for value in parenthetical):
        aliases.add(re.sub(r'\([^()]*\)', '', option))
        aliases.update(parenthetical)
    plain = lambda value: re.sub(r'(?:资格证书|资格证|证书)$', '', re.sub(r'\s+', '', value)).casefold()
    if {plain(value) for value in supplied} & {plain(value) for value in aliases}:
        return 'matched'
    return 'unmet' if profile.get('certificates_reviewed') is True else 'pending'


def _any_certificate_list(clause, profile):
    group = re.fullmatch(r'(?:至少)?(?:须|必须)?(?:具有|具备|持有|取得)(.+)中的(?:任意)?一项(?:资质|资格|证书)(?:\(.*\))?', clause)
    if not group or not any(pattern.search(clause) for _, pattern in CERTIFICATES):
        return None
    options, start, depth = [], 0, 0
    for pos, char in enumerate(group[1]):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif depth == 0 and char in '、,或':
            options.append(group[1][start:pos].strip())
            start = pos + 1
    options.append(group[1][start:].strip())
    if depth or not all(options) or re.search(r'且|同时|除|免|无需|不需|优先|建议|原则上', clause):
        status = 'pending'
    else:
        status = combine((_any_certificate_option(option, profile) for option in options), either=True)
    return check('证书要求', status, clause,
                 '逐项核对列举资质，任一项符合即可；完整证书名单均未命中则不符合，资料或等级不明确保留待核验')


def _comma_clauses(raw):
    """Separate independent comma clauses without splitting conditional OR lists."""
    parts, start, depth = [], 0, 0
    for pos, char in enumerate(raw):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append(raw[start:pos].strip())
            start = pos + 1
    parts.append(raw[start:].strip())
    if len(parts) < 2 or depth or any(not part for part in parts):
        return [raw]
    if re.search(r'也可|亦可|否则|若|如果|对于|本科生|研究生|优先|建议|原则上|除|免|同等|替代|适合|可报考|可报名', raw):
        return [raw]
    for left, right in zip(parts, parts[1:]):
        if (re.search(r'(?:或者|或)$', left) or re.match(r'(?:或者|或)', right)
                or re.match(r'满足|任选|任意|其中|二者|两者', right)):
            return [raw]
    return parts


def _remark_checks(text, profile, job=None):
    checks=[]
    sentences = re.split(r'[。；;\n]+|(?<!\d)\d+[.、](?=[^\d])', normalized(text))
    for raw in (part for sentence in sentences for part in _comma_clauses(sentence)):
        clause=raw.strip(' ，,、.')
        if not clause:
            continue
        if re.fullmatch(r'无|无其他要求|无特殊要求|不限|男女不限|性别不限|不限性别',clause):
            continue
        certificate_list = _any_certificate_list(clause, profile)
        if certificate_list is not None:
            checks.append(certificate_list)
            continue
        additional, remaining_clause = consume_qualification_clause(clause, profile, job or {})
        checks.extend(additional)
        if not remaining_clause:
            continue
        clause = remaining_clause
        first_check=len(checks)
        english=list(ENGLISH.finditer(clause))
        gender=re.search(r'(?:仅限|限|只招|招录|性别[:：]?)(男性|女性|男|女)(?:考生|报考)?|(?:男性|女性)(?:考生|报考)',clause) or re.fullmatch(r'(?:性别[:：]?)?(?:男性|女性|男|女)',clause)
        certs=[(name,pattern,m) for name,pattern in CERTIFICATES for m in pattern.finditer(clause)]
        types=sum(bool(x) for x in (english,gender,certs))
        complex_condition=bool(re.search(r'优先|建议|适合|原则上|除|免|非|不得|不接受|不限制|不限|无需|不需|本科生|研究生|专业英语|英语专业|同等|替代|也可|可报考|可报名',clause))
        if complex_condition or (ALTERNATIVE.search(clause) and types>1):
            checks.append(check('备注','pending',clause,'存在建议、否定、条件或跨条件替代关系，需人工核验'))
            continue
        consumed=[]
        if gender:
            target='女' if '女' in gender[0] else '男'
            both=re.search(r'男性或女性|女性或男性',clause)
            actual=profile.get('gender')
            status='pending' if _empty(actual) else ('matched' if actual==target or both else 'unmet')
            checks.append(check('性别',status,clause,f'画像填写：{actual or "未填写"}'))
            consumed.append(gender.span())
            if both:
                consumed.append(both.span())
        if english:
            checks.append(_english(clause,profile,english))
            consumed.extend(m.span() for m in english)
        if certs:
            checks.append(_certificates(clause,profile,certs))
            consumed.extend(m.span() for _,_,m in certs)
        remaining=list(clause)
        for start,end in consumed:
            remaining[start:end]=' '*(end-start)
        remaining=''.join(remaining)
        if english:
            remaining=re.sub(r'(?<!\d)\d{3}(?!\d)(?:分)?','',remaining)
        remaining=re.sub(r'满足其中一项即可|满足任一项即可|任选其一|证明|仅限|限|须|必须|需|应|持有|取得|获得|具备|具有|要求|报考|考生|资格证书|资格|证书|考试|成绩|分数|合格|通过|达到|不低于|高于|大于|超过|至少|及以上|以上|含|及|且|或者|或|同时|并且|并|和|可报|的','',remaining)
        remaining=re.sub(r'[\s,，、:：()（）.\-≥>]+','',remaining)
        if remaining and ALTERNATIVE.search(clause):
            checks[first_check:]=[check('备注','pending',clause,'存在未识别的替代条件，不能直接排除或判为匹配')]
        elif remaining or not consumed:
            checks.append(check('备注','pending',clause,'该备注还有未自动核验的要求，请查看原文'))
    return checks


def evaluate_job(job, profile, check_remarks=True):
    checks=_basic(job,profile)
    # Non-major parenthetical qualifications are checked separately.
    major_check = next((item for item in checks if item['field'] == '专业'), {})
    major_extras = re.findall(r'\(([^)]*)\)',normalized(job.get('major_requirement'))) if major_check.get('status') != 'unmet' else []
    for extra in major_extras:
        if MAJOR_EXTRA_QUALIFIER.search(extra):
            if re.search(r'[、,，;；/]|或',normalized(job.get('major_requirement'))):
                checks.append(check('专业附加要求','pending',normalized(job.get('major_requirement')),'专业列表中的附加条件作用范围不明确，需要核验'))
            else:
                checks.extend(_remark_checks(extra,profile,job) if check_remarks else
                              [check('专业附加要求','pending',extra,'尚未核验专业括号中的附加条件')])
    basic_status=combine(c['status'] for c in checks)
    for field in ('qualification_requirement','remark','eligibility_extra_requirements'):
        text=normalized(job.get(field))
        if not text:
            continue
        if check_remarks:
            checks.extend(_remark_checks(text,profile,job))
        else:
            checks.append(check('备注','pending',text,'尚未启用备注核验'))
    return dict(status=combine(c['status'] for c in checks),basic_status=basic_status,checks=checks,
                major_restriction_level=major_check.get('restriction_level','unknown'))
