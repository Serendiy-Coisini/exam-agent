"""Only public qualification columns may enter screening and analyst context."""
import json

PUBLIC_REQUIREMENT_KEYS = {
    '性别','性别要求','英语','英语水平','英语要求','外语要求','证书要求','资格证书','专业技能证书',
    '年龄','年龄要求','户籍','户籍要求','户口','户口要求','工作经历','工作经历要求','最低服务年限','服务年限',
    '服务基层项目工作经历','服务基层项目','基层工作最低年限','基层工作年限','应届要求','法律职业资格',
}


def public_extra_requirements(job: dict) -> str:
    if isinstance(job.get("eligibility_extra_requirements"), str):
        return job["eligibility_extra_requirements"]
    try:
        extras=json.loads(job.get('extra_fields_json') or '{}')
    except (ValueError,TypeError):
        extras={}
    if not isinstance(extras,dict):
        return ''
    return '；'.join(f'{key}：{value}' for key,value in extras.items()
                    if str(key).strip() in PUBLIC_REQUIREMENT_KEYS and isinstance(value,(str,int,float)))
