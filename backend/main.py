from contextlib import asynccontextmanager
from fastapi import FastAPI
import os
from datetime import datetime

# 추적을 위한 .env 설정 불러오기
from dotenv import load_dotenv
load_dotenv()

# LangSmith 추적 활성화
from langchain_teddynote import logging

# 프로젝트 이름을 입력합니다.
logging.langsmith("25-1_RAG_Project")

from db.database import init_db, get_db
from fast_api.router import api_router
from fast_api.middlewares import setup_middlewares
from fast_api.security import get_current_user
from config.settings import UPLOAD_DIR
from rag.vectorstore import manually_create_vector_extension
from fastapi import Depends, Request, HTTPException, status
from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from config.settings import SECRET_KEY, ALGORITHM
from sqlalchemy.orm import Session


# 애플리케이션 시작 시 DB 초기화
@asynccontextmanager
async def lifespan(app: FastAPI):
    from db.database import engine  # 기존 엔진을 임포트
    init_db()
    
    # pgvector 익스텐션 생성
    manually_create_vector_extension(engine)
    yield


# FastAPI 앱 생성
app = FastAPI(title="RAG Document Search API", lifespan=lifespan)



# 미들웨어 설정
setup_middlewares(app)

# 업로드 디렉토리 확인
os.makedirs(UPLOAD_DIR, exist_ok=True)

# API 라우터 포함
app.include_router(api_router, prefix="/fast_api")



# 기본 경로
@app.get("/")
def read_root():
    """Welcome message"""
    return {"message": "Welcome to RAG Document Search API"}

# Bearer 토큰 스킴 설정
security = HTTPBearer(auto_error=False)

def get_token_from_header(request: Request, token: str = Depends(security)):
    """헤더에서 토큰을 추출하는 함수"""
    if token:
        return token.credentials
    
    # Manual token extraction from Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    
    return None

def verify_token_optional(token: str = None):
    """토큰을 선택적으로 검증하는 함수"""
    if not token:
        return None
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        return {"username": username, "valid": True}
    except JWTError:
        return {"valid": False, "error": "Invalid token"}

# 헬스체크 엔드포인트 (토큰 검증 포함)
@app.get("/health")
async def health_check(
    request: Request,
    token: str = Depends(get_token_from_header),
    db: Session = Depends(get_db)
):
    """
    서버 헬스체크 및 토큰 검증
    - 토큰이 없어도 기본 헬스체크 수행
    - 토큰이 있으면 토큰 유효성도 함께 검증
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "RAG Document Search API"
    }
    
    # 토큰이 제공된 경우 검증
    if token:
        token_info = verify_token_optional(token)
        if token_info:
            if token_info.get("valid"):
                # 토큰이 유효한 경우 사용자 정보 추가
                try:
                    from fast_api.security import get_user_by_username
                    user = get_user_by_username(db, token_info["username"])
                    if user:
                        health_status.update({
                            "authenticated": True,
                            "user": {
                                "username": user.username,
                                "email": user.email,
                                "user_id": user.id
                            }
                        })
                    else:
                        health_status.update({
                            "authenticated": False,
                            "token_status": "valid_but_user_not_found"
                        })
                except Exception as e:
                    health_status.update({
                        "authenticated": False,
                        "token_status": "database_error",
                        "error": str(e)
                    })
            else:
                # 토큰이 유효하지 않은 경우
                health_status.update({
                    "authenticated": False,
                    "token_status": "invalid",
                    "error": token_info.get("error", "Token validation failed")
                })
        else:
            health_status.update({
                "authenticated": False,
                "token_status": "invalid"
            })
    else:
        # 토큰이 없는 경우
        health_status.update({
            "authenticated": False,
            "token_status": "not_provided"
        })
    
    return health_status
