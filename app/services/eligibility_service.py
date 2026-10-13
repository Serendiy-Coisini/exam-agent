"""Scan all imported jobs; return only a bounded public result page."""
import json
import math
from functools import lru_cache
import re

from app.db.database import connection
from app.schemas.eligibility_schema import EligibilityRequest
from app.services.product_service import model_dump, serialize_profile
from app.tools.eligibility_rules import evaluate_job, check, combine, normalized
from app.tools.province_registry import get_province_name, get_province_slug
from app.tools.region_resolver import canonical_province
from app.db.import_repository import _merge_public_job_extra_fields

PUBLIC_REQUIREMENT_KEYS = {
    '性别','性别要求','英语','英语水平','英语要求','外语要求','证书要求','资格证书','专业技能证书',
    '年龄','年龄要求','户籍','户籍要求','工作经历','工作经历要求','最低服务年限','服务年限',
}

PUBLIC_FIELDS = ('id','department_code','job_code','job_name','unit_name','department_name',
                 'year','province','region','exam_type','recruit_count','education_requirement',
                 'degree_requirement','major_requirement','identity_requirement','political_requirement',
                 'grassroots_requirement','qualification_requirement','job_description','remark')


def _scope(value):
    return '' if value in {None,'','不限','未填写','全部','全国','全省'} else normalized(value)


def _jobs(year):
    with connection() as db:
        if year is None:
            # Group years once, then stream every row. No Top N or 100,000-row cap.
            cursor=db.execute('''SELECT j.* FROM exam_jobs j JOIN
                (SELECT province,exam_type,MAX(year) AS year FROM exam_jobs GROUP BY province,exam_type) latest
                ON j.province=latest.province AND j.exam_type=latest.exam_type AND j.year=latest.year
                ORDER BY j.province,j.department_code,j.job_code,j.id''')
        else:
            cursor=db.execute('SELECT * FROM exam_jobs WHERE year=? ORDER BY province,department_code,job_code,id',(year,))
        while rows:=cursor.fetchmany(500):
            for row in rows:
                item=dict(row)
                _merge_public_job_extra_fields(item)
                try:
                    extras=json.loads(item.get('extra_fields_json') or '{}')
                except (ValueError,TypeError):
                    extras={}
                requirements=[]
                if isinstance(extras,dict):
                    for key,value in extras.items():
                        if str(key).strip() in PUBLIC_REQUIREMENT_KEYS and isinstance(value,(str,int,float)):
                            requirements.append(f'{key}：{value}')
                item['eligibility_extra_requirements']='；'.join(requirements)
                yield item


@lru_cache(maxsize=512)
def _province_name(value):
    return get_province_name(get_province_slug(value)) or ''


def _location_part(value):
    province=_province_name(canonical_province(value))
    place=re.sub(r'^.*?(?:省|自治区|特别行政区)','',value)
    match=re.match(r'(.+?市)',place)
    return {'province':province,'city':match[1] if match else ''}


def _location(job, province, city):
    job_province=get_province_name(job.get('province')) or normalized(job.get('province'))
    expected=_province_name(province) or province
    national=job_province in {'全国','不限',''}
    if province and job_province!=expected and not national:
        return False,[]
    checks=[]
    region=normalized(job.get('region'))
    if national and region and region not in {'全国','不限'}:
        parts=[_location_part(part) for part in re.split(r'[、,，;；/]|或|以及|及',region) if part.strip()]
        if province and parts and all(p.get('province') for p in parts) and all(p.get('province')!=expected for p in parts):
            return False,[]
        if city and parts and all(p.get('city') for p in parts) and all(p.get('city').removesuffix('市')!=city.removesuffix('市') for p in parts):
            return False,[]
    if city:
        target=city.removesuffix('市')
        # Match an administrative name component, not a substring such as 海南 in 海南州.
        located=bool(re.search(r'(?:^|省|自治区|特别行政区|、|,|，|;|；|\s)'+re.escape(target)+r'(?:市|自治州|地区|盟|$)',region))
        if not located:
            if not region or region in {job_province,'全国','不限'} or national:
                checks.append(check('工作地点','pending',region or job_province,'无法确定是否属于目标城市'))
            else:
                return False,[]
    if province and national:
        checks.append(check('工作地点','pending',region or job_province,'岗位为全国或多省范围，请核对是否可选择目标省份'))
    return True,checks


def query_eligible_jobs(payload: EligibilityRequest, stored_profile=None):
    profile=serialize_profile(stored_profile)
    if payload.profile is not None:
        profile.update(model_dump(payload.profile,exclude_unset=True))
    province=_scope(payload.province if payload.province is not None else profile.get('province'))
    city=_scope(payload.city if payload.city is not None else profile.get('city'))
    exam=_scope(payload.exam_type if payload.exam_type is not None else profile.get('exam_type'))
    counts=dict(matched=0,pending=0,unmet=0)
    total=0
    scope_total=0
    basic_candidates=0
    items=[]
    include=[s.strip() for s in re.split('[,，;；]',payload.remark_include) if s.strip()]
    exclude=[s.strip() for s in re.split('[,，;；]',payload.remark_exclude) if s.strip()]
    offset=(payload.page-1)*payload.page_size
    for job in _jobs(payload.year):
        if exam and normalized(job.get('exam_type'))!=exam:
            continue
        in_scope,location_checks=_location(job,province,city)
        if not in_scope:
            continue
        scope_total+=1
        text=' '.join(str(job.get(k) or '') for k in ('job_code','department_code','job_name','unit_name','department_name'))
        if payload.keyword and payload.keyword.casefold() not in text.casefold():
            continue
        remark='\n'.join(str(job.get(k) or '') for k in ('remark','qualification_requirement','eligibility_extra_requirements'))
        if any(s.casefold() not in remark.casefold() for s in include) or any(s.casefold() in remark.casefold() for s in exclude):
            continue
        result=evaluate_job(job,profile,payload.check_remarks)
        if result['basic_status']!='unmet':
            basic_candidates+=1
        result['checks'].extend(location_checks)
        result['status']=combine(c['status'] for c in result['checks'])
        status=result['status']
        counts[status]+=1
        selected=(payload.status=='all' or payload.status==status or
                  (payload.status=='candidate' and status!='unmet'))
        if not selected:
            continue
        if offset<=total<offset+payload.page_size:
            public={k:job.get(k) for k in PUBLIC_FIELDS}
            public['province']=get_province_name(public.get('province')) or public.get('province')
            public['additional_requirements']=job.get('eligibility_extra_requirements') or ''
            items.append({**public,**result})
        total+=1
    return dict(items=items,total=total,page=payload.page,page_size=payload.page_size,
                total_pages=math.ceil(total/payload.page_size),counts=counts,
                scope_total=scope_total,basic_candidate_count=basic_candidates,
                check_remarks=payload.check_remarks,
                note='条件匹配仅代表已实现规则未发现冲突；待核验项和最终报名资格须核对原始公告。')
