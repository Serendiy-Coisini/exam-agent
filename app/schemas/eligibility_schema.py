from typing import Literal
from pydantic import BaseModel, Field
from app.schemas.product_schema import UserProfilePayload


class EligibilityRequest(BaseModel):
    profile: UserProfilePayload | None = None
    province: str | None = Field(None, max_length=80)
    city: str | None = Field(None, max_length=80)
    exam_type: str | None = Field(None, max_length=80)
    year: int | None = Field(None, ge=1900, le=2100)
    check_remarks: bool = True
    status: Literal['candidate', 'all', 'matched', 'pending', 'unmet'] = 'candidate'
    keyword: str = Field('', max_length=200)
    remark_include: str = Field('', max_length=200)
    remark_exclude: str = Field('', max_length=200)
    page: int = Field(1, ge=1, le=1000000)
    page_size: int = Field(20, ge=1, le=100)
