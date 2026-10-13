from typing import Annotated, Any, Optional, Literal

from pydantic import BaseModel, Field, model_validator


class DevLoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    nickname: str = Field("", max_length=80)


class AuthRegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: str = Field("", max_length=200)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=80)


class AuthLoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class UserProfilePayload(BaseModel):
    education_level: Optional[str] = Field(None, max_length=80)
    degree: Optional[str] = Field(None, max_length=80)
    major: Optional[str] = Field(None, max_length=200)
    hukou_province: Optional[str] = Field(None, max_length=80)
    hukou_city: Optional[str] = Field(None, max_length=80)
    hukou_county: Optional[str] = Field(None, max_length=80)
    fresh_graduate_scope: Optional[Literal['', 'current_year', 'retained', 'not_fresh']] = None
    service_projects: list[Literal['大学生村官', '特岗教师', '三支一扶', '西部计划', '高校毕业生退役士兵']] = Field(default_factory=list, max_length=5)
    service_projects_reviewed: Optional[bool] = None
    service_completed: Optional[bool] = None
    service_assessment_passed: Optional[bool] = None
    service_years: Optional[float] = Field(None, ge=0, le=60)
    retired_soldier: Optional[bool] = None
    retired_college_graduate: Optional[bool] = None
    military_years: Optional[float] = Field(None, ge=0, le=60)
    law_a_certificate: Optional[bool] = None
    major_code: Optional[str] = Field(None, max_length=40)
    major_discipline: Optional[str] = Field(None, max_length=80)
    major_category: Optional[str] = Field(None, max_length=100)
    major_education: Optional[str] = Field(None, max_length=80)
    gender: Optional[str] = Field(None, pattern="^(男|女)?$")
    cet4_score: Optional[int] = Field(None, ge=0, le=710)
    cet6_score: Optional[int] = Field(None, ge=0, le=710)
    cet4_passed: Optional[bool] = None
    cet6_passed: Optional[bool] = None
    certificates: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=100)
    certificates_reviewed: Optional[bool] = None
    graduation_year: Optional[int] = Field(None, ge=1950, le=2100)
    is_fresh_graduate: Optional[bool] = None
    political_status: Optional[str] = Field(None, max_length=80)
    grassroots_years: Optional[float] = Field(None, ge=0, le=60)
    target_region: Optional[str] = Field(None, max_length=160)
    target_exam_type: Optional[str] = Field(None, max_length=80)
    target_job_type: Optional[str] = Field(None, max_length=120)
    score_estimate: Optional[float] = Field(None, ge=0, le=1000)
    work_preference: Optional[str] = Field(None, max_length=500)
    risk_preference: Optional[str] = Field(None, max_length=80)
    raw_profile_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def validate_raw_aliases(cls, value):
        if isinstance(value, dict) and isinstance(value.get('raw_profile_json'), dict):
            raw = value['raw_profile_json']
            return {**{key: item for key, item in raw.items() if key in cls.model_fields and key != 'raw_profile_json'}, **value}
        return value

    # Web 工作台兼容字段，统一保存到 raw_profile_json。
    province: Optional[str] = Field(None, max_length=80)
    city: Optional[str] = Field(None, max_length=80)
    exam_type: Optional[str] = Field(None, max_length=80)
    education: Optional[str] = Field(None, max_length=80)
    identity: Optional[str] = Field(None, max_length=120)
    accept_relocation: Optional[str] = Field(None, max_length=80)
    preferences: list[str] = Field(default_factory=list)
    filled_fields: list[str] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    title: str = Field("新会话", max_length=120)
    scene: str = Field("general_chat", max_length=40)


class SessionMessageCreateRequest(BaseModel):
    role: str = Field("user", max_length=20)
    content: str = Field(..., min_length=1, max_length=100000)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class FavoriteJobRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=160)
    job_name: str = Field("", max_length=300)
    department_name: str = Field("", max_length=300)
    region: str = Field("", max_length=160)
    exam_type: str = Field("", max_length=80)
    raw_job_json: dict[str, Any] = Field(default_factory=dict)
    note: str = Field("", max_length=1000)


class ReportCreateRequest(BaseModel):
    agent_run_id: Optional[int] = Field(None, ge=1)
    session_id: Optional[int] = Field(None, ge=1)
    title: str = Field("", max_length=200)
    report_type: str = Field("job_analysis", max_length=80)
    content: str = Field("", max_length=100000)
    summary: str = Field("", max_length=5000)
    related_jobs_json: list[dict[str, Any]] = Field(default_factory=list)
    citations_json: list[dict[str, Any]] = Field(default_factory=list)
