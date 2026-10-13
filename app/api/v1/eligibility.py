from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user
from app.db import repositories
from app.schemas.eligibility_schema import EligibilityRequest
from app.services.eligibility_service import query_eligible_jobs
from app.tools.major_hierarchy import catalog_public

router=APIRouter(prefix='/jobs',tags=['profile eligibility'])


@router.post('/eligibility')
async def eligibility(payload: EligibilityRequest, user: dict=Depends(get_current_user)):
    profile=await run_in_threadpool(repositories.get_user_profile,user['id'])
    return await run_in_threadpool(query_eligible_jobs,payload,profile)


@router.get('/major-catalog')
async def major_catalog(user: dict=Depends(get_current_user)):
    del user
    return await run_in_threadpool(catalog_public)
