"""Shared qualification evidence and visible output provenance for every analyst path."""
from copy import deepcopy

from app.agents.prompt_builder import use_public_method
from app.services.analysis_skill_service import get_analysis_skill_status
from app.tools.job_tool import _evaluate_recommendation_eligibility, _matches_recommendation_city, extract_location_preference
from app.tools.profile_context import current_qualification_profile


def qualify_jobs(payload, jobs):
    profile=current_qualification_profile(payload.profile,payload.education,payload.major,payload.identity)
    output=[]
    for original in jobs:
        job=deepcopy(original)
        if job.get('data_error'):
            output.append(job)
            continue
        if '待核对职位表' in str(job.get('education_required') or ''):
            result=dict(status='pending',major_restriction_level='unknown',checks=[dict(
                field='职位资格',status='pending',evidence='仅有历史分数记录',reason='需要对应年度职位表核验报名资格')])
        else:
            result=_evaluate_recommendation_eligibility(job,profile)
        if job.get('qualification_evidence_incomplete'):
            result['checks'].append(dict(field='职位资格证据', status='pending',
                evidence='未能从服务端原始职位表确认当前卡片',
                reason='附加资格可能不完整，需核对对应年度的原始职位表'))
            if result['status'] != 'unmet':
                result['status'] = 'pending'
        job['eligibility_status']=result['status']
        job['eligibility_checks']=result['checks']
        job['major_restriction_level']=result['major_restriction_level']
        checks=result['checks']
        major=next((item for item in checks if item['field']=='专业'),{})
        analysis=dict(job.get('ai_analysis') or {})
        analysis['qualification_match']=qualification_summary(job)
        analysis['major_match']=major.get('reason') or '专业条件待核验'
        analysis['recruit_risk']=f"职位表招录 {job.get('recruit_count') or job.get('headcount') or '未知'} 人；人数不代表竞争强弱。"
        if not job.get('applicants_count') and not job.get('applicant_count'):
            analysis['competition_data']='暂无可核验的报名人数；竞争比和分数参考以已导入的同岗位数据为准，不能据此承诺录取。'
        if result['status']=='unmet':
            job['match_level']='明确不符合（查询结果）'
            analysis['why_recommended']='这是一条指定岗位查询结果；当前画像存在明确不符合项，不作为可报推荐。'
        elif result['status']=='pending':
            analysis['why_recommended']='仅作为待核验候选，不能确认整体可报。'+str(analysis.get('why_recommended') or '')
        job['ai_analysis']=analysis
        preference=extract_location_preference('',city=payload.city,region=payload.region)
        if not _matches_recommendation_city(job,preference):
            job['query_scope_warning']='此指定岗位的工作地点超出本次目标地区，仅供查询，未作为地区匹配推荐。'
        output.append(job)
    return output


def qualification_summary(job):
    labels={'matched':'条件匹配','pending':'待核验','unmet':'明确不符合'}
    status=job.get('eligibility_status') or 'pending'
    checks=job.get('eligibility_checks') or []
    selected=[item for item in checks if item.get('status')=='unmet']
    if not selected:selected=[item for item in checks if item.get('status')=='pending'][:4]
    details='；'.join(f"{item.get('field')}：{labels.get(item.get('status'),'待核验')}，{item.get('reason') or item.get('evidence') or ''}" for item in selected)
    return f"{labels[status]}。"+details


def analysis_metadata(payload, report_result=None, grounding=None, selection=None):
    report_result=report_result or {}
    grounding=dict(grounding or {'source':'rules','accepted':False,'reasons':['未调用模型生成正文']})
    selection=dict(selection or {})
    visible_report=bool(report_result.get('llm_used') and grounding.get('accepted'))
    selection_used=bool(selection.get('model_used'))
    source=grounding.get('source','rules') if visible_report else ('mixed' if selection_used else 'rules')
    skill=get_analysis_skill_status()
    return dict(output_source=source,report={**grounding,'call_succeeded':bool(report_result.get('llm_used')),
        'model':report_result.get('model',''),'call':report_result.get('model_metadata') or {}},
        selection=selection,skill=dict(ready=skill['ready'],revision=skill['revision'],
            applied=bool(skill['ready'] and use_public_method(payload) and (visible_report or selection_used)),
            applied_to_selection=bool(selection.get('skill_applied'))),
        effective_conditions=dict(province=payload.region,city=payload.city,exam_type=payload.exam_type,
            year=payload.year,education=payload.education,major=payload.major,identity=payload.identity,
            constraints=payload.selection_constraints),
        selection_plan=payload.selection_plan)
