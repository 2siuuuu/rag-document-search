from fastapi import APIRouter

from fast_api.endpoints.auth import router as auth_router
from fast_api.endpoints.documents import router as documents_router
from fast_api.endpoints.directories import router as directories_router
from fast_api.endpoints.users import router as users_router
from fast_api.endpoints.operations import router as operations_router

api_router = APIRouter()

# 인증 관련 라우터
api_router.include_router(auth_router, prefix="/auth", tags=["인증"])

# 문서 관련 라우터
api_router.include_router(documents_router, prefix="/documents", tags=["문서"])
api_router.include_router(directories_router, prefix="/directories", tags=["디렉토리 정보 제공"])

# 사용자 관련 라우터
api_router.include_router(users_router, prefix="/users", tags=["사용자"])

# 작업 관련 라우터
api_router.include_router(operations_router, prefix="/operations", tags=["작업"]) 

