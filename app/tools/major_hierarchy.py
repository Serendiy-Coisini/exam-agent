"""Exact undergraduate major hierarchy matching from a private local catalog.

Names never use substring or similarity matching. Unparsed constraints and
unsupported education stages deliberately retain three-valued uncertainty.
"""
import copy
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parents[2] / 'data' / 'major_catalog.json'
_CODE = re.compile(r'\d{6,7}(?:T|K|TK)?')
_UNRESTRICTED = {'不限', '专业不限', '不限专业', '无专业要求', '无要求', '不限制', '无'}
_STAGE_LABEL = r'博士研究生|硕士研究生|大学本科|大学专科|研究生|本科|专科|大专|高职'
_SECTIONS = re.compile(r'(' + _STAGE_LABEL + r')\s*[:：]')


def _text(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip()


def _prepare(catalog):
    """Validate all rows before allowing any row to establish eligibility."""
    if not isinstance(catalog, dict) or catalog.get('status', 'ready') != 'ready':
        return None
    entries = catalog.get('entries')
    if not isinstance(entries, list) or not entries:
        return None
    codes, names, categories, disciplines = {}, {}, {}, {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        code, name = entry.get('code'), entry.get('name')
        dc, dn = entry.get('discipline_code'), entry.get('discipline_name')
        cc, cn = entry.get('category_code'), entry.get('category_name')
        if (not isinstance(code, str) or not _CODE.fullmatch(code)
                or any(not isinstance(x, str) or not x.strip() for x in (name, dc, dn))
                or not isinstance(cc, str) or not isinstance(cn, str)
                or bool(cc) != bool(cn)
                or not re.fullmatch(r'\d{2}', dc)
                or not code.startswith(dc)
                or (cc and (not re.fullmatch(r'\d{4}', cc)
                            or not code.startswith(cc) or not cc.startswith(dc)))
                or type(entry.get('source_page')) is not int or entry['source_page'] < 1
                or code in codes or name in names
                or (cc in categories and categories[cc] != (cn, dc))
                or (dc in disciplines and disciplines[dc] != dn)):
            return None
        codes[code], names[name] = entry, entry
        if cc:
            categories[cc] = (cn, dc)
        disciplines[dc] = dn
    if len({v[0] for v in categories.values()}) != len(categories):
        return None
    if len(set(disciplines.values())) != len(disciplines):
        return None
    terms = {}

    def add(term, value):
        if term in terms and terms[term] != value:
            terms[term] = None
        else:
            terms[term] = value

    for code, entry in codes.items():
        for term in (code, entry['name'], code + entry['name']):
            add(term, ('specialty', code))
    for code, (name, _) in categories.items():
        for term in (code, name, code + name):
            add(term, ('category', code))
    for code, name in disciplines.items():
        for term in (code, name, code + name, name + '门类', name + '学科门类',
                     '学科门类:' + name):
            add(term, ('discipline', code))
    return dict(codes=codes, names=names, terms=terms, categories=categories, disciplines=disciplines)


@lru_cache(maxsize=4)
def _read_catalog(path, modified, size):
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if (not isinstance(payload, dict)
                or not isinstance(payload.get('source_basename'), str)
                or Path(payload['source_basename']).name != payload['source_basename']
                or not re.fullmatch(r'[a-fA-F0-9]{64}', str(payload.get('source_sha256', '')))
                or type(payload.get('year')) is not int):
            raise ValueError('invalid metadata')
        index = _prepare(payload)
        if index is None:
            raise ValueError('invalid hierarchy')
        # An explicit allowlist prevents local paths or implementation metadata
        # from being exposed by an authenticated catalog endpoint.
        safe = {key: payload[key] for key in ('source_basename', 'source_sha256', 'year', 'entries')}
        safe['entries'] = [{key: entry[key] for key in (
            'code', 'name', 'discipline_code', 'discipline_name', 'category_code',
            'category_name', 'source_page', 'notes') if key in entry} for entry in safe['entries']]
        safe['status'] = 'ready'
        safe['source_file'] = safe['source_basename']
        safe['sha256'] = safe['source_sha256']
        safe['counts'] = dict(specialties=len(index['codes']),
                              categories=len({e['category_code'] for e in safe['entries'] if e['category_code']}),
                              disciplines=len({e['discipline_code'] for e in safe['entries']}))
        if payload.get('counts') is not None and payload['counts'] != safe['counts']:
            raise ValueError('incomplete catalog counts')
        pages = payload.get('source_page_count')
        if pages is not None and (type(pages) is not int or pages < 1
                                  or any(entry['source_page'] > pages for entry in safe['entries'])):
            raise ValueError('impossible page provenance')
        return safe, index
    except (OSError, ValueError, TypeError, KeyError):
        return dict(status='invalid', entries=[], reason='本地专业目录无法通过完整性校验'), None


def _catalog_state():
    try:
        stat = CATALOG_PATH.stat()
    except FileNotFoundError:
        return dict(status='missing', entries=[], reason='尚未配置本地专业目录'), None
    except OSError:
        return dict(status='invalid', entries=[], reason='无法读取本地专业目录'), None
    return _read_catalog(str(CATALOG_PATH), stat.st_mtime_ns, stat.st_size)


def load_catalog():
    """Return safe JSON metadata and entries; missing data never raises."""
    return copy.deepcopy(_catalog_state()[0])


def catalog_public():
    """Payload for a caller that enforces authentication at its API boundary."""
    return load_catalog()


def _stage(value):
    value = _text(value)
    if re.fullmatch(r'(?:大学)?本科(?:生)?|学士', value):
        return 'bachelor'
    if re.fullmatch(r'(?:博士|硕士)?研究生|硕士|博士', value):
        return 'graduate'
    if re.fullmatch(r'(?:大学)?专科|大专|高职(?:专科)?', value):
        return 'junior_college'
    return None


def _scoped_requirement(job, stage):
    raw = _text(job.get('major_requirement'))
    specific = _text(job.get('major_requirement_' + stage)) if stage else ''
    if specific:
        if raw:
            original_scope, _ = _scoped_requirement(
                {key: value for key, value in job.items()
                 if key != 'major_requirement_' + stage}, stage)
            if (original_scope and original_scope not in _UNRESTRICTED
                    and re.sub(r'\s+', '', original_scope) != re.sub(r'\s+', '', specific)):
                return '', raw + '；分层专业要求：' + specific
            if not original_scope and not _SECTIONS.search(raw):
                return '', raw + '；分层专业要求：' + specific
        return specific, raw or specific
    sections = list(_SECTIONS.finditer(raw))
    if not sections:
        # A populated separate stage field cannot authorize other stages.
        if not raw and any(_text(job.get('major_requirement_' + key))
                           for key in ('bachelor', 'graduate', 'junior_college')):
            return '', '；'.join(_text(job.get('major_requirement_' + key))
                                for key in ('bachelor', 'graduate', 'junior_college')
                                if _text(job.get('major_requirement_' + key)))
        return raw, raw
    if raw[:sections[0].start()].strip(' ;；,，'):
        return '', raw
    selected = []
    for position, section in enumerate(sections):
        end = sections[position + 1].start() if position + 1 < len(sections) else len(raw)
        if _stage(section[1]) == stage:
            selected.append(raw[section.end():end].strip(' ;；,，'))
    return '、'.join(selected), raw


def _split_terms(value):
    """Split alternatives only outside parentheses, preserving constraints."""
    parts, start, depth, index = [], 0, 0, 0
    while index < len(value):
        char = value[index]
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and (char in '、,;；/\n' or char == '或'):
            parts.append(value[start:index].strip())
            start = index + 1
        index += 1
    if depth:
        return None
    parts.append(value[start:].strip())
    return parts if all(parts) else None


def _match_major_path(level, code, entry, index, trace):
    """Match an applicant's specialty, then its category and discipline."""
    lineage = [('specialty', '具体专业', entry['code'], entry['name']),
               ('category', '专业类', entry['category_code'], entry['category_name']),
               ('discipline', '门类', entry['discipline_code'], entry['discipline_name'])]
    for candidate_level, label, candidate_code, name in lineage:
        if not candidate_code or candidate_level != level:
            continue
        matched = candidate_code == code
        if level == 'specialty':
            wanted_name = index['codes'][code]['name']
        elif level == 'category':
            wanted_name = index['categories'][code][0]
        else:
            wanted_name = index['disciplines'][code]
        trace.append(f"{label}{'匹配' if matched else '不符合'}：岗位要求{wanted_name}（{code}），画像{name}（{candidate_code}）")
        return matched
    return False


def _major_priority(part):
    priority = {'specialty': 0, 'category': 1, 'discipline': 2, 'unrestricted': 3}
    return min(priority[level] for level in part[1])


def _any_major_match(parsed, entry):
    unknown = False
    for part in sorted(parsed, key=_major_priority):
        decision = part[0](entry)
        if decision is True:
            return True
        unknown |= decision is None
    return None if unknown else False


def _intersect_major_match(outer, inner, entry, exclusion=False, trace=None):
    if not exclusion:
        unknown = False
        for part in sorted((outer, inner), key=_major_priority):
            decision = part[0](entry)
            if decision is False:
                return False
            unknown |= decision is None
        return None if unknown else True
    left = outer[0](entry)
    if left is False:
        return False
    right = inner[0](entry)
    if exclusion and right is not None:
        if trace is not None:
            trace.append('命中排除专业范围' if right else '未命中排除专业范围')
        right = not right
    if left is False or right is False:
        return False
    return None if left is None or right is None else True


def _parse(value, index, trace=None):
    """Return a predicate and restriction levels, or fail closed on unknowns."""
    if trace is None:
        trace = []
    value = re.sub(r'\s+', '', value)
    direct = index['terms'].get(value)
    if direct:
        level, code = direct
        return lambda entry: _match_major_path(level, code, entry, index, trace), {level}
    parts = _split_terms(value)
    if parts is None:
        return None
    if len(parts) > 1:
        parsed = [_parse(part, index, trace) for part in parts]
        if any(part is None for part in parsed):
            return None
        return lambda entry: _any_major_match(parsed, entry), set().union(*(part[1] for part in parsed))
    if value in _UNRESTRICTED:
        return lambda entry: True, {'unrestricted'}
    if '(' in value:
        match = re.fullmatch(r'([^()]+)\((.*)\)', value)
        if not match:
            return None
        outer = _parse(match[1], index, trace)
        inner_text = match[2]
        exclusion = re.match(r'(不含|不包括|不包含|除外:?)', inner_text)
        if exclusion:
            inner_text = inner_text[exclusion.end():]
        inner = _parse(inner_text, index, trace)
        if outer is None:
            return None
        if inner is None:
            # A narrower, unrecognized specialization cannot erase a known
            # outer category mismatch. Keep in-category applicants uncertain.
            # Explicit alternatives/broadenings may change the outer scope.
            if (not outer[1] <= {'category', 'discipline'} or not inner_text
                    or re.search(r'或|不限|其他|也可|可报|放宽|但|除外|不要求|不限制|男|女|户籍|户口|英语|四级|六级|CET|证书|应届|毕业|基层|服务|资格|人员', inner_text, re.I)):
                return None
            return lambda entry: False if outer[0](entry) is False else None, outer[1]
        if exclusion:
            return lambda entry: _intersect_major_match(outer, inner, entry, True, trace), outer[1]
        return lambda entry: _intersect_major_match(outer, inner, entry), inner[1]
    return None


def check_major(job, profile, catalog=None):
    """Evaluate a major restriction without borrowing a different stage's map."""
    stage = _stage(profile.get('major_education') or profile.get('education') or profile.get('education_level'))
    requirement, evidence = _scoped_requirement(job, stage)
    original = job.get('major_requirement')
    if original is not None and str(original).strip():
        evidence = str(original) + evidence[len(_text(original)):] if evidence.startswith(_text(original)) else str(original)

    def result(status, reason, level='unknown'):
        return dict(field='专业', status=status, evidence=evidence or '原表未提供明确专业要求',
                    reason=reason, restriction_level=level)

    if requirement in _UNRESTRICTED:
        return result('matched', '适用学历层次专业不限', 'unrestricted')
    if not requirement:
        return result('pending', '没有明确且适用于画像学历层次的专业要求')
    if catalog is None:
        _, index = _catalog_state()
    else:
        index = _prepare(catalog)
    if index is None:
        return result('pending', '本地专业目录缺失或存在歧义，须核对原始目录')
    trace = []
    parsed = _parse(_text(requirement), index, trace)
    if parsed is None:
        return result('pending', '专业要求包含未识别名称、代码或附加限制，须核验原文')
    levels = parsed[1]
    level = next(iter(levels)) if len(levels) == 1 else 'mixed'
    if stage != 'bachelor':
        return result('pending', '当前目录仅适用于本科专业，画像专业学历层次尚不能据此确认', level)
    name, code = _text(profile.get('major')), _text(profile.get('major_code'))
    entry = index['names'].get(name)
    if entry is None or (code and code != entry['code']):
        return result('pending', '画像毕业专业名称或代码不能共同解析为目录中的唯一专业', level)
    for field, key, name_key in [('major_discipline', 'discipline_code', 'discipline_name'),
                                 ('major_category', 'category_code', 'category_name')]:
        actual = re.sub(r'\s+', '', _text(profile.get(field)))
        if actual and actual not in {entry[key], entry[name_key], entry[key] + entry[name_key]}:
            return result('pending', '手填画像专业层级与权威目录记录不一致', level)
    matched = parsed[0](entry)
    path = ' → '.join(filter(None, (f"{entry['name']}（{entry['code']}）", entry['category_name'],
                                    entry['discipline_name'])))
    reason = f"画像专业路径：{path}；逐级核验：" + '；'.join(trace)
    if matched is None:
        return result('pending', reason + '；括号内专业或附加限制仍须核验', level)
    return result('matched' if matched else 'unmet', reason, level)
