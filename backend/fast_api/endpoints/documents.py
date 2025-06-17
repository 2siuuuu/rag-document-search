from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from datetime import datetime
import uuid
import json
import boto3
 
# 메서드 import
from debug import debugging
from db.database import get_db, engine
from db.models import User
from fast_api.security import get_current_user
from rag.document_service import process_document
from config.settings import AWS_SECRET_ACCESS_KEY,S3_BUCKET_NAME,AWS_ACCESS_KEY_ID,AWS_DEFAULT_REGION  # 설정 임포트
import os
import logging
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()




# 로거 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)

# S3 클라이언트 초기화 (설정 사용)
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_DEFAULT_REGION
)
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise ValueError("S3_BUCKET_NAME 환경 변수가 설정되지 않았습니다.")

router = APIRouter()

''' 엔드포인트 설명:

'''


@router.get("/")
async def list_items(
    path: str = Query("/", description="현재 경로"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
    ):
    """지정된 경로의 파일 및 폴더 목록을 반환"""

    from sqlalchemy import text
    from db import crud
    try:
        
        filtered_items = []

        user_id = current_user.id

        # 루트 디렉토리가 존재하지 않으면 생성하고, 존재하면 아무 작업도 하지 않는다.
        if not crud.get_directory_by_id(db, "root", user_id):
            # 루트 디렉토리 생성
            crud.create_directory(db, "root", "Home", "/", True, None, datetime.now(), None)

        # 프론트 엔드에서 선택한 경로
            # 만약 최초 로그인 시 프론트 엔드에서 잘못된 경로('')를 전송한다면
        if path == '':
            # selected_path를 '/'로 설정.
            selected_path = '/'
        else:
            # 그렇지 않다면 프론트 엔드에서 전송한 경로를 사용.
            selected_path = path

        # <db에서 디렉토리 정보와 파일 정보 가져오기>

        # 커넥션 풀에서 직접 연결 가져오기
        with db.connection() as connection:
            # 파일 목록 쿼리
            if selected_path == '/':
                # selected_path 가 '/'일 경우 쿼리문은 달라야 한다. issue # 77 참고.
                file_query = text("""
                    SELECT id, name, 'file' as type, path
                    FROM directories
                    WHERE path LIKE '/%' 
                    AND path NOT LIKE '/%/%'
                    AND is_directory = FALSE
                    AND owner_id = :user_id
                """)
            else:
                # selected_path가 '/'가 아니면 일반적인 경로 처리.
                file_query = text("""
                    SELECT id, name, 'file' as type, path 
                    FROM directories 
                    WHERE path LIKE :selected_path || '/%' 
                    AND path NOT LIKE :selected_path || '/%/%' 
                    AND is_directory = FALSE
                    AND owner_id = :user_id
                """)
            file_result = connection.execute(file_query, {"selected_path": selected_path, "user_id": user_id}).mappings().fetchall()
            
            # 디렉토리 목록 쿼리
            if selected_path == '/':
                # selected_path 가 '/'일 경우 쿼리문은 달라야 한다. issue # 77 참고.
                dir_query = text("""
                    SELECT id, name, 'folder' as type, path 
                    FROM directories 
                    WHERE path LIKE '/%' 
                    AND path NOT LIKE '/%/%' 
                    AND is_directory = TRUE
                    AND id <> 'root'
                    AND owner_id = :user_id
                """)
            else:
                # selected_path가 '/'가 아니면 일반적인 경로 처리.
                dir_query = text("""
                    SELECT id, name, 'folder' as type, path 
                    FROM directories 
                    WHERE path LIKE :selected_path || '/%' 
                    AND path NOT LIKE :selected_path || '/%/%' 
                    AND is_directory = TRUE
                    AND id <> 'root'
                    AND owner_id = :user_id
                """)
            dir_result = connection.execute(dir_query, {"selected_path": selected_path, "user_id": user_id}).mappings().fetchall()
        # </db에서 디렉토리 정보와 파일 정보 가져오기>

        # <가져온 정보를 filtered_items에 추가>
        filtered_items.extend([dict(item) for item in dir_result])
        filtered_items.extend([dict(item) for item in file_result])
        # </가져온 정보를 filtered_items에 추가>
        
        return {"items": filtered_items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing items: {str(e)}")

@router.post("/manage")
async def upload_document(
    files: List[UploadFile] = File(None), # None을 ... 으로 변경
    path: str = Form('/'),
    directory_structure: str = Form(None),
    operations: str = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """통합 문서 / 디렉토리 관리"""
    from typing import Dict, Any



    # API 테스트 용 코드.
    # if current_upload_path == '':
    #     current_upload_path = None
    # if operations == '':
    #     operations = None
    
    # path는 사용자가 유저 인터페이스 창에서 선택한 경로이다.
    current_upload_path = path
    # debugging.stop_debugger()
    try:
        results = {
            "success": True,
            "message": "작업이 완료되었습니다.",
            "items": []
        }
        user_id = current_user.id

        # 1 & 2. 파일 업로드 처리 (디렉토리 구조 포함 또는 단일 파일)
        # 파일이 존재하는 경우에 아래 코드 실행. (operations작업과 구분.)
        if files:
            # 단일 파일인 경우
            if os.path.dirname(files[0].filename) == "":
                # debugging.stop_debugger()
                # 파일 업로드 처리
                file_results = await process_file_uploads(files, current_upload_path, current_user, db)
                # debugging.stop_debugger()
                results["items"].extend(file_results)
            else:
                # 디렉토리 업로드인 경우
                # 디렉토리 업로드 처리
                directory_results = await process_directory_uploads(current_upload_path, directory_structure, user_id, db)
                # debugging.stop_debugger()
                results["items"].extend(directory_results)

                # 파일 업로드 처리
                file_results = await process_file_uploads(files, current_upload_path, current_user, db)
                # debugging.stop_debugger()
                results["items"].extend(file_results)
        
        # 3 & 4. 디렉토리 작업 처리 (생성, 이동, 삭제 등)
        if operations:
            try:
                # 
                ops_data: Dict[str, Any] = json.loads(operations)
                op_results = await process_directory_operations(ops_data, user_id, db)
                results["items"].extend(op_results)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="Invalid operations format")
        
        return results
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error managing documents: {str(e)}")

# @router.get("/structure")
# async def get_filesystem_structure(
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db)
# ):
#     """
#     전체 파일 시스템 구조 반환
#     """
#     # get_filesystem_structure 엔드포인트와 "/"엔드포인트가 프론트엔드 입장에서 어떤 차이를 가지는지 알아보기.
#     from db import crud
#     try:
#         user_id = current_user.id

#         # 루트 디렉토리 존재여부 확인 및 생성
#         # 루트 디렉토리가  
#         # 존재하면 아무 작업도 하지 않는다.
#         if not crud.get_directory_by_id(db, "root", user_id):
#             # 존재하지 않으면 루트 디렉토리 생성
#             crud.create_directory(db, "root", "Home", "/", True, None, datetime.now(), user_id)

#         # 디렉토리만 필터링. 디렉토리 구조만 보내면 됨.
#         directories = crud.get_only_directory(db, user_id)

#         # <로직>
       

#         # 2) 새 리스트에 수정된 객체 생성
#         your_result = []
#         for d in directories:
#             # 루트 디렉토리는 제외.( 루트 디렉토리는 프론트엔드에서 처리해준다.)
#             # db입장에서 필요한 데이터이지만 프론트 엔드로 해당 정보를 보낼 시 이미 프론트엔드에서 처리하기 때문에 충돌이 발생하여 버그가 발생함.
#             if d['id'] == "root":
#                 continue
#             your_result.append({
#                 'id':   d['id'],
#                 'name': d['name'],
#                 'path': d['path']
#             })
#         # </로직>
#         directories = your_result

#         return {
#             "directories": directories}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error fetching filesystem structure: {str(e)}")

# @router.post("/query")
# async def query_document(
#     query: str = Form(...), 
#     current_user: User = Depends(get_current_user),
#     db: Session = Depends(get_db)
# ):

#     """문서 질의응답 엔드포인트"""
#     from db.database import engine  # 기존 엔진을 임포트
#     # debugging.stop_debugger()
#     user_id = current_user.id

#     pass


#     # docs = process_query(user_id,query,engine)
    
#     # answer = get_llms_answer(docs, query)


#     # return {"answer": answer} 

# 단일 파일 다운로드 엔드포인트 생성 위치

@router.get("/{document_id}/download")
async def download_single_file(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """단일 파일 다운로드"""
    from db import crud
    try:
        # 1. 사용자 권한 확인
        user_id = current_user.id
        
        # 파일 ID 타입 확인 및 변환
        try:
            doc_id = int(document_id) if isinstance(document_id, str) else document_id
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="유효하지 않은 파일 ID입니다.")
        
        # 2. 다운로드에 필요한 파일 정보 준비
        # documents 테이블에서 파일 정보 가져오기
        document = crud.get_document_by_id(db, doc_id, user_id)
        if not document:
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        
        # 파일 소유자 확인 (권한 검증)
        if document.user_id != user_id:
            raise HTTPException(status_code=403, detail="파일 다운로드 권한이 없습니다.")
        
        # S3 정보 추출
        s3_key = document.s3_key
        filename = document.filename
        
        logger.info(f"단일 파일 다운로드 시작: {filename} (사용자: {user_id})")
        
        # 3. S3에서 파일 존재 확인
        try:
            # S3 객체 메타데이터 가져오기 (파일 존재 및 크기 확인)
            s3_response = s3_client.head_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
            file_size = s3_response['ContentLength']
            content_type = s3_response.get('ContentType', 'application/octet-stream')
        except s3_client.exceptions.NoSuchKey:
            logger.error(f"S3에서 파일을 찾을 수 없습니다: {s3_key}")
            raise HTTPException(status_code=404, detail="파일이 S3에 존재하지 않습니다.")
        except Exception as e:
            logger.error(f"S3 파일 확인 중 오류: {str(e)}")
            raise HTTPException(status_code=500, detail="파일 확인 중 오류가 발생했습니다.")
        
        # 4. 스트리밍 다운로드 함수 정의
        def file_iterator(chunk_size: int = 8192):
            """S3에서 파일을 청크 단위로 읽어서 스트리밍"""
            try:
                # S3에서 파일 스트림 가져오기
                s3_object = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
                stream = s3_object['Body']
                
                # 청크 단위로 읽어서 yield
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
                        
            except Exception as e:
                logger.error(f"파일 스트리밍 중 오류: {str(e)}")
                raise HTTPException(status_code=500, detail="파일 다운로드 중 오류가 발생했습니다.")
        
        # 한글 파일명 처리 (RFC 6266 표준)
        filename_ascii = filename.encode('ascii', 'ignore').decode('ascii')
        filename_encoded = quote(filename.encode('utf-8'))

        # 5. StreamingResponse로 파일 전달
        return StreamingResponse(
            file_iterator(),
            media_type=content_type,
            headers={
                "Content-Length": str(file_size),
                "Content-Disposition": f'attachment; filename="{filename_ascii}"; filename*=UTF-8"{filename_encoded}"',
                "Cache-Control": "no-cache"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"단일 파일 다운로드 중 예상치 못한 오류: {str(e)}")
        raise HTTPException(status_code=500, detail="파일 다운로드 중 오류가 발생했습니다.")


# 다중 파일 ZIP 다운로드 엔드포인트 생성 위치

class DownloadZipRequest(BaseModel):
    fileIds: List[str]  # 문자열 배열로 변경하여 호환성 향상
    zipName: str = "selected_files.zip"

@router.post("/download-zip")
async def download_multiple_files_as_zip(
    request_data: DownloadZipRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """다중 파일을 ZIP으로 압축하여 다운로드"""
    from db import crud
    import zipfile
    import io
    import asyncio
    
    try:
        # 1. 사용자 권한 확인
        user_id = current_user.id
        file_ids = request_data.fileIds
        zip_name = request_data.zipName
        
        if not file_ids:
            raise HTTPException(status_code=400, detail="다운로드할 파일을 선택해주세요.")
        
        # 파일 ID들을 정수로 변환
        try:
            int_file_ids = [int(fid) for fid in file_ids]
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="유효하지 않은 파일 ID가 포함되어 있습니다.")
        
        logger.info(f"ZIP 다운로드 시작: {len(int_file_ids)}개 파일 (사용자: {user_id})")
        
        # 2. 모든 파일 정보 가져오기 및 권한 확인
        documents = []
        for file_id in int_file_ids:
            document = crud.get_document_by_id(db, file_id, user_id)
            if not document:
                logger.warning(f"파일 ID {file_id}를 찾을 수 없습니다.")
                continue
            
            # 파일 소유자 확인
            if document.user_id != user_id:
                logger.warning(f"파일 ID {file_id}에 대한 권한이 없습니다.")
                continue
                
            documents.append(document)
        
        if not documents:
            raise HTTPException(status_code=404, detail="다운로드 가능한 파일이 없습니다.")
        
        # 3. ZIP 스트림 생성 함수 (실시간 압축 및 전송)
        async def zip_stream_generator():
            """파일들을 실시간으로 압축하면서 스트리밍"""
            try:
                # 메모리 내 ZIP 파일 생성
                zip_buffer = io.BytesIO()
                
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    
                    for i, document in enumerate(documents):
                        # 끊김 감지 - 클라이언트가 연결을 끊었는지 확인
                        if await request.is_disconnected():
                            logger.info("클라이언트 연결이 끊어졌습니다. ZIP 생성을 중단합니다.")
                            break
                        
                        try:
                            # S3에서 파일 다운로드
                            s3_response = s3_client.get_object(
                                Bucket=S3_BUCKET_NAME, 
                                Key=document.s3_key
                            )
                            file_data = s3_response['Body'].read()
                            
                            # ZIP에 파일 추가 (파일명 중복 방지)
                            filename = document.filename
                            # 동일한 파일명이 있을 경우 번호 추가
                            counter = 1
                            original_filename = filename
                            while filename in [info.filename for info in zip_file.infolist()]:
                                name, ext = os.path.splitext(original_filename)
                                filename = f"{name}_{counter}{ext}"
                                counter += 1
                            
                            zip_file.writestr(filename, file_data)
                            logger.info(f"ZIP에 파일 추가: {filename} ({i+1}/{len(documents)})")
                            
                        except s3_client.exceptions.NoSuchKey:
                            logger.warning(f"S3에서 파일을 찾을 수 없습니다: {document.s3_key}")
                            continue
                        except Exception as e:
                            logger.error(f"파일 {document.filename} 처리 중 오류: {str(e)}")
                            continue
                
                # ZIP 파일 완성 후 스트리밍
                zip_buffer.seek(0)
                
                # 8KB 청크 단위로 전송
                chunk_size = 8192
                while True:
                    # 끊김 감지
                    if await request.is_disconnected():
                        logger.info("클라이언트 연결이 끊어졌습니다. ZIP 전송을 중단합니다.")
                        break
                    
                    chunk = zip_buffer.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
                
                logger.info(f"ZIP 다운로드 완료: {zip_name}")
                
            except asyncio.CancelledError:
                logger.info("ZIP 생성이 취소되었습니다.")
                raise
            except Exception as e:
                logger.error(f"ZIP 생성 중 오류: {str(e)}")
                raise HTTPException(status_code=500, detail="ZIP 파일 생성 중 오류가 발생했습니다.")
        
        # 한글 ZIP 파일명 처리
        zip_name_ascii = zip_name.encode('ascii', 'ignore').decode('ascii')
        zip_name_encoded = quote(zip_name.encode('utf-8'))        

        # 4. StreamingResponse로 ZIP 파일 전달
        return StreamingResponse(
            zip_stream_generator(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name_ascii}"; filename*=UTF-8"{zip_name_encoded}"',
                "Cache-Control": "no-cache",
                "Transfer-Encoding": "chunked"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ZIP 다운로드 중 예상치 못한 오류: {str(e)}")
        raise HTTPException(status_code=500, detail="ZIP 다운로드 중 오류가 발생했습니다.")

# -----------------------------------------
# -----------------------------------------
# -----------------------------------------
# -----------------------------------------
# -----------------------------------------
# -----------------------------------------

# 유틸 함수
async def process_directory_uploads(current_upload_path, directory_structure, user_id, db):
    """디렉토리 업로드 처리"""
    # 라이브러리 임포트
    import json
    from typing import Dict, Any
    from db import crud

    # 결과가 저장될 리스트를 미리 선언
    results = []

    # 1. 문자열로 받은 디렉토리 구조를 파이썬 dict로 변환
    tree: Dict[str, Any] = json.loads(directory_structure)

    # 2. 최상위 디렉토리 처리
        # 최상위 디렉토리를 처리하는 함수 선언
    top_dir_results, top_dir_children = process_top_directory(tree, current_upload_path, db, user_id)
 
    # Store DB : directories 테이블에 저장
    results.append(store_directory_table(db, top_dir_results, user_id))

    # 3. 재귀로 하위 디렉토리 및 파일 처리
    async def traverse(name: str, subtree: Dict[str, Any], parent_id: str, parent_path: str):
        # 디렉토리 생성
        child_dir_id = str(uuid.uuid4())
        child_dir_name = name
        #* 변경: OS 종속적 os.path.join 대신 '/' 문자열 조합 사용
        child_dir_path = f"{parent_path.rstrip('/')}/{name}"

        # 디렉토리 테이블에 저장할 딕셔너리 생성
        child_dir_value_dict = {
            "id": child_dir_id,
            "name": child_dir_name,
            "path": child_dir_path,
            "is_directory": True,
            "parent_id": parent_id,
            "created_at": datetime.now().isoformat()
        }
        
        # Store DB : directories 테이블에 저장
        results.append(store_directory_table(db, child_dir_value_dict, user_id))
        

        # 3-1. 하위 디렉토리 탐색
        for child_name, child_tree in subtree.items():
            await traverse(child_name, child_tree, child_dir_id, child_dir_path)


    # 4. 실제 트리 순회 시작
    # root 자체가 아닌, 루트의 자식들부터 순회하도록 변경  #* 변경된 부분
    for child_name, child_tree in top_dir_children.items():
        await traverse(child_name, child_tree, top_dir_results["id"], top_dir_results["path"])

    return results


async def process_file_uploads(files, current_upload_path, current_user, db):
    """파일 업로드 처리"""
    from db import crud
    # 결과가 저장될 리스트를 미리 선언
    results = []
    user_id = current_user.id
    user_username = current_user.username

    try:
    # 3-2. 해당 디렉토리에 포함된 파일 처리
    # [해결]ㅈ버그 발견 : files에 파일이 2개 이상일 경우에 1번째 파일은 읽는데 성공하지만 2번째 파일부터는 파일이 닫혀서 읽을 수 없다.
        for upload_file in files:
            # 파일 이름을 추출 & 파일 이름 중복 처리.
            file_name = set_filename(upload_file, db, user_id)
            # debugging.stop_debugger()

            # s3 key 생성 v
            s3_key = f"uploads/{user_username}/{file_name}"

            # 파일 경로 설정 v
            file_path, file_path_dir = set_file_path(file_name, upload_file, current_upload_path)
            # debugging.stop_debugger()

            # file_path_dir가 db-> directories 테이블에 존재하면 그 레코드에서 id값을 가져온다.
            # 만약 file_path_dir가 '/'이면, 즉 루트위치에 업로드 하는 것이라면 prent_id는 무조건 "root". -> issue 97 comment check
            if file_path_dir == "/":
                parent_id = "root"
            else:
                parent_id = crud.get_directory_id_by_path(db, file_path_dir, user_id)
            # debugging.stop_debugger()

            # 파일 업로드 처리 시작
            # <파일의 내용을 여러 번 재사용하기 위해 메모리에 로드.>
            file_content = await upload_file.read()
            # debugging.stop_debugger()
            
            # 문서
            
            # s3 업로드 v
            s3_upload_result = await upload_file_to_s3(upload_file, s3_key, file_name, file_path)
            # debugging.stop_debugger()
            results.append(s3_upload_result)

            # 문서 저장
            document_id = await process_document(
                        file_name=file_name,
                        file_path=file_path,
                        file_content=file_content,
                        user_id=user_id,
                        db=db,
                        s3_key=s3_key
                    )
            # debugging.stop_debugger()

            # 디렉토리 테이블에 저장할 데이터 준비
            directory_value_dict = {
                "id": document_id,
                "name": file_name,
                "path": file_path,
                "is_directory": False,
                "parent_id": parent_id,
                "created_at": datetime.now().isoformat()
            }
            # 디렉토리 테이블에 정보 저장
            results.append(store_directory_table(db, directory_value_dict, user_id))
    except Exception as e:
        print(e)
    return results


async def process_directory_operations(operations, user_id: int, db):
    """디렉토리 작업 처리 (생성, 이동, 삭제 등)"""
    from db import crud
    import asyncio
    results = [] #결과값 객체들이 담김.
    
    for op in operations:
        op_type = op.get("operation_type")
        reserved_item_id = op.get("item_id", None)
        reserved_item_name = op.get("name", None)
        # 아래 코드는 논리적으로 잘못되었다. 각 작업에서 받는 데이터를 확인 후 분기문 안에서 reserved_path 받도록 수정하기.
        if op.get("target_path", None) or op.get("path", None) == "":
            reserved_path = '/'
        else:
            reserved_path = op.get("target_path", None) or op.get("path", None)

        
        if reserved_item_id:
            # 아이템의 디렉토리 여부
            item_is_directory = crud.get_file_is_directory_by_id(db, reserved_item_id, user_id)


        try:
            # 새 폴더 생성
            if op_type == "create":
                new_folder_id = str(uuid.uuid4())
                 
                target_item_original_name = reserved_item_name
                # 이름 중복 확인
                target_item_new_name = generate_unique_directory_name(db, target_item_original_name, user_id)                   
                
                if reserved_path == "/":
                    # 새 폴더를 생성하는 위치가 루트인 경우
                    # 새로운 path 설정
                    item_path =  reserved_path+target_item_new_name
                    # 새 폴더의 parent_id는 root이다.
                    parent_id = "root"

                    # 디렉토리 테이블에 저장할 데이터 준비
                    directory_value_dict = {
                        "id": new_folder_id,
                        "name": target_item_new_name,
                        "path": item_path,
                        "is_directory": True,
                        "parent_id": parent_id,
                        "created_at": datetime.now().isoformat(),
                        "operation":op_type
                    }
                    # debugging.stop_debugger()
                    # 디렉토리 정보 저장
                    results.append(store_directory_table(db, directory_value_dict, user_id))                    
                else:
                    # 새 폴더를 생성하는 위치가 루트가 아닌 경우
                    # 새로운 path 설정
                    item_path = reserved_path +"/"+ target_item_new_name
                    # 새 폴더가 생성되는 디렉토리의 id를 가져온다.
                    parent_id = crud.get_directory_id_by_path(db,reserved_path, user_id)
                    # 디렉토리 테이블에 저장할 데이터 준비
                    directory_value_dict = {
                        "id": new_folder_id,
                        "name": target_item_new_name,
                        "path": item_path,
                        "is_directory": True,
                        "parent_id": parent_id,
                        "created_at": datetime.now().isoformat(),
                        "operation":op_type
                    }                    
                    # debugging.stop_debugger()
                    # 디렉토리 정보 저장
                    results.append(store_directory_table(db, directory_value_dict, user_id))
            
            # 항목 이동
            elif op_type == "move":
                """move 작업이 받는 데이터:
                item_id, target_path
                """
                # 프론트 엔드에서 오는 target_path의 의미는 destination_path임. 프론트엔드에서 오는 변수명 수정 필요.
                reserved_path = op.get("target_path", None)
                # 목적지 경로가 루트일 경우 ""를 전달하는 오류를 임시로 해결.
                if reserved_path == "":
                    reserved_path = '/'


                # target_item_current_path = reserved_path # 이 줄은 수정 후에 삭제.
                target_item_destination_path = reserved_path 
                # target item의 parent_id 가져오기
                target_item_parent_id = crud.get_parent_id_by_id(db, reserved_item_id, user_id)
                
                # target item의 기존 경로 가져오기
                target_item_path = crud.get_file_path_by_id(db, reserved_item_id, user_id)
                # target item이 속해 있는 디렉토리의 경로.
                target_item_current_path = crud.get_file_path_by_id(db, target_item_parent_id, user_id)
                # target item의 기존 이름 가져오기
                target_item_name = crud.get_file_name_by_id(db, reserved_item_id, user_id)
                # target item의 타입 가져오기
                target_item_type = crud.get_file_is_directory_by_id(db, reserved_item_id, user_id)
                # 목적지의 id값 가져오기
                # target_item_destination_path가 "/"인 경우, 분기 -> issue 97 comment check
                if target_item_destination_path == "/":
                    destination_id = "root"
                else:
                    destination_id = crud.get_directory_id_by_path(db, target_item_destination_path, user_id)
                
                # debugging.stop_debugger()

                # target item의 새로운 parent_id 준비
                target_new_parent_id = destination_id

                if target_item_current_path == "/": # target이 root디렉토리에 존재하는 경우
                    if target_item_destination_path != "/": # 이동할 목적지가 root가 아닌 경우

                        # target item의 새로운 경로 준비
                        target_new_path = target_item_destination_path + target_item_path  
                        # target item이 디렉토리인 경우 하위 아이템이 존재한다면 해당 item의 레코드를 반환
                        target_item_children = crud.get_directory_by_parent_id(db, reserved_item_id, user_id)

                        if target_item_children: # 하위 아이템이 존재하는 경우
                            # 하위 아이템이 존재하는 경우에는 sql 스크립트를 실행. (자식 아이템까지 재귀적으로 처리됨.)
                            crud.update_directory_with_sql_file_safe(db, reserved_item_id, target_item_path, target_new_path, target_new_parent_id, user_id)
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})
                            # debugging.stop_debugger()
                        else: # 하위 아이템이 존재하지 않는 경우
                            # 디렉토리 경로 및 부모 id 업데이트
                            crud.update_directory_path_and_parent(db, reserved_item_id, target_new_path, target_new_parent_id, user_id)
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})
                            # debugging.stop_debugger()
                    else: # 이동할 목적지가 root인 경우(이동하지 않음)
                        # 아무 작업도 하지 않음.
                        results.append({
                                        "operation": "move",
                                        "type": "directory" if target_item_type else "file",
                                        "id": reserved_item_id,
                                        "name": target_item_name,
                                        "old_path": target_item_path,
                                        "new_path": target_item_current_path,
                                        "status": "success"})
                else: # target이 root디렉토리에 존재하지 않는 경우
                    # target item이 디렉토리인 경우 하위 아이템이 존재한다면 해당 item의 레코드를 반환
                    target_item_children = crud.get_directory_by_parent_id(db, reserved_item_id, user_id)
                    # 부모 아이템의 path == 현재 아이템이 속해 있는 디렉토리의 path.
                    parent_item_path = target_item_current_path                  
                    if target_item_destination_path != "/": # 이동할 목적지가 root가 아닌 경우
                        if target_item_children: # 하위 아이템이 존재하는 경우
                            # target item의 새로운 경로 준비
                            target_new_path = target_item_destination_path + target_item_path.replace(parent_item_path, "")
                            # 디렉토리 경로 및 부모 id 업데이트
                            # 하위 아이템이 존재하는 경우에는 sql 스크립트를 실행. (자식 아이템까지 재귀적으로 처리됨.)
                            # debugging.stop_debugger()
                            crud.update_directory_with_sql_file_safe(db, reserved_item_id, target_item_path, target_new_path, target_new_parent_id, user_id)
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})                            
                        else: # 하위 아이템이 존재하지 않는 경우
                            # target item의 새로운 경로 준비
                            target_new_path = target_item_destination_path + '/' + target_item_name
                            # 디렉토리 경로 및 부모 id 업데이트
                            # debugging.stop_debugger()
                            crud.update_directory_path_and_parent(db, reserved_item_id, target_new_path, target_new_parent_id, user_id)
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})
                    else: # 이동할 목적지가 root인 경우
                        if target_item_children: # 하위 아이템이 존재하는 경우
                            # target item의 새로운 경로 준비
                            target_new_path = target_item_path.replace(parent_item_path, "")
                            # 디렉토리 경로 및 부모 id 업데이트
                            # 하위 아이템이 존재하는 경우에는 sql 스크립트를 실행. (자식 아이템까지 재귀적으로 처리됨.)
                            # debugging.stop_debugger()
                            crud.update_directory_with_sql_file_safe(db, reserved_item_id, target_item_path, target_new_path, target_new_parent_id, user_id)                            
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})                      
                        else: # 하위 아이템이 존재하지 않는 경우
                            # target item의 새로운 경로 준비
                            target_new_path = target_item_destination_path + target_item_name
                            # debugging.stop_debugger()
                            # 디렉토리 경로 및 부모 id 업데이트
                            crud.update_directory_path_and_parent(db, reserved_item_id, target_new_path, target_new_parent_id, user_id)
                            results.append({
                                            "operation": "move",
                                            "type": "directory" if target_item_type else "file",
                                            "id": reserved_item_id,
                                            "name": target_item_name,
                                            "old_path": target_item_path,
                                            "new_path": target_new_path,
                                            "status": "success"})  

            # 항목 삭제
            elif op_type == "delete":
                
                item_is_directory = crud.get_file_is_directory_by_id(db, reserved_item_id, user_id)
                # 항목의 이름과 경로를 가져오기
                item_name = crud.get_file_name_by_id(db, reserved_item_id, user_id)
                item_path = crud.get_file_path_by_id(db, reserved_item_id, user_id)
                # 자식이 포함되어 있는지 여부 확인을 위한 조사.
                item_has_children = crud.get_directory_by_parent_id(db, reserved_item_id, user_id)
                # debugging.stop_debugger()

                if item_is_directory:# 디렉토리인 경우
                    if item_has_children:# 자식이 존재할 경우
                        # 자식 중 파일들의 리스트를 가져온다.
                        child_file_ids = crud.get_child_file_ids(db, reserved_item_id, user_id)
                        
                        for file_id in child_file_ids:
                            # 파일의 기존 이름 가져오기
                            file_original_name = crud.get_file_name_by_id(db, file_id, user_id)
                            # 파일의 기존 경로 가져오기
                            file_original_path = crud.get_file_path_by_id(db, file_id, user_id)
                            # debugging.stop_debugger()
                            # s3와 db에서 삭제.
                            results.append(delete_file(db, file_id, file_original_name, file_original_path, s3_client, user_id))
                        # 디렉토리 재귀적 전부 삭제.
                        # debugging.stop_debugger()
                        results.append(delete_directory(db, reserved_item_id, item_name, item_path, user_id))


                    else: # 단일 디렉토리인 경우
                    # 디렉토리 재귀적 전부 삭제.
                        results.append(delete_directory(db, reserved_item_id, item_name, item_path, user_id))
                else:
                    # 파일인 경우                  
                    # s3와 db에서 삭제.
                    results.append(delete_file(db, reserved_item_id, item_name, item_path, s3_client, user_id))

            # 항목 이름 변경
            elif op_type == "rename":
                """
                이 블럭이 실행되기 위해 필요한 변수:
                reserved_item_id
                reserved_item_name
                s3_client
                """                
                reserved_item_new_name = reserved_item_name


                # 아이템의 자식포함 여부
                item_has_children = crud.get_directory_by_parent_id(db, reserved_item_id, user_id)
                # 아이템의 기존 이름 가져오기
                target_item_original_name = crud.get_file_name_by_id(db, reserved_item_id, user_id)
                # target item의 기존 경로 가져오기
                target_item_original_path = crud.get_file_path_by_id(db, reserved_item_id, user_id)                

                # target item의 parent_id 가져오기
                target_item_parent_id = crud.get_parent_id_by_id(db, reserved_item_id, user_id)                


                if item_is_directory:
                    # 디렉토리인 경우
                    if item_has_children:
                        # 자식 아이템이 존재하는 경우
                        # 자식 아이템중 파일의 id 리스트 가져오기
                        child_file_ids = crud.get_child_file_ids(db, reserved_item_id, user_id)
                        # 자식 아이템의 s3_key 리스트 가져오기 <- 필요하면 주석 해제.
                        # child_file_s3_keys = [crud.get_s3_key_by_id(db, file_id, user_id) for file_id in child_file_ids]
                        
                        # 타겟 디렉토리 포함 디렉토리인 항목만 이름 전부 교체
                        # 타겟의 새로운 경로
                        item_new_path = target_item_original_path.replace(target_item_original_name, reserved_item_new_name)
                        crud.update_directory_and_child_dirs(db, reserved_item_id, target_item_original_path, reserved_item_new_name, item_new_path, user_id)

                        if child_file_ids:
                            # 파일 처리 (비동기)
                            tasks = []
                            for file_id in child_file_ids:
                                # 파일의 기존 이름 가져오기
                                file_original_name = crud.get_file_name_by_id(db, file_id, user_id)
                                # 파일의 기존 경로 가져오기
                                file_original_path = crud.get_file_path_by_id(db, file_id, user_id)
                                # 기존 경로에서 target_item_original_name을 제거하고 reserved_item_new_name로 바꿔야 함.
                                # 파일이 저장되어 있는 디렉토리 id 가져오기
                                file_parent_id = crud.get_parent_id_by_id(db, file_id, user_id)
                                print(f"처리 중인 파일 ID: {file_id}")  # 예시: 로그 출력
                                # 비동기 함수(코루틴 객체) 생성
                                task = edit_path_and_reupload_document(
                                    db, user_id, file_id, file_original_name, target_item_original_name, reserved_item_new_name, file_original_path,
                                    file_parent_id, s3_client, op_type
                                )
                                tasks.append(task)
                            # results_list의 실제 값 확인 필요.
                            results_list = await asyncio.gather(*tasks)
                            # 결과를 합치려면
                            for result in results_list:
                                results.extend(result)
                        
                        results.append({
                            "operation": "rename",
                            "type": "directory",
                            "id": reserved_item_id,
                            "name": reserved_item_new_name,
                            "old_path": target_item_original_path,
                            "new_path": item_new_path,
                            "status": "success" 
                        })                        
                        
                    else:
                        # 자식 아이템이 존재하지 않는 경우
                        # 아이템의 새로운 경로 설정
                        item_new_path = target_item_original_path.replace(target_item_original_name, reserved_item_new_name)
                        # 새로운 이름, 경로로 디렉토리 테이블의 필드값 업데이트
                        crud.update_item_name_and_path(db, reserved_item_id, reserved_item_new_name, item_new_path, user_id)
                        results.append({
                            "operation": "rename",
                            "type": "directory",
                            "id": reserved_item_id,
                            "name": reserved_item_new_name,
                            "old_path": target_item_original_path,
                            "new_path": item_new_path,
                            "status": "success" 
                        })
                else:
                    # 파일인 경우 아래 블럭을 함수화 할 수 있는지 확인.
                    # 필요한 변수 reserved_item_id, 
                    results.extend(await rename_and_reupload_document(db, user_id, reserved_item_id, target_item_original_name, target_item_original_path, reserved_item_new_name, target_item_parent_id, s3_client, op_type))
            
            # 항목 복사
            elif op_type == "copy":
                """사용 변수
                reserved_path
                reserved_item_id
                """

                target_item_id = reserved_item_id
                target_destination_path = reserved_path

                # 아이템의 디렉토리 여부 가져오기.
                item_is_directory = crud.get_file_is_directory_by_id(db, target_item_id, user_id)
                # target item의 기존 경로 가져오기
                target_item_original_path = crud.get_file_path_by_id(db, target_item_id, user_id)
                # 아이템의 기존 이름 가져오기
                target_item_original_name = crud.get_file_name_by_id(db, target_item_id, user_id)
                # 파일이 저장되어 있는 디렉토리 id 가져오기
                file_parent_id = crud.get_parent_id_by_id(db, target_item_id, user_id)
                # 아이템에게 자식이 있는지 여부
                item_has_children = crud.get_directory_by_parent_id(db, target_item_id, user_id)

                
                # 아이템을 복사한 위치를 가져오기. target아이템의 parent_id에 해당하는 레코드의 path
                target_item_copied_path = crud.get_file_path_by_id(db, crud.get_parent_id_by_id(db, target_item_id, user_id), user_id)
               
                # 파일인지 디렉토리인지 판단
                if item_is_directory:# 디렉토리인 경우
                    if item_has_children: # 자식 아이템이 존재하는 경우
                        if target_item_copied_path == target_destination_path:# 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우.
                            if target_destination_path == "/":# 그 와중에 목적지가 루트인 경우
                                # target의 부모의 기존 path. 
                                parent_path_of_target = target_item_copied_path
                                
                                # target 처리
                                    # target의 새 아이디 설정
                                target_new_id = str(uuid.uuid4())
                                    # target의 새 이름 설정
                                target_new_name = generate_unique_directory_name(db, target_item_original_name, user_id)
                                    # target의 새 경로 설정
                                target_new_path = target_destination_path + target_new_name
                                    # target의 새 부모 설정
                                target_item_new_parent_id = "root"
                                # 저장될 데이터를 일반화
                                id = target_new_id
                                name = target_new_name
                                path = target_new_path
                                parent_id = target_item_new_parent_id
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": item_is_directory,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                                #
                                # 자식 디렉토리 처리
                                # 자식 디렉토리 리스트
                                child_directories = crud.get_child_directory_ids(db, target_item_id, user_id)
                                for child_directory in child_directories:
                                    # 새 id
                                    child_new_directory_id = str(uuid.uuid4())
                                    # 이름
                                        # 기존 이름 그대로 사용
                                    child_original_name = crud.get_file_name_by_id(db, child_directory, user_id)
                                    # 경로
                                    child_new_path = crud.get_file_path_by_id(db, child_directory, user_id).replace(target_item_original_name,target_new_name)
                                    
                                    # parent_id
                                    child_new_parent_id = crud.get_directory_id_by_path(db, child_new_path.replace("/"+child_original_name,""), user_id)
                                    
                                    # 저장될 데이터를 일반화
                                    id = child_new_directory_id
                                    name = child_original_name
                                    path = child_new_path
                                    parent_id = child_new_parent_id
                                    # 디렉토리 테이블에 저장할 데이터 준비
                                    directory_value_dict = {
                                        "id": id,
                                        "name": name,
                                        "path": path,
                                        "is_directory": item_is_directory,
                                        "parent_id": parent_id,
                                        "created_at": datetime.now().isoformat(),
                                        "operation":op_type
                                    }
                                    # 디렉토리 정보 저장
                                    results.append(store_directory_table(db, directory_value_dict, user_id))                                
                                # 자식 파일 처리
                                # 자식 파일 리스트
                                child_files = crud.get_child_file_ids(db, target_item_id, user_id)
                                    # 자식 파일 처리
                                for child_file in child_files:
                                    # 파일의 새 이름 설정
                                        # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
                                    # 자식 파일의 기존 이름 가져오기
                                    child_file_original_name = crud.get_file_name_by_id(db,child_file, user_id)
                                    child_file_new_name = generate_unique_filename(db, child_file_original_name, user_id)
                                    # 파일의 새 s3_key 설정
                                        # 기존 s3_key
                                    child_file_original_s3_key = crud.get_s3_key_by_id(db, child_file, user_id)
                                        # 새 s3_key
                                    child_file_new_s3_key = child_file_original_s3_key.replace(child_file_original_name, child_file_new_name)
                                    # 파일의 새 아이디 설정
                                    child_file_new_id = crud.add_documents(db, child_file_new_name, child_file_new_s3_key, datetime.now(), user_id).id
                                    # 파일의 새 경로 설정
                                    # 파일의 기존 경로 가져오기
                                    child_file_original_path = crud.get_file_path_by_id(db, child_file, user_id)
                                        # 1. 경로 부분 변경, 2. 기존 파일 이름을 새 파일 이름으로 교체.
                                    child_file_new_path = child_file_original_path.replace(target_item_original_name,target_new_name).replace(child_file_original_name,child_file_new_name)
                                    
                                    child_file_new_parent_id = crud.get_directory_id_by_path(db,child_file_new_path.replace("/"+child_file_new_name,""), user_id)
                                    # s3로 복사
                                    # 버킷 내 다른 위치로 파일 복사
                                    s3_client.copy_object(
                                        Bucket=S3_BUCKET_NAME,
                                        CopySource={'Bucket': S3_BUCKET_NAME, 'Key': child_file_original_s3_key},
                                        Key=child_file_new_s3_key
                                    )
                                    # s3 에서 해당 파일 데이터를 가져오기.
                                    response = s3_client.get_object(
                                        Bucket=S3_BUCKET_NAME,
                                        Key=child_file_new_s3_key
                                    )
                                    # 저장될 데이터를 일반화
                                    id = child_file_new_id
                                    name = child_file_new_name
                                    path = child_file_new_path
                                    parent_id = child_file_new_parent_id
                                    s3_key = child_file_new_s3_key
                                    file_content = response['Body'].read()  # file_content는 bytes 타입
                        
                                    # 문서 새로 저장
                                    await process_document(
                                                    file_name=name,
                                                    file_path=path,
                                                    file_content=file_content,
                                                    user_id=user_id,
                                                    db=db,
                                                    s3_key=s3_key
                                                )                        
                                    # 디렉토리 테이블에 저장할 데이터 준비
                                    directory_value_dict = {
                                        "id": id,
                                        "name": name,
                                        "path": path,
                                        "is_directory": False,
                                        "parent_id": parent_id,
                                        "created_at": datetime.now().isoformat(),
                                        "operation":op_type
                                    }                        
                                    # 디렉토리 정보 저장
                                    results.append(store_directory_table(db, directory_value_dict, user_id))                                 
                            else:# 목적지가 루트가 아니지만 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일함.
                                # target 처리
                                # target의 새 아이디 설정
                                target_new_id = str(uuid.uuid4())
                                # target의 새 이름 설정
                                target_new_name = generate_unique_directory_name(db, target_item_original_name, user_id)
                                # target의 새 경로 설정
                                target_new_path = target_item_original_path.replace(target_item_original_name, target_new_name)
                                # target의 부모 설정
                                # 기존 parent_id 그대로 사용. 
                                target_original_parent_id = file_parent_id
                                # 저장될 데이터를 일반화
                                id = target_new_id
                                name = target_new_name
                                path = target_new_path
                                parent_id = target_original_parent_id
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": item_is_directory,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                                #
                                # 자식 디렉토리 처리
                                # 자식 디렉토리 리스트
                                child_directories = crud.get_child_directory_ids(db, target_item_id, user_id)
                                for child_directory in child_directories:
                                    # 새 id
                                    child_new_directory_id = str(uuid.uuid4())
                                    # 이름
                                        # 기존 이름 그대로 사용
                                    child_original_name = crud.get_file_name_by_id(db, child_directory, user_id)
                                    # 경로
                                    child_new_path = crud.get_file_path_by_id(db, child_directory, user_id).replace(target_item_original_name,target_new_name)
                                    # parent_id
                                    child_new_parent_id = crud.get_directory_id_by_path(db,child_new_path.replace("/"+child_original_name,""), user_id)
                                    
                                    # 저장될 데이터를 일반화
                                    id = child_new_directory_id
                                    name = child_original_name
                                    path = child_new_path
                                    parent_id = child_new_parent_id
                                    # 디렉토리 테이블에 저장할 데이터 준비
                                    directory_value_dict = {
                                        "id": id,
                                        "name": name,
                                        "path": path,
                                        "is_directory": item_is_directory,
                                        "parent_id": parent_id,
                                        "created_at": datetime.now().isoformat(),
                                        "operation":op_type
                                    }
                                    # 디렉토리 정보 저장
                                    results.append(store_directory_table(db, directory_value_dict, user_id))                                
                                # 자식 파일 처리
                                # 자식 파일 리스트
                                child_files = crud.get_child_file_ids(db, target_item_id, user_id)
                                    # 자식 파일 처리
                                for child_file in child_files:
                                    # 파일의 새 이름 설정
                                        # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
                                    # 자식 파일의 기존 이름 가져오기
                                    child_file_original_name = crud.get_file_name_by_id(db,child_file, user_id)
                                    child_file_new_name = generate_unique_filename(db, child_file_original_name, user_id)
                                    # 파일의 새 s3_key 설정
                                        # 기존 s3_key
                                    child_file_original_s3_key = crud.get_s3_key_by_id(db, child_file, user_id)
                                        # 새 s3_key
                                    child_file_new_s3_key = child_file_original_s3_key.replace(child_file_original_name, child_file_new_name)
                                    # 파일의 새 아이디 설정
                                    child_file_new_id = crud.add_documents(db, child_file_new_name, child_file_new_s3_key, datetime.now(), user_id).id
                                    # 파일의 새 경로 설정
                                    # 파일의 기존 경로 가져오기
                                    child_file_original_path = crud.get_file_path_by_id(db, child_file, user_id)
                                        # 1. 경로 부분 변경, 2. 기존 파일 이름을 새 파일 이름으로 교체.
                                    child_file_new_path = child_file_original_path.replace(target_item_original_name,target_new_name).replace(child_file_original_name,child_file_new_name)
                                    child_file_new_parent_id = crud.get_directory_id_by_path(db,child_file_new_path.replace("/"+child_file_new_name,""), user_id)
                                    # s3로 복사
                                    # 버킷 내 다른 위치로 파일 복사
                                    s3_client.copy_object(
                                        Bucket=S3_BUCKET_NAME,
                                        CopySource={'Bucket': S3_BUCKET_NAME, 'Key': child_file_original_s3_key},
                                        Key=child_file_new_s3_key
                                    )
                                    # s3 에서 해당 파일 데이터를 가져오기.
                                    response = s3_client.get_object(
                                        Bucket=S3_BUCKET_NAME,
                                        Key=child_file_new_s3_key
                                    )
                                    # 저장될 데이터를 일반화
                                    id = child_file_new_id
                                    name = child_file_new_name
                                    path = child_file_new_path
                                    parent_id = child_file_new_parent_id
                                    s3_key = child_file_new_s3_key
                                    file_content = response['Body'].read()  # file_content는 bytes 타입
                        
                                    # 문서 새로 저장
                                    await process_document(
                                                    file_name=name,
                                                    file_path=path,
                                                    file_content=file_content,
                                                    user_id=user_id,
                                                    db=db,
                                                    s3_key=s3_key
                                                )                        
                                    # 디렉토리 테이블에 저장할 데이터 준비
                                    directory_value_dict = {
                                        "id": id,
                                        "name": name,
                                        "path": path,
                                        "is_directory": False,
                                        "parent_id": parent_id,
                                        "created_at": datetime.now().isoformat(),
                                        "operation":op_type
                                    }                        
                                    # 디렉토리 정보 저장
                                    results.append(store_directory_table(db, directory_value_dict, user_id))                                
                        elif (target_item_copied_path != '/') and (target_destination_path == "/"):# 목적지가 루트인 경우

                            # target의 부모의 기존 path. 
                            parent_path_of_target = target_item_copied_path

                            # target 처리
                                # 디렉토리의 새 아이디 설정
                            new_directory_id = str(uuid.uuid4())
                                # 디렉토리 새 이름 설정
                                    # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
                                # 디렉토리의 새 경로 설정
                            target_item_new_path = target_destination_path + target_item_original_name
                                # 디렉토리의 새 부모 설정
                            target_item_new_parent_id = "root"

                            # 저장될 데이터를 일반화
                            id = new_directory_id
                            name = target_item_original_name
                            path = target_item_new_path
                            parent_id = target_item_new_parent_id
                            # 디렉토리 테이블에 저장할 데이터 준비
                            directory_value_dict = {
                                "id": id,
                                "name": name,
                                "path": path,
                                "is_directory": item_is_directory,
                                "parent_id": parent_id,
                                "created_at": datetime.now().isoformat(),
                                "operation":op_type
                            }
                            # 디렉토리 정보 저장
                            results.append(store_directory_table(db, directory_value_dict, user_id))

                            # 자식 디렉토리 처리
                            # 자식 디렉토리 리스트
                            child_directories = crud.get_child_directory_ids(db, target_item_id, user_id)
                            for child_directory in child_directories:
                                # 새 id
                                child_new_directory_id = str(uuid.uuid4())
                                # 이름
                                    # 기존 이름 그대로 사용
                                child_original_name = crud.get_file_name_by_id(db, child_directory, user_id)
                                # 경로
                                child_new_path = crud.get_file_path_by_id(db, child_directory, user_id).replace(parent_path_of_target,"")
                                # parent_id
                                    # 본인 path에서 "/"+<본인 name>을 찾아 ""으로 교체한 값이 directories테이블의 path필드값에 존재하면 그 존재하는 레코드의 id값으로 설정.
                                child_new_parent_id = crud.get_directory_id_by_path(db,child_new_path.replace("/"+child_original_name,""), user_id)
                                
                                # 저장될 데이터를 일반화
                                id = child_new_directory_id
                                name = child_original_name
                                path = child_new_path
                                parent_id = child_new_parent_id
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": item_is_directory,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                            
                            # 자식 파일 리스트
                            child_files = crud.get_child_file_ids(db, target_item_id, user_id)
                                # 자식 파일 처리
                            for child_file in child_files:
                                # 파일의 새 이름 설정
                                    # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
                                # 자식 파일의 기존 이름 가져오기
                                child_file_original_name = crud.get_file_name_by_id(db,child_file, user_id)
                                child_file_new_name = generate_unique_filename(db, child_file_original_name, user_id)
                                # 파일의 새 s3_key 설정
                                    # 기존 s3_key
                                child_file_original_s3_key = crud.get_s3_key_by_id(db, child_file, user_id)
                                    # 새 s3_key
                                child_file_new_s3_key = child_file_original_s3_key.replace(child_file_original_name, child_file_new_name)
                                # 파일의 새 아이디 설정
                                child_file_new_id = crud.add_documents(db, child_file_new_name, child_file_new_s3_key, datetime.now(), user_id).id
                                # 파일의 새 경로 설정
                                # 파일의 기존 경로 가져오기
                                child_file_original_path = crud.get_file_path_by_id(db, child_file, user_id)
                                    # 1. 경로 부분 변경, 2. 기존 파일 이름을 새 파일 이름으로 교체.
                                child_file_new_path = child_file_original_path.replace(parent_path_of_target,"").replace(child_file_original_name,child_file_new_name)
                                child_file_new_parent_id = crud.get_directory_id_by_path(db,child_file_new_path.replace("/"+child_file_new_name,""), user_id)
                                # s3로 복사
                                # 버킷 내 다른 위치로 파일 복사
                                s3_client.copy_object(
                                    Bucket=S3_BUCKET_NAME,
                                    CopySource={'Bucket': S3_BUCKET_NAME, 'Key': child_file_original_s3_key},
                                    Key=child_file_new_s3_key
                                )
                                # s3 에서 해당 파일 데이터를 가져오기.
                                response = s3_client.get_object(
                                    Bucket=S3_BUCKET_NAME,
                                    Key=child_file_new_s3_key
                                )
                                # 저장될 데이터를 일반화
                                id = child_file_new_id
                                name = child_file_new_name
                                path = child_file_new_path
                                parent_id = child_file_new_parent_id
                                s3_key = child_file_new_s3_key
                                file_content = response['Body'].read()  # file_content는 bytes 타입
                                # 문서 새로 저장
                                await process_document(
                                                file_name=name,
                                                file_path=path,
                                                file_content=file_content,
                                                user_id=user_id,
                                                db=db,
                                                s3_key=s3_key
                                            )                        
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": False,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }                        
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                                # 자식 파일 처리 끝.
                        else:# 목적지가 루트가 아닌 경우
                            # target의 부모의 기존 path. 
                            parent_path_of_target = target_item_copied_path

                            # target 처리
                                # 디렉토리의 새 아이디 설정
                            new_directory_id = str(uuid.uuid4())
                                # 디렉토리 새 이름 설정
                                    # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
                                # 디렉토리의 새 경로 설정
                            target_item_new_path = target_destination_path + "/" + target_item_original_name
                                # 디렉토리의 새 부모 설정
                            target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)

                            # 저장될 데이터를 일반화
                            id = new_directory_id
                            name = target_item_original_name
                            path = target_item_new_path
                            parent_id = target_item_new_parent_id
                            # 디렉토리 테이블에 저장할 데이터 준비
                            directory_value_dict = {
                                "id": id,
                                "name": name,
                                "path": path,
                                "is_directory": item_is_directory,
                                "parent_id": parent_id,
                                "created_at": datetime.now().isoformat(),
                                "operation":op_type
                            }
                            # 디렉토리 정보 저장
                            results.append(store_directory_table(db, directory_value_dict, user_id))

                            # 자식 디렉토리 처리
                            # 자식 디렉토리 리스트
                            child_directories = crud.get_child_directory_ids(db, target_item_id, user_id)
                            for child_directory in child_directories:
                                # 새 id
                                child_new_directory_id = str(uuid.uuid4())
                                # 이름
                                    # 기존 이름 그대로 사용
                                child_original_name = crud.get_file_name_by_id(db, child_directory, user_id)
                                # 경로
                                child_new_path = crud.get_file_path_by_id(db, child_directory, user_id).replace(parent_path_of_target,target_destination_path,1)
                                # parent_id
                                    # 본인 path에서 "/"+<본인 name>을 찾아 ""으로 교체한 값이 directories테이블의 path필드값에 존재하면 그 존재하는 레코드의 id값으로 설정.
                                child_new_parent_id = crud.get_directory_id_by_path(db,child_new_path.replace("/"+child_original_name,"",1), user_id)
                                
                                # 저장될 데이터를 일반화
                                id = child_new_directory_id
                                name = child_original_name
                                path = child_new_path
                                parent_id = child_new_parent_id
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": item_is_directory,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                            
                            # 자식 파일 리스트
                            child_files = crud.get_child_file_ids(db, target_item_id, user_id)
                                # 자식 파일 처리
                            for child_file in child_files:
                                # 파일의 새 이름 설정
                                    # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
                                # 자식 파일의 기존 이름 가져오기
                                child_file_original_name = crud.get_file_name_by_id(db,child_file, user_id)
                                child_file_new_name = generate_unique_filename(db, child_file_original_name, user_id)
                                # 파일의 새 s3_key 설정
                                    # 기존 s3_key
                                child_file_original_s3_key = crud.get_s3_key_by_id(db, child_file, user_id)
                                    # 새 s3_key
                                child_file_new_s3_key = child_file_original_s3_key.replace(child_file_original_name, child_file_new_name,1)
                                
                                # 파일의 새 아이디 설정
                                child_file_new_id = crud.add_documents(db, child_file_new_name, child_file_new_s3_key, datetime.now(), user_id).id
                                # 파일의 새 경로 설정
                                # 파일의 기존 경로 가져오기
                                child_file_original_path = crud.get_file_path_by_id(db, child_file, user_id)
                                    # 1. 경로 부분 변경, 2. 기존 파일 이름을 새 파일 이름으로 교체.  <-여기서 오류날 것. 여기서부터 다시 테스트.
                                child_file_new_path = child_file_original_path.replace(parent_path_of_target,target_destination_path,1).replace(child_file_original_name,child_file_new_name,1)
                                
                                child_file_new_parent_id = crud.get_directory_id_by_path(db,child_file_new_path.replace("/"+child_file_new_name,"",1), user_id)
                                
                                # s3로 복사
                                # 버킷 내 다른 위치로 파일 복사
                                s3_client.copy_object(
                                    Bucket=S3_BUCKET_NAME,
                                    CopySource={'Bucket': S3_BUCKET_NAME, 'Key': child_file_original_s3_key},
                                    Key=child_file_new_s3_key
                                )
                                # s3 에서 해당 파일 데이터를 가져오기.
                                response = s3_client.get_object(
                                    Bucket=S3_BUCKET_NAME,
                                    Key=child_file_new_s3_key
                                )
                                # 저장될 데이터를 일반화
                                id = child_file_new_id
                                name = child_file_new_name
                                path = child_file_new_path
                                parent_id = child_file_new_parent_id
                                s3_key = child_file_new_s3_key
                                file_content = response['Body'].read()  # file_content는 bytes 타입
                                # 문서 새로 저장
                                await process_document(
                                                file_name=name,
                                                file_path=path,
                                                file_content=file_content,
                                                user_id=user_id,
                                                db=db,
                                                s3_key=s3_key
                                            )                        
                                # 디렉토리 테이블에 저장할 데이터 준비
                                directory_value_dict = {
                                    "id": id,
                                    "name": name,
                                    "path": path,
                                    "is_directory": False,
                                    "parent_id": parent_id,
                                    "created_at": datetime.now().isoformat(),
                                    "operation":op_type
                                }                        
                                # 디렉토리 정보 저장
                                results.append(store_directory_table(db, directory_value_dict, user_id))
                                # 자식 파일 처리 끝.                            
                        # 데이터 처리 및 저장.
                    else:# 단일 디렉토리인 경우
                        # 데이터 준비.
                        # 목적지에 따른 데이터 준비의 방법은 다르다.
                        if  target_item_copied_path == target_destination_path:# 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우.
                            
                            # 디렉토리의 새 아이디 설정
                            new_directory_id = str(uuid.uuid4())
                            # 이름 중복 확인하고 새로운 이름 생성.
                            target_item_new_name = generate_unique_directory_name(db, target_item_original_name, user_id)
                            # 디렉토리의 새 경로 설정
                            # 아이템의 기존 경로에서 기존 이름을 새 이름으로 replace.
                            target_item_new_path = target_item_original_path.replace(target_item_original_name, target_item_new_name)
                            # 디렉토리의 새 부모 설정
                            if target_destination_path == "/": # -> issue 97 comment check
                                target_item_new_parent_id = "root"
                            else:
                                target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)

                            # 저장될 데이터를 일반화
                            id = new_directory_id
                            name = target_item_new_name
                            path = target_item_new_path
                            parent_id = target_item_new_parent_id
                        elif (target_item_copied_path != '/') and (target_destination_path == "/"):# 목적지가 루트인 경우

                            # 디렉토리의 새 아이디 설정
                            new_directory_id = str(uuid.uuid4())
                            # 디렉토리 새 이름 설정
                                # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
                            # 디렉토리의 새 경로 설정
                            target_item_new_path = target_destination_path + target_item_original_name
                            # 디렉토리의 새 부모 설정
                            target_item_new_parent_id = "root"
                            
                            # 저장될 데이터를 일반화
                            id = new_directory_id
                            name = target_item_original_name
                            path = target_item_new_path
                            parent_id = target_item_new_parent_id
                        else:# 목적지가 루트가 아닌 경우

                            # 디렉토리의 새 아이디 설정
                            new_directory_id = str(uuid.uuid4())
                            # 디렉토리 새 이름 설정
                                # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
                            # 디렉토리의 새 경로 설정
                            target_item_new_path = target_destination_path + "/" + target_item_original_name
                            # 디렉토리의 새 부모 설정
                            target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)

                            # 저장될 데이터를 일반화
                            id = new_directory_id
                            name = target_item_original_name
                            path = target_item_new_path
                            parent_id = target_item_new_parent_id                            
                            
                        # 준비된 데이터를 저장.
                        # 새로운 디렉토리 추가
                        # 디렉토리 테이블에 저장할 데이터 준비
                        directory_value_dict = {
                            "id": id,
                            "name": name,
                            "path": path,
                            "is_directory": item_is_directory,
                            "parent_id": parent_id,
                            "created_at": datetime.now().isoformat(),
                            "operation":op_type
                        }                    
                        # 저장 및 저장된 결과를 리턴.
                        results.append(store_directory_table(db, directory_value_dict, user_id))
                else:# 파일인 경우
                    
                    # 파일 처리 함수.

                    # 데이터 준비.
                    # 공통인 부분
                    # 파일의 새 이름 설정
                        # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
                    target_item_new_name = generate_unique_filename(db, target_item_original_name, user_id)
                    # 파일의 새 s3_key 설정
                        # 기존 s3_key
                    target_item_original_s3_key = crud.get_s3_key_by_id(db, target_item_id, user_id)
                        # 새 s3_key
                    target_item_new_s3_key = target_item_original_s3_key.replace(target_item_original_name, target_item_new_name)
                    # 파일의 새 아이디 설정
                    target_item_new_id = crud.add_documents(db,target_item_new_name,target_item_new_s3_key,datetime.now(),user_id).id

                    # 목적지에 따른 데이터 준비의 방법은 다르다.

                    if target_item_copied_path == target_destination_path: # 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우.
                        
                        # 파일의 새 경로 설정
                        target_item_new_path = target_item_original_path.replace(target_item_original_name, target_item_new_name)
                        # 파일의 새 부모 설정
                            # 기존 부모 id.
                        
                        # s3 에서 해당 파일 데이터를 가져오기.
                        response = s3_client.get_object(
                            Bucket=S3_BUCKET_NAME,
                            Key=target_item_original_s3_key
                        )
                        
                        # 저장될 데이터를 일반화
                        id = target_item_new_id
                        name = target_item_new_name
                        path = target_item_new_path
                        parent_id = file_parent_id
                        s3_key = target_item_new_s3_key
                        file_content = response['Body'].read()  # file_content는 bytes 타입                             
                    elif (target_item_copied_path != '/') and (target_destination_path == "/"):# 목적지가 루트인 경우
                        

                        # 파일의 새 경로 설정
                        target_item_new_path = target_destination_path + target_item_new_name
                        # 파일의 새 부모 설정
                        target_item_new_parent_id = "root"

                        # s3 에서 해당 파일 데이터를 가져오기.
                        response = s3_client.get_object(
                            Bucket=S3_BUCKET_NAME,
                            Key=target_item_original_s3_key
                        )
                        
                        # 저장될 데이터를 일반화
                        id = target_item_new_id
                        name = target_item_new_name
                        path = target_item_new_path
                        parent_id = target_item_new_parent_id
                        s3_key = target_item_new_s3_key
                        file_content = response['Body'].read()  # file_content는 bytes 타입

                    else: # 목적지가 루트가 아닌 경우
                        
                        # 파일의 새 경로 설정
                        target_item_new_path = target_destination_path + "/" + target_item_new_name
                        # 파일의 새 부모 설정
                        target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)
                        # s3 에서 해당 파일 데이터를 가져오기.
                        response = s3_client.get_object(
                            Bucket=S3_BUCKET_NAME,
                            Key=target_item_original_s3_key
                        )

                        # 저장될 데이터를 일반화
                        id = target_item_new_id
                        name = target_item_new_name
                        path = target_item_new_path
                        parent_id = target_item_new_parent_id
                        s3_key = target_item_new_s3_key
                        file_content = response['Body'].read()  # file_content는 bytes 타입                           

                    # s3로 복사
                    # 버킷 내 다른 위치로 파일 복사
                    s3_client.copy_object(
                        Bucket=S3_BUCKET_NAME,
                        CopySource={'Bucket': S3_BUCKET_NAME, 'Key': target_item_original_s3_key},
                        Key=s3_key
                    )                        
                    # 문서 새로 저장
                    await process_document(
                                    file_name=name,
                                    file_path=path,
                                    file_content=file_content,
                                    user_id=user_id,
                                    db=db,
                                    s3_key=s3_key
                                )                        
                    # 디렉토리 테이블에 저장할 데이터 준비
                    directory_value_dict = {
                        "id": id,
                        "name": name,
                        "path": path,
                        "is_directory": item_is_directory,
                        "parent_id": parent_id,
                        "created_at": datetime.now().isoformat(),
                        "operation":op_type
                    }                        
                    # 디렉토리 정보 저장
                    results.append(store_directory_table(db, directory_value_dict, user_id))
            else:
                results.append({
                    "operation": op_type,
                    "status": "error",
                    "error": f"Unknown operation type: {op_type}"
                })
        
        except Exception as e:
            results.append({
                "operation": op_type,
                "status": "error",
                "error": str(e)
            })
    
    return results


def get_file_type(filename):
    """파일 타입 유추"""
    if not filename:
        return "blank"
    
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    
    if ext in ["pdf"]:
        return "document"
    elif ext in ["docx", "doc", "hwp", "hwpx"]:
        return "document"
    elif ext in ["xlsx", "xls", "csv"]:
        return "spreadsheet"
    elif ext in ["jpg", "jpeg", "png", "gif"]:
        return "image"
    elif ext in ["txt"]:
        return "blank"
    elif not ext:
        return "folder"
    
    return "blank"


def generate_unique_filename(db: Session, file_name: str, user_id: int) -> str:
    """
    DB에 없는 고유한 파일명을 돌려줍니다.

    Parameters
    ----------
    db : Session
        SQLAlchemy 세션
    file_name : str
        저장하려는 원본 파일 이름 (예: 'report.pdf', 'report(2).pdf')

    Returns
    -------
    str
        DB에 존재하지 않는 새 파일 이름
    """
    import re
    from db import crud
    # 기본 이름·확장자·초기 번호 추출
    m = re.match(r'^(.*?)(?:\((\d+)\))?(\.[^.]+)$', file_name)
    if not m:
        return file_name  # 매칭이 안 되면 파일명을 그대로 반환
    base, num_str, ext = m.group(1), m.group(2), m.group(3)
    next_n = int(num_str) + 1 if num_str else 1

    candidate = file_name  # 우선 그대로 시도
    while crud.get_file_info_by_filename(db, candidate, user_id):
        candidate = f"{base}({next_n}){ext}"
        next_n += 1

    return candidate


def generate_unique_directory_name(db: Session, directory_name: str, user_id: int) -> str:
    """
    DB에 없는 고유한 디렉토리명을 돌려줍니다.

    Parameters
    ----------
    db : Session
        SQLAlchemy 세션
    directory_name : str
        저장하려는 원본 디렉토리 이름 (예: 'report', 'report(2)')
    user_id : int
        현재 사용자 ID

    Returns
    -------
    str
        DB에 존재하지 않는 새 디렉토리 이름
    """
    import re
    from db import crud

    print(dir(crud))

    # 디렉토리명에서 (숫자) 패턴 추출 (확장자 없음)
    m = re.match(r'^(.*?)(?:\((\d+)\))?$', directory_name)
    if not m:
        return directory_name  # 매칭이 안 되면 파일명을 그대로 반환
    if m:
        base, num_str = m.group(1), m.group(2)
        next_n = int(num_str) + 1 if num_str else 1
    else:
        base = directory_name
        next_n = 1

    candidate = directory_name
    while crud.get_directory_info_by_name(db, candidate, user_id):
        candidate = f"{base}({next_n})"
        next_n += 1

    return candidate

# 최상위 디렉토리 처리
def process_top_directory(tree, current_upload_path: str, db: Session, user_id: int):
    """최상위 디렉토리 처리"""
    # 라이브러리 임포트
    import json
    from typing import Dict, Any
    from db import crud
    
    top_dir_name, top_dir_children = next(iter(tree.items()))
    top_dir_id = str(uuid.uuid4())

    # 현재 사용자가 업로드 하는 경로가 루트인 경우
    if current_upload_path == '/':
        top_dir_path = current_upload_path+top_dir_name
    else:
        # 현재 사용자가 업로드 하는 경로가 루트가 아닌 특정한 경로인 경우
        top_dir_path = current_upload_path+"/"+top_dir_name
    
    # 업로드 위치가 루트인 경우 분기 처리 -> issue 97 comment check
    if current_upload_path == "/":
        parent_id = "root"
    else:
        parent_id = crud.get_directory_id_by_path(db, current_upload_path, user_id)

    if parent_id is None:
        # 루트 위치에 업로드 하는 경우
        # 부모 아이디를 "root"로 설정.
        parent_id = "root"


    top_dir_results = {
        "id": top_dir_id,
        "name": top_dir_name,
        "path": top_dir_path,
        "is_directory": True,
        "parent_id": parent_id,
        "created_at": datetime.now().isoformat()
    }
    
    
    return top_dir_results, top_dir_children


def store_directory_table(db: Session, value_dict: dict, user_id: int):
    """디렉토리 테이블에 디렉토리 정보를 저장"""
    """모든 오퍼레이션 처리 후 디렉토리에 저장하는 기능을 여기서 처리하도록 수정하기."""
    from db import crud

    # value_dict에 operation이 존재하는 경우
    if 'operation' in value_dict:
        operation_value = value_dict["operation"]
    else:
        operation_value = None

    # 디렉토리 / 파일 구분
    if value_dict["is_directory"] == True:
        type = "directory"
    else:
        type = "file"

    try:
        if operation_value == None:
            # 일반적인 디렉토리, 파일 정보 저장인 경우
            crud.create_directory(
                db=db,
                id=value_dict["id"],
                name=value_dict["name"],
                path=value_dict["path"],
                is_directory=value_dict["is_directory"],
                parent_id=value_dict["parent_id"],
                created_at=value_dict["created_at"],
                owner_id=user_id
            )
            return {
                "type": type,
                "id": value_dict["id"],
                "name": value_dict["name"],
                "path": value_dict["path"],
                "status": "success"
            }
        else:
            # operation인 경우.
            if operation_value == "create":
                crud.create_directory(
                    db=db,
                    id=value_dict["id"],
                    name=value_dict["name"],
                    path=value_dict["path"],
                    is_directory=value_dict["is_directory"],
                    parent_id=value_dict["parent_id"],
                    created_at=value_dict["created_at"],
                    owner_id=user_id
                )
                return {
                    "operation": operation_value,
                    "type": type,
                    "id": value_dict["id"],
                    "name": value_dict["name"],
                    "path": value_dict["path"],
                    "status": "success"
                }
            elif operation_value == "move":
                crud.create_directory(
                    db=db,
                    id=value_dict["id"],
                    name=value_dict["name"],
                    path=value_dict["path"],
                    is_directory=value_dict["is_directory"],
                    parent_id=value_dict["parent_id"],
                    created_at=value_dict["created_at"],
                    owner_id=user_id
                )
                return {
                    "operation": operation_value,
                    "type": type,
                    "id": value_dict["id"],
                    "name": value_dict["name"],
                    "path": value_dict["path"],
                    "status": "success"
                }
            elif operation_value == "delete":
                crud.create_directory(
                    db=db,
                    id=value_dict["id"],
                    name=value_dict["name"],
                    path=value_dict["path"],
                    is_directory=value_dict["is_directory"],
                    parent_id=value_dict["parent_id"],
                    created_at=value_dict["created_at"],
                    owner_id=user_id
                )
                return {
                    "operation": operation_value,
                    "type": type,
                    "id": value_dict["id"],
                    "name": value_dict["name"],
                    "path": value_dict["path"],
                    "status": "success"
                }
            elif operation_value == "rename":
                crud.create_directory(
                    db=db,
                    id=value_dict["id"],
                    name=value_dict["name"],
                    path=value_dict["path"],
                    is_directory=value_dict["is_directory"],
                    parent_id=value_dict["parent_id"],
                    created_at=value_dict["created_at"],
                    owner_id=user_id
                )
                return {
                    "operation": operation_value,
                    "type": type,
                    "id": value_dict["id"],
                    "name": value_dict["name"],
                    "path": value_dict["path"],
                    "status": "success"
                }
            elif operation_value == "copy":
                crud.create_directory(
                    db=db,
                    id=value_dict["id"],
                    name=value_dict["name"],
                    path=value_dict["path"],
                    is_directory=value_dict["is_directory"],
                    parent_id=value_dict["parent_id"],
                    created_at=value_dict["created_at"],
                    owner_id=user_id
                )
                return {
                    "operation": operation_value,
                    "type": type,
                    "id": value_dict["id"],
                    "name": value_dict["name"],
                    "path": value_dict["path"],
                    "status": "success"
                }

    except Exception as e:
        return {
            "type": type,
            "id": value_dict["id"],
            "name": value_dict["name"],
            "path": value_dict["path"],
            "status": "error",
            "error": str(e)
        }
    
def set_filename(upload_file: any, db: Session, user_id: int):
    """파일 이름 추출
    

    """
    from db import crud
    if os.path.dirname(upload_file.filename) == "":
        # 단일파일인 경우
        file_name = upload_file.filename
    else:
        # 단일파일이 아닌 경우 (경로 데이터가 파일명에 포함되어 있는 경우)
        file_name = os.path.basename(upload_file.filename)
    
    # 동일한 파일이 db에 존재하는 지 확인
    # get_file_info_by_filename()함수는 현재 접속 중인 사용자의 db에서만 검색해야 하는데, 이 함수는 전체 db에서 검색하는 것이므로 수정해야 한다.
    file_info = crud.get_file_info_by_filename(db, file_name, user_id)
    if file_info:
        # 중복 파일명 처리
        file_name = generate_unique_filename(db, file_name, user_id)
    return file_name

# 필요 없는 함수가 맞으면 바로 삭제.
# def set_s3_key(db: Session, file_name: str, current_user: User):
#     """s3 key 생성"""
#     from db import crud
#     # 파일 이름으로 documents 테이블에 파일이 존재하는 지 확인
#     file_info = crud.get_file_info_by_filename(db, file_name)
#     if file_info:
#         # 중복 파일명 처리
#         file_name = generate_unique_filename(db, file_name)
#     else:
#         # 기존 파일명으로 s3 key 설정.
#         s3_key = f"uploads/{current_user.username}/{file_name}"
#     return s3_key


def set_file_path(file_name: str, upload_file: any, current_upload_path: str):
    """파일 경로 설정"""

    if os.path.dirname(upload_file.filename) == "":
        # 단일파일인 경우
        if current_upload_path == '/':
            # 루트 위치 업로드인 경우
            file_path_full = current_upload_path+file_name
            file_path_dir = current_upload_path
        else:
            # 특정 위치 업로드인 경우
            file_path_full = current_upload_path+"/"+file_name
            file_path_dir = current_upload_path
    else:
        # 단일파일이 아닌 경우 (경로 데이터가 파일명에 포함되어 있는 경우)
        # 파일 이름과 경로 이름 분리
        dir_name =os.path.dirname(upload_file.filename)
        
        if current_upload_path == '/':
            # 루트 위치 업로드인 경우
            file_path_full = "/" +dir_name+"/"+file_name
            file_path_dir = "/" +dir_name
        else:
            # 특정 위치 업로드인 경우
            file_path_full = current_upload_path+"/"+dir_name+"/"+file_name
            file_path_dir = current_upload_path+"/"+dir_name

    return (file_path_full, file_path_dir)


async def upload_file_to_s3(upload_file: any, s3_key: str, file_name: str, file_path: str, content_type: any = None):
    """s3 업로드"""
    try:
        if hasattr(upload_file, "file"):
            # .file 속성이 존재하는 경우
            # 파일 업로드 전에 파일 포인터를 초기화
            upload_file.file.seek(0)
            # 파일 업로드
            s3_client.upload_fileobj(
                Fileobj=upload_file.file,
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                ExtraArgs={'ContentType': upload_file.content_type}
            )
        else:
            # .file 속성이 존재하지 않는 경우
            upload_file.seek(0)
            # 파일 업로드
            s3_client.upload_fileobj(
                Fileobj=upload_file,
                Bucket=S3_BUCKET_NAME,
                Key=s3_key,
                ExtraArgs={'ContentType': content_type}
            )



        return {
            "type": "file",
            "id": None,
            "name": file_name,
            "path": file_path,
            "status": "success"
        }
    except Exception as e:
        return {
            "type": "file",
            "id": None,
            "name": file_name,
            "path": file_path,
            "status": "error",
            "error": str(e)
        }


def delete_directory(db: Session, reserved_item_id: str, item_name: str, item_path: str, user_id: int):
    """디렉토리 삭제"""
    from db import crud
    # 디렉토리 삭제
    crud.delete_directory_by_id(db, reserved_item_id, user_id)

    return {
        "operation": "delete",
        "type": "directory",
        "id": reserved_item_id,
        "name": item_name,
        "path": item_path,
        "status": "success"
    }

def delete_file(db: Session, reserved_item_id: str, item_name: str, item_path: str, s3_client: boto3.client, user_id: int):
    """파일 삭제"""
    from db import crud
    # 파일 삭제
     # 삭제를 위해 s3_key값을 검색해서 가져오기
    s3_key = crud.get_s3_key_by_id(db, int(reserved_item_id), user_id)
    # s3에서 삭제
    s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
    # documents 테이블에서 해당 id의 데이터를 삭제하면서 document_chunks, directories 테이블에서 데이터 삭제.
    crud.delete_document_by_id(db, reserved_item_id, user_id)

    return {
        "operation": "delete",
        "type": "file",
        "id": reserved_item_id,
        "name": item_name,
        "path": item_path,
        "status": "success" 
    }

async def rename_and_reupload_document(db: Session, user_id: int, reserved_item_id: str, target_item_original_name: str, target_item_original_path: str, reserved_item_new_name: str, target_item_parent_id: str, s3_client: boto3.client, op_type: any=None):
    """문서 이름 변경 및 재업로드.
    주요 매개변수: 
    reserved_item_id : 아이템의 id.
    target_item_original_name : 아이템의 기존 이름.
    target_item_original_path : 아이템의 기존 경로.
    reserved_item_new_name : 아이템의 새로운 이름.
    target_item_parent_id : 아이템의 부모 아이디.

    함수의 작업:
    1. 기존 아이템의 s3_key 가져오기
    2. s3에서 해당 파일 데이터 가져오기
    3. 기존 파일을 삭제
    4. 새로운 이름과 경로, s3_key로 s3에 새 파일을 업로드
    5. 새로운 정보로 문서 저장 (document_chunks, documents 테이블에 저장)
    6. 새로운 정보를 디렉토리 테이블에 저장
    
    """
    from db import crud
    import io
    results = []
    # 기존 아이템의 s3_key 가져오기
    target_item_original_s3_key = crud.get_s3_key_by_id(db, reserved_item_id, user_id)                    
    # s3에서 해당 파일 데이터 가져오기
    response = s3_client.get_object(
        Bucket=S3_BUCKET_NAME,
        Key=target_item_original_s3_key
    )

    # response에서 컨텐츠 타입, 파일 데이터 추출
    # 컨텐츠 타입 추출
    content_type = response.get("ContentType")
    # ext = content_type.split("/")[-1]  # 결과: 'pdf', 'docx', 'hwp', 'hwpx' 등등
    # 파일 데이터를 바이트 타입으로 가져오기
    data = response['Body'].read()  # data는 bytes 타입
    file_content = data
    # s3 업로드를 위해 파일 객체로 포장.
    file_obj = io.BytesIO(data)

    # 기존 파일을 삭제
    results.append(delete_file(db, reserved_item_id, target_item_original_name, target_item_original_path, s3_client, user_id))

    # 새로운 이름과 경로, s3_key로 s3에 새 파일을 업로드
    # 새 s3_key 생성
    # 이름 중복 확인
    target_item_new_name = generate_unique_filename(db, reserved_item_new_name, user_id)
    # 새 s3_key 생성
    target_item_new_s3_key = target_item_original_s3_key.replace(target_item_original_name, target_item_new_name)
    # 아이템의 새 주소 설정
    target_item_new_path = target_item_original_path.replace(target_item_original_name, target_item_new_name)
    # 새 파일을 s3로 업로드
    results.append(await upload_file_to_s3(file_obj, target_item_new_s3_key, reserved_item_new_name, target_item_new_path, content_type))

    # 새로운 정보로 문서 저장
    # 문서 저장
    document_id = await process_document(
                file_name=target_item_new_name,
                file_path=target_item_new_path,
                file_content=file_content,
                user_id=user_id,
                db=db,
                s3_key=target_item_new_s3_key
            )
                    
    # 새로운 정보를 디렉토리 테이블에 저장
    # 디렉토리 테이블에 저장할 데이터 준비
    directory_value_dict = {
        "id": document_id,
        "name": target_item_new_name,
        "path": target_item_new_path,
        "is_directory": False,
        "parent_id": target_item_parent_id,
        "created_at": datetime.now().isoformat(),
        "operation":op_type
    }
    # 디렉토리 테이블에 저장
    results.append(store_directory_table(db, directory_value_dict, user_id))
    return results

# edit_path_and_reupload_document
async def edit_path_and_reupload_document(db: Session, user_id: int, file_id: str, file_original_name: str, target_item_original_name: str, reserved_item_new_name: str, file_original_path: str, file_parent_id: str, s3_client: boto3.client, op_type: any=None):
    """문서의 경로를 변경 및 재업로드"""
    from db import crud
    import io
    results = []
    # 기존 아이템의 s3_key 가져오기
    target_item_original_s3_key = crud.get_s3_key_by_id(db, file_id, user_id)                    
    # s3에서 해당 파일 데이터 가져오기
    response = s3_client.get_object(
        Bucket=S3_BUCKET_NAME,
        Key=target_item_original_s3_key
    )
    # 컨텐츠 타입 추출
    content_type = response.get("ContentType")
    ext = content_type.split("/")[-1]  # 결과: 'pdf', 'docx', 'hwp', 'hwpx' 등등
    # 파일 데이터를 바이트 타입으로 가져오기
    data = response['Body'].read()  # data는 bytes 타입
    file_content = data
    # s3 업로드를 위해 파일 객체로 포장.
    file_obj = io.BytesIO(data)

    # 기존 파일을 삭제
    results.append(delete_file(db, file_id, file_original_name, file_original_path, s3_client, user_id))

    # 새로운 경로를 주고 s3에 새 파일을 업로드
    # 기존에 쓰던 s3_key 사용 : target_item_original_s3_key
    # 파일의 새 주소 설정
    target_item_new_path = file_original_path.replace(target_item_original_name, reserved_item_new_name)
    # 새 파일을 s3로 업로드
    results.append(await upload_file_to_s3(file_obj, target_item_original_s3_key, file_original_name, target_item_new_path, content_type))

    # 새로운 정보로 문서 저장
    # 문서 저장
    document_id = await process_document(
                file_name=file_original_name,
                file_path=target_item_new_path,
                file_content=file_content,
                user_id=user_id,
                db=db,
                s3_key=target_item_original_s3_key
            )
                    
    # 새로운 정보를 디렉토리 테이블에 저장
    # 디렉토리 테이블에 저장할 데이터 준비
    directory_value_dict = {
        "id": document_id,
        "name": file_original_name,
        "path": target_item_new_path,
        "is_directory": False,
        "parent_id": file_parent_id,
        "created_at": datetime.now().isoformat(),
        "operation":op_type
    }
    # 디렉토리 테이블에 저장
    results.append(store_directory_table(db, directory_value_dict, user_id))
    return results


async def copy_file(db: Session, target_item_id: str, target_destination_path: str, user_id: int, op_type: str):
    """단일 파일 복사 프로세스를 함수로 만듦."""
    from db import crud
    # 아이템의 디렉토리 여부 가져오기.
    item_is_directory = crud.get_file_is_directory_by_id(db, target_item_id, user_id)
    # target item의 기존 경로 가져오기
    target_item_original_path = crud.get_file_path_by_id(db, target_item_id, user_id)
    # 아이템의 기존 이름 가져오기
    target_item_original_name = crud.get_file_name_by_id(db, target_item_id, user_id)
    # 파일이 저장되어 있는 디렉토리 id 가져오기
    file_parent_id = crud.get_parent_id_by_id(db, target_item_id, user_id)
    # 아이템에게 자식이 있는지 여부
    item_has_children = crud.get_directory_by_parent_id(db, target_item_id, user_id)
    
    # 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우의 판단을 위해 target_item_original_path에서 파일 이름만 제거.
    target_item_original_path_without_name = target_item_original_path.replace(target_item_original_name, "").rstrip("/")
    # 데이터 준비.
    # 공통인 부분
    # 파일의 새 이름 설정
        # 파일을 새로 생성해야 하므로 이름을 새로 짓는다.
    target_item_new_name = generate_unique_filename(db, target_item_original_name, user_id)
    # 파일의 새 s3_key 설정
        # 기존 s3_key
    target_item_original_s3_key = crud.get_s3_key_by_id(db, target_item_id, user_id)
        # 새 s3_key
    target_item_new_s3_key = target_item_original_s3_key.replace(target_item_original_name, target_item_new_name)
    # 파일의 새 아이디 설정
    target_item_new_id = crud.add_documents(db,target_item_new_name,target_item_new_s3_key,datetime.now(),user_id).id                    

    # 목적지에 따른 데이터 준비의 방법은 다르다.
    if target_destination_path == "/":# 목적지가 루트인 경우
        
        # 파일의 새 경로 설정
        target_item_new_path = target_destination_path + target_item_new_name
        # 파일의 새 부모 설정
        target_item_new_parent_id = "root"
        # s3 에서 해당 파일 데이터를 가져오기.
        response = s3_client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=target_item_original_s3_key
        )
        
        # 저장될 데이터를 일반화
        id = target_item_new_id
        name = target_item_new_name
        path = target_item_new_path
        parent_id = target_item_new_parent_id
        s3_key = target_item_new_s3_key
        file_content = response['Body'].read()  # file_content는 bytes 타입
        
    elif target_item_original_path_without_name == target_destination_path: # 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우.
                        
        # 파일의 새 경로 설정
        target_item_new_path = target_item_original_path.replace(target_item_original_name, target_item_new_name)
        # 파일의 새 부모 설정
            # 기존 부모 id.
                        
        # s3 에서 해당 파일 데이터를 가져오기.
        response = s3_client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=target_item_original_s3_key
        )
                        
        # 저장될 데이터를 일반화
        id = target_item_new_id
        name = target_item_new_name
        path = target_item_new_path
        parent_id = file_parent_id
        s3_key = target_item_new_s3_key
        file_content = response['Body'].read()  # file_content는 bytes 타입                             

    else: # 목적지가 루트가 아닌 경우
                        
        # 파일의 새 경로 설정
        target_item_new_path = target_destination_path + "/" + target_item_new_name
        # 파일의 새 부모 설정
        target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)
        # s3 에서 해당 파일 데이터를 가져오기.
        response = s3_client.get_object(
            Bucket=S3_BUCKET_NAME,
            Key=target_item_original_s3_key
        )
        
        # 저장될 데이터를 일반화
        id = target_item_new_id
        name = target_item_new_name
        path = target_item_new_path
        parent_id = target_item_new_parent_id
        s3_key = target_item_new_s3_key
        file_content = response['Body'].read()  # file_content는 bytes 타입                           

    # s3로 복사
    # 버킷 내 다른 위치로 파일 복사
    s3_client.copy_object(
        Bucket=S3_BUCKET_NAME,
        CopySource={'Bucket': S3_BUCKET_NAME, 'Key': target_item_original_s3_key},
        Key=s3_key
    )                        
    # 문서 새로 저장
    await process_document(
                    file_name=name,
                    file_path=path,
                    file_content=file_content,
                    user_id=user_id,
                    db=db,
                    s3_key=s3_key
                )                        
    # 디렉토리 테이블에 저장할 데이터 준비
    directory_value_dict = {
        "id": id,
        "name": name,
        "path": path,
        "is_directory": item_is_directory,
        "parent_id": parent_id,
        "created_at": datetime.now().isoformat(),
        "operation":op_type
    }                        
    # 디렉토리 정보 저장
    return store_directory_table(db, directory_value_dict, user_id)

def copy_directory(db: Session, target_item_id: str, target_destination_path: str, user_id: int, op_type: str):
    """단일 디렉토리 복사 프로세스를 함수로 만듦."""
    from db import crud

    # 아이템의 디렉토리 여부 가져오기.
    item_is_directory = crud.get_file_is_directory_by_id(db, target_item_id, user_id)
    # target item의 기존 경로 가져오기
    target_item_original_path = crud.get_file_path_by_id(db, target_item_id, user_id)
    # 아이템의 기존 이름 가져오기
    target_item_original_name = crud.get_file_name_by_id(db, target_item_id, user_id)

    # 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우의 판단을 위해 target_item_original_path에서 파일 이름만 제거.
    target_item_original_path_without_name = target_item_original_path.replace(target_item_original_name, "").rstrip("/")

    # 데이터 준비.
    # 목적지에 따른 데이터 준비의 방법은 다르다.
    if target_destination_path == "/":# 목적지가 루트인 경우

        # 디렉토리의 새 아이디 설정
        new_directory_id = str(uuid.uuid4())
        # 디렉토리 새 이름 설정
            # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
        # 디렉토리의 새 경로 설정
        target_item_new_path = target_destination_path + target_item_original_name
        # 디렉토리의 새 부모 설정
        target_item_new_parent_id = "root"
                            
        # 저장될 데이터를 일반화
        id = new_directory_id
        name = target_item_original_name
        path = target_item_new_path
        parent_id = target_item_new_parent_id
    elif  target_item_original_path_without_name == target_destination_path:# 아이템을 복사한 위치와 붙여넣기 하는 위치가 동일할 경우.
                            
        # 디렉토리의 새 아이디 설정
        new_directory_id = str(uuid.uuid4())
        # 이름 중복 확인하고 새로운 이름 생성.
        target_item_new_name = generate_unique_directory_name(db, target_item_original_name, user_id)
        # 디렉토리의 새 경로 설정
        # 아이템의 기존 경로에서 기존 이름을 새 이름으로 replace.
        target_item_new_path = target_item_original_path.replace(target_item_original_name, target_item_new_name)
        # 디렉토리의 새 부모 설정
        # 목적지가 루트인 경우 분기 -> issue 97 comment check
        if target_destination_path == "/":
            target_item_new_parent_id = "root"
        else:
            target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)

        # 저장될 데이터를 일반화
        id = new_directory_id
        name = target_item_new_name
        path = target_item_new_path
        parent_id = target_item_new_parent_id
    else:# 목적지가 루트가 아닌 경우
        # 디렉토리의 새 아이디 설정
        new_directory_id = str(uuid.uuid4())
        # 디렉토리 새 이름 설정
            # 디렉토리 이름은 중복 처리할 필요 없으므로 기존 이름 그대로 사용.
        # 디렉토리의 새 경로 설정
        target_item_new_path = target_destination_path + "/" + target_item_original_name
        # 디렉토리의 새 부모 설정
        target_item_new_parent_id = crud.get_directory_id_by_path(db, target_destination_path, user_id)

        # 저장될 데이터를 일반화
        id = new_directory_id
        name = target_item_original_name
        path = target_item_new_path
        parent_id = target_item_new_parent_id                            
                            
    # 준비된 데이터를 저장.
    # 새로운 디렉토리 추가
    # 디렉토리 테이블에 저장할 데이터 준비
    directory_value_dict = {
        "id": id,
        "name": name,
        "path": path,
        "is_directory": item_is_directory,
        "parent_id": parent_id,
        "created_at": datetime.now().isoformat(),
        "operation":op_type
    }                    
    # 저장 및 저장된 결과를 리턴.
    return store_directory_table(db, directory_value_dict, user_id)



