from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import User
from db.user_service import get_users
from fast_api.security import get_current_user

router = APIRouter()

@router.get("/")
def read_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """사용자 목록 조회 엔드포인트"""
    users = get_users(db, skip=skip, limit=limit)
    return {"users": users} 


@router.get("/profile")
def read_users(
    current_user: User = Depends(get_current_user)
):
    """현재 사용자 정보 조회 엔드포인트"""
    return current_user     