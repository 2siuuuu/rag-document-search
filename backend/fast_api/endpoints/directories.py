from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime


from fast_api.security import get_current_user
from db.database import get_db
from db.models import User

router = APIRouter()
'''
directories 엔드포인트 설명:

'''

@router.get('/')
async def get_filesystem_structure(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    전체 파일 시스템 구조 반환
    """
    # get_filesystem_structure 엔드포인트와 "/"엔드포인트가 프론트엔드 입장에서 어떤 차이를 가지는지 알아보기.
    from db import crud
    try:
        user_id = current_user.id

        # 루트 디렉토리 존재여부 확인 및 생성
        # 루트 디렉토리가  
        # 존재하면 아무 작업도 하지 않는다.
        if not crud.get_directory_by_id(db, "root", user_id):
            # 존재하지 않으면 루트 디렉토리 생성
            crud.create_directory(db, "root", "Home", "/", True, None, datetime.now(), None)

        # 디렉토리만 필터링. 디렉토리 구조만 보내면 됨.
        directories = crud.get_only_directory(db, user_id)

        # <로직>
       

        # 2) 새 리스트에 수정된 객체 생성
        your_result = []
        for d in directories:
            # 루트 디렉토리는 제외.( 루트 디렉토리는 프론트엔드에서 처리해준다.)
            # db입장에서 필요한 데이터이지만 프론트 엔드로 해당 정보를 보낼 시 이미 프론트엔드에서 처리하기 때문에 충돌이 발생하여 버그가 발생함.
            if d['id'] == "root":
                continue
            your_result.append({
                'id':   d['id'],
                'name': d['name'],
                'path': d['path']
            })
        # </로직>
        directories = your_result

        return {
            "directories": directories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching filesystem structure: {str(e)}")
