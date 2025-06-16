from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from typing import Optional

import logging
from datetime import datetime, timedelta

# 메서드 import
from debug import debugging
from db.database import get_db
from db.models import User
from fast_api.security import get_current_user
from llm import invoke
from fast_api.endpoints import op_schemas
from services.operation_store import get_operation_store, OperationStore

import uuid
import re

# LLM 관련 import 추가
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough


router = APIRouter()

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@router.post("/stage", response_model=op_schemas.StageOperationResponse)
async def stage_operation(
    request: op_schemas.StageOperationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    operation_store: OperationStore = Depends(get_operation_store),
    accept_language: Optional[str] = Header(default=None, alias="Accept-Language")
):
    """
    자연어 명령을 분석하고 작업을 준비하는 엔드포인트
    
    Args:
        request: 사용자 명령과 컨텍스트 정보
        current_user: 현재 인증된 사용자
        db: 데이터베이스 세션
        operation_store: Redis 작업 저장소
        accept_language: Accept-Language header

    
    Returns:
        OperationResponse: 준비된 작업 정보
    """
    try:
        user_id = current_user.id
        logger.info(f"Stage operation request from user {user_id}: {request.command}")
        # Extract language from Accept-Language header (default to 'ko')
        language = accept_language.split(',')[0].strip().lower() if accept_language else 'ko'
        logger.debug(f"🈯 Detected language from header: {language}")

        # TODO: Pass `language` to downstream logic (LLM prompt, i18n, etc.) as needed.

        # command 값 접근
        command = request.command

        # context 값 접근
        context = request.context

        # context의 하위 아이템 접근
        current_path = context.currentPath
        selected_files = context.selectedFiles
        available_folders = context.availableFolders
        timestamp = context.timestamp

        # 타입을 결정.
        operation_type = invoke.get_operation_type(command)

        # 타입 별 AI 호출 분기. 각 함수의 매개변수로 command, context전달.
        if operation_type == "move":
            result = process_move(command, context, language)
        elif operation_type == "copy":
            result = process_copy(command, context, language)
        elif operation_type == "delete":
            result = process_delete(command, context, language)
        elif operation_type == "rename":
            result = process_rename(command, context, language)
        elif operation_type == "create_folder":
            result = process_create_folder(command, context, language)
        elif operation_type == "search":
            result = process_search(command, language)
        elif operation_type == "summarize":
            result = process_summarize(command, context, language)
        elif operation_type == "error":
            result = process_error(command, operation_type, language)

        # ✅ Redis에 작업 정보 저장 (error 타입 제외)
        if operation_type != "error":
            # Pydantic 모델을 사용하여 데이터 구조 검증
            operation_store_data = op_schemas.OperationStoreData(
                operation_id=result.operationId,
                command=command,
                context={
                    "currentPath": current_path,
                    "selectedFiles": [dict(file) for file in selected_files],
                    "availableFolders": [dict(folder) for folder in available_folders],
                    "timestamp": timestamp.isoformat() if timestamp else datetime.now().isoformat()
                },
                operation=dict(result.operation),
                requiresConfirmation=result.requiresConfirmation,
                riskLevel=result.riskLevel,
                preview=dict(result.preview),
                user_id=user_id,
                created_at=datetime.now().isoformat()
            )
            
            # 딕셔너리로 변환하여 Redis에 저장
            operation_data = operation_store_data.dict()
            
            # Redis에 저장
            if not operation_store.store_operation(result.operationId, operation_data):
                logger.error(f"Failed to store operation {result.operationId} in Redis")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="작업 정보 저장에 실패했습니다"
                )
            
            logger.info(f"Operation {result.operationId} stored successfully in Redis")

        # 결과 반환
        return result
        
    except HTTPException:
        # HTTPException은 그대로 다시 발생
        raise
    except Exception as e:
        logger.error(f"Unexpected error in stage_operation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="작업 준비 중 오류가 발생했습니다"
        )

@router.post("/{operation_id}/execute", response_model=op_schemas.ExecutionResponse)
async def execute_operation(
    operation_id: str,
    request: op_schemas.ExecuteOperationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    operation_store: OperationStore = Depends(get_operation_store)
):
    """
    준비된 작업을 실행하는 엔드포인트
    
    Args:
        operation_id: 실행할 작업의 ID
        request: 사용자 확인 및 옵션
        current_user: 현재 인증된 사용자
        db: 데이터베이스 세션
        operation_store: Redis 작업 저장소
    
    Returns:
        ExecutionResponse: 실행 결과 정보
    """
    user_id = current_user.id
    logger.info(f"Execute operation {operation_id} for user {user_id}")
    # debugging.stop_debugger()
    
    try:
        # Redis에서 작업 정보 조회
        operation_data = operation_store.get_operation(operation_id)
        
        if not operation_data:
            logger.warning(f"Operation not found: {operation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="작업을 찾을 수 없거나 만료되었습니다"
            )
        
        # 사용자 권한 확인
        if operation_data.get("user_id") != user_id:
            logger.warning(f"Unauthorized access attempt for operation {operation_id} by user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업에 대한 권한이 없습니다"
            )
        
        # 작업 타입 확인
        operation = operation_data.get("operation", {})
        operation_type = operation.get("type")
        
        logger.info(f"Executing {operation_type} operation: {operation_id}")
        
        # 작업 타입별 실제 실행 로직
        execution_result = await execute_operation_logic(operation_type, operation, request.userOptions, current_user, db)
        
        # ~~실행 완료 후 Redis에서 작업 정보 삭제~~ 실행 후 삭제하면 안 됨.
        # operation_store.delete_operation(operation_id)
        
        # 성공 응답 생성
        response = op_schemas.ExecutionResponse(
            message=execution_result.get("message", "작업이 성공적으로 완료되었습니다"),
            undoAvailable=execution_result.get("undoAvailable", False),
            undoDeadline=execution_result.get("undoDeadline"),
            results=execution_result.get("results"),
            searchResults=execution_result.get("searchResults"),
            summaries=execution_result.get("summaries")
        )
        
        logger.info(f"Operation {operation_id} executed successfully")
        # 저장소 테스트
        # debugging.redis_store_test(operation_id)

        return response
        
    except HTTPException:
        # HTTPException은 그대로 다시 발생
        raise
    except Exception as e:
        logger.error(f"Unexpected error executing operation {operation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="작업 실행 중 오류가 발생했습니다"
        )

@router.post("/{operation_id}/cancel", response_model=op_schemas.BasicResponse)
async def cancel_operation(
    operation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    operation_store: OperationStore = Depends(get_operation_store)
):
    """
    준비된 작업을 취소하는 엔드포인트
    
    Args:
        operation_id: 취소할 작업의 ID
        current_user: 현재 인증된 사용자
        db: 데이터베이스 세션
        operation_store: Redis 작업 저장소
    
    Returns:
        BasicResponse: 취소 결과 메시지
    """
    user_id = current_user.id
    logger.info(f"Cancel operation {operation_id} for user {user_id}")
    
    try:
        # Redis에서 작업 정보 조회
        operation_data = operation_store.get_operation(operation_id)
        
        if not operation_data:
            logger.warning(f"Operation not found for cancellation: {operation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="취소할 작업을 찾을 수 없거나 이미 만료되었습니다"
            )
        
        # 사용자 권한 확인
        if operation_data.get("user_id") != user_id:
            logger.warning(f"Unauthorized cancel attempt for operation {operation_id} by user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업을 취소할 권한이 없습니다"
            )
        
        # Redis에서 작업 정보 삭제
        if operation_store.delete_operation(operation_id):
            logger.info(f"Operation {operation_id} cancelled successfully")
            return op_schemas.BasicResponse(message="작업이 취소되었습니다")
        else:
            logger.warning(f"Failed to delete operation {operation_id} from Redis")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="작업 취소 중 오류가 발생했습니다"
            )
            
    except HTTPException:
        # HTTPException은 그대로 다시 발생
        raise
    except Exception as e:
        logger.error(f"Unexpected error cancelling operation {operation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="작업 취소 중 오류가 발생했습니다"
        )

@router.post("/{operation_id}/undo", response_model=op_schemas.BasicResponse)
async def undo_operation(
    operation_id: str,
    request: op_schemas.UndoOperationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    operation_store: OperationStore = Depends(get_operation_store)
):
    """
    실행된 작업을 되돌리는 엔드포인트
    
    Args:
        operation_id: 되돌릴 작업의 ID
        request: 되돌리기 사유 및 시간
        current_user: 현재 인증된 사용자
        db: 데이터베이스 세션
        operation_store: Redis 작업 저장소
    
    Returns:
        BasicResponse: 되돌리기 결과 메시지
    """
    user_id = current_user.id
    logger.info(f"Undo operation {operation_id} for user {user_id}, reason: {request.reason}")
    
    try:
        # 실행되었던 작업 정보 조회 
        undo_data = operation_store.get_operation(operation_id)
        
        # 저장소 테스트
        # debugging.redis_store_test(operation_id)
        # debugging.stop_debugger()

        if not undo_data:
            logger.warning(f"Undo data not found for operation: {operation_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="되돌릴 수 있는 작업을 찾을 수 없거나 되돌리기 기한이 만료되었습니다"
            )
        
        # 사용자 권한 확인
        if undo_data.get("user_id") != user_id:
            logger.warning(f"Unauthorized undo attempt for operation {operation_id} by user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="이 작업을 되돌릴 권한이 없습니다"
            )
        
        # 작업 타입별 undo 로직 실행
        operation_type = undo_data.get("operation", {}).get("type")
        undo_result = await execute_undo_logic(operation_type, undo_data, request.reason, current_user, db)
        
        if undo_result.get("success", False):
            # 실행되었던 작업 정보 삭제
            operation_store.delete_operation(operation_id)
            
            logger.info(f"Operation {operation_id} undone successfully")
            return op_schemas.BasicResponse(
                message=undo_result.get("message", "작업이 성공적으로 되돌려졌습니다")
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=undo_result.get("error", "작업 되돌리기에 실패했습니다")
            )
            
    except HTTPException:
        # HTTPException은 그대로 다시 발생
        raise
    except Exception as e:
        logger.error(f"Unexpected error undoing operation {operation_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="작업 되돌리기 중 오류가 발생했습니다"
        )


# operation_type별 function 만들기

def process_move(command, context, language):
    """
    이동 작업을 처리하는 함수 (LLM 기반으로 리팩토링됨)
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        language: 사용자 언어 ('ko' 또는 'en')
    Returns:
        작업 결과 정보
    """
    # LLM을 사용하여 목적지 설정
    destination = get_destination(command, context, 'move')
    # debugging.stop_debugger()
    # LLM을 사용하여 작업 설명 생성
    description = get_description(command, context, destination, 'move', language)
    # debugging.stop_debugger()

    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    
    # destination에서 create_folder 접두사 제거 (실제 작업에서는 깨끗한 경로 사용)
    clean_destination = destination
    if destination.startswith('create_folder/'):
        clean_destination = destination.replace('create_folder/', '', 1)
    
    warnings = [] 
    # debugging.stop_debugger()
    # Pydantic 모델 사용
    move_operation = op_schemas.MoveOperation(
        targets=context.selectedFiles,
        destination=clean_destination
    )
    
    preview = op_schemas.OperationPreview(
        description=description,
        warnings=warnings
    )
    
    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=move_operation,
        requiresConfirmation=True,
        riskLevel="medium",
        preview=preview
    )

def process_copy(command, context, language):
    """
    복사 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        
    Returns:
        작업 결과 정보
    """
    # 명령 분석
    destination = get_destination(command, context, 'copy')
    # 작업 설명 요약 생성.
    description = get_description(command, context, destination, 'copy', language)

    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    
    # destination에서 create_folder 접두사 제거 (실제 작업에서는 깨끗한 경로 사용)
    clean_destination = destination
    if destination.startswith('create_folder/'):
        clean_destination = destination.replace('create_folder/', '', 1)
    
    warnings = [] 
    
    # Pydantic 모델 사용
    copy_operation = op_schemas.CopyOperation(
        targets=context.selectedFiles,
        destination=clean_destination
    )
    
    preview = op_schemas.OperationPreview(
        description=description,
        warnings=warnings
    )
    
    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=copy_operation,
        requiresConfirmation=True,
        riskLevel="low",
        preview=preview
    )

def process_delete(command, context, language):
    """
    삭제 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        
    Returns:
        작업 결과 정보
    """
    # 작업 설명 요약 생성.
    description = get_description(command, context, None, 'delete', language)

    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    warnings = [] 
    
    # Pydantic 모델 사용
    delete_operation = op_schemas.DeleteOperation(
        targets=context.selectedFiles
    )
    
    preview = op_schemas.OperationPreview(
        description=description,
        warnings=warnings
    )
    
    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=delete_operation,
        requiresConfirmation=True,
        riskLevel="high",
        preview=preview
    )

def process_error(command, operation_type, language):
    """
    오류 상황을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        error_type: 에러 타입 (err-1 또는 err-2)
        language: 사용자 언어 ('ko' 또는 'en')
        
    Returns:
        에러 정보를 담은 결과 객체
    """
    # 데이터 준비
    operation_id = "error-"+str(uuid.uuid4())
    
    # 언어별 메시지 설정
    is_english = language.startswith('en')
    
    # 에러 타입별 메시지 및 가이드 설정
    if operation_type == "error":
        # 부정표현 또는 파일관련이지만 매칭안됨
        if is_english:
            message = "The command is not related to file management or cannot be understood. Please try again."
            description = f"Unable to process the command '{command}'."
            warnings = ["Please enter an appropriate command."]
        else:
            message = "파일 관리와 관련없는 명령이거나, 명령을 이해할 수 없습니다. 다시 입력해주세요."
            description = f"입력하신 명령 '{command}'을(를) 처리할 수 없습니다."
            warnings = ["적절한 명령을 다시 입력해주십시오."]
    else:
        # 기본 에러 메시지
        if is_english:
            message = "An unknown error has occurred."
            description = "A system error has occurred."
            warnings = ["Please try again later."]
        else:
            message = "알 수 없는 오류가 발생했습니다."
            description = "시스템에서 오류가 발생했습니다."
            warnings = ["잠시 후 다시 시도해주세요."]
    
    # 로그 기록
    logger.warning(f"Error processing command: '{command}', Error type: error")
    
    # Pydantic 모델 사용
    error_operation = op_schemas.ErrorOperation(
        error_type="error",
        message=message
    )
    
    preview = op_schemas.OperationPreview(
        description=description,
        warnings=warnings
    )
    
    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operation_id,
        operation=error_operation,
        requiresConfirmation=False,
        riskLevel="none",
        preview=preview
    )

def process_rename(command, context, language):
    """
    이름 변경 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        
    Returns:
        작업 결과 정보
    """

    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    
    # 파일의 새로운 이름을 추출하는 함수는 아래의 get
    new_name = get_new_name(command, context, language)

    # description = generate_rename_description(context, new_name)
    description = get_description(command, context, None, 'rename', language, new_name)
    # debugging.stop_debugger()

    # Pydantic 모델 사용
    rename_operation = op_schemas.RenameOperation(
        target=context.selectedFiles,
        newName=new_name
    )
    
    preview = op_schemas.OperationPreview(
        description=description
    )

    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=rename_operation,
        requiresConfirmation=True,
        riskLevel="medium",
        preview=preview
    )

def process_create_folder(command, context, language):
    """
    폴더 생성 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        
    Returns:
        작업 결과 정보
    """
    # 데이터 준비
    # get_parent_path 함수 선언문과 정의문을 삭제하고 get_new_folder_name 함수의 이름을 변경-> 새로운 이름의 함수를 선언, 
    # # 그 함수의 정의부도 새롭게 정의: 기존 get_new_folder_name 작업과 get_parent_path작업을 이 새로운 함수에서 수행하도록 리팩토링.
    operationId = "op-"+str(uuid.uuid4())

    folder_name, parent_Path = get_new_folder_name_and_parent_path(command, context)
    # parent_Path = get_parent_path(command, context)

    description = generate_create_folder_description(folder_name, parent_Path, language)

    # parent_Path 파싱. 만약 'create_folder'가 존재하는 경우,
    # parent_Path에서 'create_folder' 문자열이 있으면 제거
    if parent_Path.startswith('create_folder'):
        code_remove_ver = parent_Path.replace('create_folder', '', 1)

        # parent_Path에 코드를 제거한 경로 데이터 저장.
        parent_Path = code_remove_ver

        additional_desc = folder_name + " 폴더를 생성합니다."
        # 추가 설명 문장 추가.
        description += " " + additional_desc

    # Pydantic 모델 사용
    create_folder_operation = op_schemas.CreateFolderOperation(
        folderName=folder_name,
        parentPath=parent_Path
    )
    
    preview = op_schemas.OperationPreview(
        description=description
    )

    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=create_folder_operation,
        requiresConfirmation=True,
        riskLevel="low",
        preview=preview
    )

def process_search(command, language):
    """
    검색 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        
    Returns:
        작업 결과 정보
    """
    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    search_term = get_search_term(command, language)
    description = generate_search_description(search_term, language)

    # Pydantic 모델 사용
    search_operation = op_schemas.SearchOperation(
        searchTerm=search_term
    )
    
    preview = op_schemas.OperationPreview(
        description=description
    )

    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=search_operation,
        requiresConfirmation=False,
        riskLevel="low",
        preview=preview
    )

def process_summarize(command, context, language):
    """
    요약 작업을 처리하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        language: 사용자 언어 ('ko' 또는 'en')
        
    Returns:
        작업 결과 정보
    """

    # 데이터 준비
    operationId = "op-"+str(uuid.uuid4())
    description = generate_summarize_description(command, context, language)

    # Pydantic 모델 사용
    summarize_operation = op_schemas.SummarizeOperation(
        targets=context.selectedFiles
    )
    
    preview = op_schemas.OperationPreview(
        description=description
    )

    # StageOperationResponse 객체 생성
    return op_schemas.StageOperationResponse(
        operationId=operationId,
        operation=summarize_operation,
        requiresConfirmation=True,
        riskLevel="low",
        preview=preview
    )





def get_destination(command, context, operation_type):
    """
    LLM을 사용하여 목적지 경로를 결정하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        operation_type: 작업 타입 ('move' 또는 'copy')
    
    Returns:
        str: 목적지 경로
    """
    
    # 사용 가능한 폴더 목록을 문자열로 변환
    available_folders_str = ""
    if context.availableFolders:
        folders_list = []
        for folder in context.availableFolders:
            folders_list.append(f"Name: {folder.name}, Path: {folder.path}")
        available_folders_str = "\n".join(folders_list)
    else:
        available_folders_str = "No available folders"
    
    # get_description 함수와 동일하게 한국어 버전 프롬프트는 삭제했다. 그러나 language 데이터는 프롬프트에 추가하지 않았다. 이유는 목적지 값은 번역이 필요없기 때문에.
    prompt_template = """
        <Instructions>
You need to extract the destination folder name that the user wants to {operation_type} files to from the user's command.

First, analyze the user's command to understand what destination folder they want.
Then check if that destination folder exists in the available folders list.

If the destination exists in available folders, return the corresponding path.
If the destination doesn't exist in available folders, return "create_folder/[folder_name]".
If no specific destination is mentioned, return "/".

Output format:
<destination>destination_path_here</destination>
        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Available folders>
{available_folders}
        </Available folders>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.1,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "operation_type": operation_type,
            "available_folders": available_folders_str
        })
        
        # 모델 출력에서 destination 추출
        if "<destination>" in result and "</destination>" in result:
            destination = result.split("<destination>")[1].split("</destination>")[0].strip()
            return destination
        else:
            logger.warning(f"Could not parse destination from LLM output: {result}")
            return "/"
            
    except Exception as e:
        logger.error(f"Error in get_destination: {e}")
        return "/"

def get_description(command, context, destination='/', operation_type="default", language='ko', new_name=None):
    """
    LLM을 사용하여 작업 설명을 생성하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        destination: 목적지 경로
        operation_type: 작업 타입
        language: 사용자 언어 ('ko' 또는 'en')
        new_name: 새로운 이름
    
    Returns:
        str: 작업 설명 문장
    """
    
    # 선택된 파일 정보를 문자열로 변환
    selected_files_str = ""
    if context.selectedFiles:
        files_list = []
        for file in context.selectedFiles:
            files_list.append(f"Name: {file.name}, Type: {file.type}")
        selected_files_str = "\n".join(files_list)
    else:
        selected_files_str = "No files selected"
    
    # destination에서 create_folder 제거 (있다면)
    clean_destination = destination
    if destination.startswith('create_folder/'):
        clean_destination = destination.replace('create_folder/', '', 1)
    
    prompt_template = """
        <Instructions>
        User's Language: {language}
        
You must respond in the language specified by the user's language setting:
- If language is "ko" or starts with "ko", respond in Korean
- If language is "en" or starts with "en", respond in English

Based on the user's command and the selected files, generate a short and clear description of what will happen when this {operation_type} operation is executed.

The description should be:
- Concise and informative
- Maximum 2 sentences
- Written in the user's specified language

IMPORTANT - Different formats based on operation type:

1. For DELETE operations:
   - Do NOT mention destination at all
   - Focus only on what files/folders will be deleted
   - Format: "Will delete [files]" or "선택된 파일들을 삭제합니다"

2. For RENAME operations:
   - Do NOT mention destination at all
   - Focus on the original name and new name
   - Format: "Will rename [original_name] to [new_name]" or "[바뀌기 전의 아이템 이름]을 [new_name]으로 변경합니다"
   - Use the name from selected files as the original name
   - Use the provided new_name parameter as the target name

3. For other operations (move, copy, etc.):
   - Include destination information
   - Format: "Will {operation_type} [files] to [destination]" or "선택된 파일들을 [destination]로 {operation_type}합니다"
   - If destination starts with "/" it means an existing folder
   - If destination doesn't start with "/" it means a new folder will be created

        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Operation type>{operation_type}</Operation type>
        
        <Selected files>
{selected_files}
        </Selected files>
        
        <Destination>{destination}</Destination>
        
        <New name (for rename operations)>{new_name}</New name (for rename operations)>
        
        <Description format>
<description>Your description here</description>
        </Description format>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.3,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "operation_type": operation_type,
            "selected_files": selected_files_str,
            "destination": clean_destination,
            "language": language,
            "new_name": new_name or ""
        })
        
        # 모델 출력에서 description 추출
        if "<description>" in result and "</description>" in result:
            description = result.split("<description>")[1].split("</description>")[0].strip()
            return description
        else:
            logger.warning(f"Could not parse description from LLM output: {result}")
            # 기본 설명 생성
            if language.startswith('en'):
                return f"Will {operation_type} selected files to {clean_destination}"
            else:
                return f"선택된 파일들을 {clean_destination}로 {operation_type}합니다"
            
    except Exception as e:
        logger.error(f"Error in get_description: {e}")
        # 기본 설명 생성
        if language.startswith('en'):
            return f"Will {operation_type} selected files to {clean_destination}"
        else:
            return f"선택된 파일들을 {clean_destination}로 {operation_type}합니다"

def extract_move_destination(command, context):
    """
    이동 작업에 대한 목적지를 추출하는 함수
    
    Args:
        command: 사용자의 자연어 명령
    """
    # 1. 사용자 명령에서 목적지 추출
    input_destination = None
    
    # 다양한 패턴으로 목적지 추출 시도
    patterns = [
        r'(\w+)\s*폴더로',  # "마케팅 폴더로"
        r'(\w+)\s*폴더에', # "마케팅 폴더에"
        r'(\w+)로\s*이동',  # "마케팅로 이동"
        r'(\w+)에\s*이동',  # "마케팅에 이동"
        r'(\w+)\s*디렉토리로', # "마케팅 디렉토리로"
        r'(\w+)\s*디렉토리에', # "마케팅 디렉토리에"
        r'로\s*옮겨.*?(\w+)', # "로 옮겨 마케팅"
        r'에\s*옮겨.*?(\w+)', # "에 옮겨 마케팅"
    ]
    
    # 각 패턴을 순서대로 시도하여 목적지 추출
    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            input_destination = match.group(1)
            break  # 첫 번째 매칭되는 패턴에서 중단
    
    # 2. availableFolders에서 매칭되는 path 찾기
    if input_destination:
        for folder in context.availableFolders:
            if folder.get('name') == input_destination:
                output_destination = folder.get('path')
                return output_destination
    
    # 매칭되는 폴더가 없는 경우 output_destination에 'create_folder' 코드 추가.
    if context.availableFolders:
        output_destination = 'create_folder'+'/'+input_destination
    else:
        output_destination = '/'
    
    return output_destination

def generate_move_description(context, destination):
    """
    이동 작업에 대한 설명 문장을 생성하는 함수

    Args:
        context: 작업 컨텍스트 정보 (selectedFiles 포함)
        destination: 목적지 경로 ('create_folder/폴더명' 형태일 수 있음)
        
    Returns:
        str: "파일명들을 목적지로 이동합니다." 형태의 설명 문장
    """
    # 1. selectedFiles 값 준비
    # selectedFiles 리스트의 각 아이템 객체의 name키의 값을 나열하여 저장
    file_names = []
    for file in context.selectedFiles:
        file_names.append(file.get('name', ''))
    st_result = ', '.join(file_names)

    # 2. destination 값 파싱
    # destination에서 'create_folder' 문자열이 있으면 제거
    if destination.startswith('create_folder'):
        ds_result = destination.replace('create_folder', '', 1)
    else:
        ds_result = destination
    
    # 3. description 문장 생성
    desc_result = st_result + "를 " + ds_result + "로 이동합니다."

    return desc_result

def extract_copy_destination(command, context):
    """
    복사 작업에 대한 목적지를 추출하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
    
    Returns:
        str: 목적지 경로
    """
    # 1. 사용자 명령에서 목적지 추출
    input_destination = None
    
    # 다양한 패턴으로 목적지 추출 시도 (복사 작업에 맞는 패턴 포함)
    patterns = [
        r'(\w+)\s*폴더로',  # "마케팅 폴더로"
        r'(\w+)\s*폴더에', # "마케팅 폴더에"
        r'(\w+)로\s*복사',  # "마케팅로 복사"
        r'(\w+)에\s*복사',  # "마케팅에 복사"
        r'(\w+)\s*디렉토리로', # "마케팅 디렉토리로"
        r'(\w+)\s*디렉토리에', # "마케팅 디렉토리에"
        r'로\s*백업.*?(\w+)', # "로 백업 마케팅"
        r'에\s*백업.*?(\w+)', # "에 백업 마케팅"
        r'(\w+)\s*폴더에\s*백업', # "마케팅 폴더에 백업"
        r'(\w+)\s*폴더로\s*백업', # "마케팅 폴더로 백업"
    ]
    
    # 각 패턴을 순서대로 시도하여 목적지 추출
    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            input_destination = match.group(1)
            break  # 첫 번째 매칭되는 패턴에서 중단
    
    # 2. availableFolders에서 매칭되는 path 찾기
    if input_destination:
        for folder in context.availableFolders:
            if folder.get('name') == input_destination:
                output_destination = folder.get('path')
                return output_destination
    
    # 매칭되는 폴더가 없는 경우 output_destination에 'create_folder' 코드 추가.
    if context.availableFolders:
        output_destination = 'create_folder'+'/'+input_destination
    else:
        output_destination = '/'
    
    return output_destination

def generate_copy_description(context, destination='/'):
    """
    복사 작업에 대한 설명 문장을 생성하는 함수

    Args:
        context: 작업 컨텍스트 정보 (selectedFiles 포함)
        destination: 목적지 경로 ('create_folder/폴더명' 형태일 수 있음)
        
    Returns:
        str: "파일명들을 목적지로 복사합니다." 형태의 설명 문장
    """
    # 1. selectedFiles 값 준비
    # selectedFiles 리스트의 각 아이템 객체의 name키의 값을 나열하여 저장
    file_names = []
    for file in context.selectedFiles:
        file_names.append(file.get('name', ''))
    st_result = ', '.join(file_names)

    # 2. destination 값 파싱
    # destination에서 'create_folder' 문자열이 있으면 제거
    if destination.startswith('create_folder'):
        ds_result = destination.replace('create_folder', '', 1)
    else:
        ds_result = destination
    
    # 3. description 문장 생성 (복사 작업에 맞게 수정)
    desc_result = st_result + "를 " + ds_result + "로 복사합니다."

    return desc_result

def generate_delete_description(context):
    """
    삭제 작업에 대한 설명 문장을 생성하는 함수
    
    Args:
        context: 작업 컨텍스트 정보 (selectedFiles 포함)
        
    Returns:
        str: "선택한 X개 항목 (파일Y, 폴더 Z) 을 영구적으로 삭제합니다. 이 작업은 되돌릴 수 없습니다." 형태의 설명 문장
    """
    # 1. selectedFiles에서 파일과 폴더 개수 세기
    file_count = 0
    folder_count = 0
    
    for item in context.selectedFiles:
        item_type = item.get('type', '')
        if item_type == 'folder':
            folder_count += 1
        else:
            file_count += 1
    
    # 2. 전체 항목 개수 계산
    total_count = file_count + folder_count
    
    # 3. 항목 타입별 설명 문구 생성
    type_description_parts = []
    if file_count > 0:
        type_description_parts.append(f"파일{file_count}")
    if folder_count > 0:
        type_description_parts.append(f"폴더 {folder_count}")
    
    type_description = ", ".join(type_description_parts)
    
    # 4. 최종 설명 문장 생성
    desc_result = f"선택한 {total_count}개 항목 ({type_description}) 을 영구적으로 삭제합니다. 이 작업은 되돌릴 수 없습니다."

    return desc_result

    
def get_new_name(command, context, language):
    """
    LLM을 사용하여 이름 변경 작업에 대한 새로운 이름을 추출하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        language: 사용자 언어
    
    Returns:
        str: 추출된 새로운 이름 (없으면 None)
    """
    
    # 선택된 파일 정보를 문자열로 변환
    selected_files_str = ""
    if context.selectedFiles:
        files_list = []
        for file in context.selectedFiles:
            files_list.append(f"Name: {file.name}, Type: {file.type}")
        selected_files_str = "\n".join(files_list)
    else:
        selected_files_str = "No files selected"
    
    prompt_template = """
        <Instructions>
        User's Language: {language}
        
You must respond in the language specified by the user's language setting:
- If language is "ko" or starts with "ko", respond in Korean
- If language is "en" or starts with "en", respond in English

Analyze the user's command and the selected files to extract the new name that the user wants to rename the file/folder to.

Steps to follow:
1. Look at the selected files to understand what file/folder is being renamed
2. Analyze the user's command to find what new name they want to give to the file/folder
3. Extract only the new name (without file extension unless specifically mentioned)
4. If no clear new name is found, return "None"

Important rules:
- Extract only the new name part, not the entire command
- Do not include words like "으로", "로", "바꿔", "변경", "수정" etc.
- If the user mentions a file extension, include it in the new name
- If the original file has an extension but user doesn't mention it, do NOT include extension in the new name

Output format:
<new_name>extracted_new_name_here</new_name>

If no new name is found, output:
<new_name>None</new_name>
        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Selected files>
{selected_files}
        </Selected files>
        
        <New name format>
<new_name>Your extracted new name here</new_name>
        </New name format>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.1,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "selected_files": selected_files_str,
            "language": language
        })
        
        # 모델 출력에서 new_name 추출
        if "<new_name>" in result and "</new_name>" in result:
            new_name = result.split("<new_name>")[1].split("</new_name>")[0].strip()
            # "None"이면 실제 None 반환
            if new_name.lower() == "none":
                return None
            return new_name
        else:
            logger.warning(f"Could not parse new name from LLM output: {result}")
            return None
            
    except Exception as e:
        logger.error(f"Error in get_new_name: {e}")
        return None

def generate_rename_description(context, new_name):
    """
    이름 변경 작업에 대한 설명 문장을 생성하는 함수
    
    Args:
        context: 작업 컨텍스트 정보 (selectedFiles 포함)
        new_name: 새로운 이름
        
    Returns:
        str: "문서 이름을 new_name(으)로 변경합니다." 또는 "디렉토리 이름을 new_name(으)로 변경합니다." 형태의 설명 문장
    """
    # 1. selectedFiles에서 첫 번째 아이템의 타입 확인
    # (rename 작업은 일반적으로 단일 아이템에 대해 수행됨)
    if context.selectedFiles and len(context.selectedFiles) > 0:
        selected_item = context.selectedFiles[0]
        item_type = selected_item.get('type', '')
        
        # 2. 아이템 타입에 따른 설명 문구 생성
        if item_type == 'folder':
            # 폴더(디렉토리)인 경우
            desc_result = f"디렉토리 이름을 {new_name}(으)로 변경합니다."
        else:
            # 파일인 경우 (기본값)
            desc_result = f"문서 이름을 {new_name}(으)로 변경합니다."
    else:
        # 선택된 아이템이 없는 경우 기본 메시지
        desc_result = f"아이템 이름을 {new_name}(으)로 변경합니다."
    
    return desc_result

def get_new_folder_name_and_parent_path(command, context):
    """
    LLM을 사용하여 새 폴더 이름과 부모 경로를 추출하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보

    Returns:
        tuple: (새 폴더 이름, 새 폴더 부모 경로)
    """
    
    # 사용 가능한 폴더 목록을 문자열로 변환
    available_folders_str = ""
    if context.availableFolders:
        folders_list = []
        for folder in context.availableFolders:
            folders_list.append(f"Name: {folder.name}, Path: {folder.path}")
        available_folders_str = "\n".join(folders_list)
    else:
        available_folders_str = "No available folders"
    
    # 현재 경로 정보
    current_path = context.currentPath or "/"
    
    prompt_template = """
        <Instructions>
You need to analyze the user's command to extract:
1. The name of the new folder that the user wants to create
2. The parent path where the new folder should be created

Rules for folder name extraction:
- Extract only the folder name that the user wants to create
- Do not include words like "폴더", "디렉토리", "생성", "만들", "추가" etc.
- Just extract the actual name (e.g., if user says "프로젝트 폴더를 생성", extract "프로젝트")

Rules for parent path extraction:
1. If the user specifies a location in their command:
   - Check if the specified location exists in the available folders list
   - If it exists, return the corresponding path from available folders
   - If it doesn't exist, return "create_folder/[specified_location]"

2. If the user mentions current location (현재, 여기, 이곳, etc.):
   - Return the current path

3. If no specific location is mentioned:
   - Return "/" (root path)

Output format:
<new_folder_name>extracted_folder_name</new_folder_name>
<parent_path>extracted_parent_path</parent_path>

If extraction fails, return:
<new_folder_name>None</new_folder_name>
<parent_path>/</parent_path>
        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Current path>{current_path}</Current path>
        
        <Available folders>
{available_folders}
        </Available folders>
        
        <Output format>
<new_folder_name>Your extracted folder name here</new_folder_name>
<parent_path>Your extracted parent path here</parent_path>
        </Output format>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.1,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "current_path": current_path,
            "available_folders": available_folders_str
        })
        
        # 모델 출력에서 new_folder_name과 parent_path 추출
        new_folder_name = None
        parent_path = "/"
        
        if "<new_folder_name>" in result and "</new_folder_name>" in result:
            new_folder_name = result.split("<new_folder_name>")[1].split("</new_folder_name>")[0].strip()
            if new_folder_name.lower() == "none":
                new_folder_name = None
        
        if "<parent_path>" in result and "</parent_path>" in result:
            parent_path = result.split("<parent_path>")[1].split("</parent_path>")[0].strip()
            if not parent_path:
                parent_path = "/"
        
        # 추출에 실패한 경우 기본값 사용
        if not new_folder_name:
            logger.warning(f"Could not parse folder name from LLM output: {result}")
            new_folder_name = "새폴더"  # 기본 폴더명
        
        return (new_folder_name, parent_path)
            
    except Exception as e:
        logger.error(f"Error in get_new_folder_name_and_parent_path: {e}")
        return ("새폴더", "/")


def get_parent_path(command, context):
    """
    새 폴더의 부모 경로를 추출하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보
        folder_name: 생성할 폴더 이름(A)
    
    Returns:
        str: 부모 경로 (기존 경로 또는 'create_folder/B' 형태)
    """
    # 1. 현재 위치 관련 패턴 확인 (최우선)
    current_location_patterns = [
        r'현재\s*디렉토리',      # "현재 디렉토리에"
        r'현재\s*위치',         # "현재 위치에" 
        r'현재\s*폴더',         # "현재 폴더에"
        r'이\s*위치',          # "이 위치에"
        r'이\s*폴더',          # "이 폴더에"
        r'여기',              # "여기에"
        r'지금\s*위치',         # "지금 위치에"
        r'이곳'               # "이곳에"
    ]
    
    for pattern in current_location_patterns:
        if re.search(pattern, command):
            return context.currentPath or '/'
    
    # 2. 사용자 명령에서 부모 디렉토리 이름(B) 추출
    parent_dir_name = None
    
    # 다양한 패턴으로 부모 디렉토리 위치 추출 시도
    patterns = [
        r'(\w+)\s*폴더\s*안에',  # "프로젝트 폴더 안에"
        r'(\w+)\s*폴더\s*내에',  # "프로젝트 폴더 내에" 
        r'(\w+)\s*디렉토리\s*안에',  # "프로젝트 디렉토리 안에"
        r'(\w+)\s*디렉토리\s*내에',  # "프로젝트 디렉토리 내에"
        r'(\w+)\s*내에\s*생성',  # "프로젝트 내에 생성" 
        r'(\w+)\s*안에\s*생성',  # "프로젝트 안에 생성"
        r'(\w+)\s*폴더\s*아래',  # "프로젝트 폴더 아래"
        r'(\w+)\s*디렉토리\s*아래',  # "프로젝트 디렉토리 아래"
    ]
    
    # 각 패턴을 순서대로 시도하여 부모 디렉토리 이름 추출
    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            parent_dir_name = match.group(1)
            break  # 첫 번째 매칭되는 패턴에서 중단
    
    # 3. availableFolders에서 매칭되는 path 찾기
    if parent_dir_name:
        for folder in context.availableFolders:
            if folder.get('name') == parent_dir_name:
                return folder.get('path')  # C 값 반환
        
        # 매칭되는 폴더가 없는 경우 'create_folder/B' 반환
        return 'create_folder/' + parent_dir_name
    
    # 부모 디렉토리가 명시되지 않은 경우 현재 경로 사용
    return context.currentPath or '/'

def generate_create_folder_description(folder_name, parent_path, language):
    """
    폴더 생성 작업에 대한 설명 문장을 생성하는 함수
    
    Args:
        folder_name: 생성할 폴더 이름
        parent_path: 폴더 생성 위치
        language: 사용자 언어 ('ko' 또는 'en')
            
    Returns:
        str: 폴더 생성 작업에 대한 설명 문장
    """
    # parent_path에 'create_folder' 문자열이 포함되어 있는지 확인
    if 'create_folder' in parent_path:
        # 'create_folder/'를 제거한 값을 parent_dir_name에 할당
        parent_dir_name = parent_path.replace('create_folder/', '')
    else:
        # parent_path를 '/'로 split하고 가장 마지막 경로 문자열을 가져옴
        path_parts = parent_path.strip('/').split('/')
        # 빈 문자열이거나 루트 경로인 경우 처리
        if path_parts and path_parts[0]:
            parent_dir_name = path_parts[-1]
        else:
            # 언어에 따른 루트 표현
            parent_dir_name = 'Root' if language.startswith('en') else '루트'
    
    # 언어에 따른 결과 문자열 생성
    if language.startswith('en'):
        result_desc = f"Create '{folder_name}' folder in {parent_dir_name}."
    else:
        result_desc = f"{parent_dir_name} 내에 {folder_name} 폴더를 생성합니다."
    
    return result_desc


def get_search_term(command, language):
    """
    LLM을 사용하여 사용자의 명령에서 검색하고 싶은 내용을 추출한다.
    
    Args:
        command: 사용자의 자연어 명령
        language: 사용자 언어 ('ko' 또는 'en')
        
    Returns:
        str: 추출된 검색 키워드
    """
    
    prompt_template = """
        <Instructions>
        User's Language: {language}
        
You must respond in the language specified by the user's language setting:
- If language is "ko" or starts with "ko", respond in Korean
- If language is "en" or starts with "en", respond in English

Analyze the user's search command and generate an appropriate search term based on what they're looking for.

Rules for search term generation:
1. If the user asks for file location (예: "파일의 위치를 알려줘", "where is the file"):
   - Korean: Extract "[파일명]의 위치" 
   - English: Extract "location of [filename]"

2. If the user asks which folder contains a file (예: "파일은 어떤 폴더에 있어?", "which folder contains the file"):
   - Korean: Extract "[파일명]이 저장된 디렉토리" or "[파일명]이 저장된 폴더"
   - English: Extract "directory containing [filename]" or "folder containing [filename]"

3. If the user asks about file content (예: "계약서에서 조건 관련 내용", "contract terms"):
   - Keep the search intent as is, but make it clear and searchable
   - Korean: "[문서명]에서 [검색내용]" or just "[검색내용]"
   - English: "[search content] in [document]" or just "[search content]"

4. For general searches:
   - Extract the main search keywords
   - Remove unnecessary command words like "찾아줘", "검색해", "find", "search"
   - Keep the essential search terms

Output format:
<search_term>your_generated_search_term_here</search_term>

If no clear search intent is found, return:
<search_term>None</search_term>
        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Search term format>
<search_term>Your generated search term here</search_term>
        </Search term format>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.1,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "language": language
        })
        
        # 모델 출력에서 search_term 추출
        if "<search_term>" in result and "</search_term>" in result:
            search_term = result.split("<search_term>")[1].split("</search_term>")[0].strip()
            # "None"이면 기본 검색어 사용
            if search_term.lower() == "none":
                return command.strip()
            return search_term
        else:
            logger.warning(f"Could not parse search term from LLM output: {result}")
            return command.strip()
            
    except Exception as e:
        logger.error(f"Error in get_search_term: {e}")
        return command.strip()

def clean_search_command(command):
    """
    검색 명령에서 불필요한 명령어 부분만 제거하고 
    중요한 검색 키워드와 컨텍스트는 유지한다.
    
    Args:
        command: 원본 명령어
        
    Returns:
        str: 정리된 검색어
    """
    # 제거할 명령어 패턴들 (순서 중요)
    remove_patterns = [
        # 문장 끝 패턴들 (물음표 포함)
        r'\s*찾아\s*줘?\s*\??$',
        r'\s*검색\s*해?\s*줘?\s*\??$',
        r'\s*어디\s*있어\s*\??$',
        r'\s*어디\s*에?\s*있나\s*\??$',
        r'\s*어디\s*있지\s*\??$',
        r'\s*어디\s*에?\s*저장\s*되어?\s*있어\s*\??$',
        r'\s*확인\s*해?\s*줘?\s*\??$',
        r'\s*알려\s*줘?\s*\??$',
        r'\s*찾아\s*봐?\s*\??$',
        r'\s*위치\s*알려\s*줘?\s*\??$',
        r'\s*경로\s*알려\s*줘?\s*\??$',
        r'\s*어디\s*에?\s*$',
        r'\s*어디\s*$',
        
        # 특수 케이스
        r'\s*이\s*어디\s*있지\s*\??$',  # '파일이 어디있지?'
    ]
    
    # 원본 명령어 복사
    cleaned = command
    
    # 패턴 제거
    for pattern in remove_patterns:
        cleaned = re.sub(pattern, '', cleaned)
    
    # 추가 정리
    cleaned = cleaned.strip()
    
    # 빈 문자열이면 원본 반환
    if not cleaned:
        return command
    
    # 특수 케이스 처리
    cleaned = handle_special_cases(cleaned, command)
    
    return cleaned


def handle_special_cases(cleaned, original):
    """
    특수한 케이스들을 처리하는 함수
    
    Args:
        cleaned: 정리된 검색어
        original: 원본 명령어
        
    Returns:
        str: 최종 검색어
    """
    # '~가 어디있는지', '~이 어디있는지' 패턴 제거
    cleaned = re.sub(r'\s*가\s*어디\s*있는지\s*', '', cleaned)
    cleaned = re.sub(r'\s*이\s*어디\s*있는지\s*', '', cleaned)
    
    # '~에서 ~' 패턴 처리
    if '에서' in cleaned:
        # '계약서에서 조건 관련 내용' 형태로 유지
        parts = cleaned.split('에서', 1)
        if len(parts) == 2:
            doc = parts[0].strip()
            content = parts[1].strip()
            return f"{doc}에서 {content}"
    
    # '보고서에 매출 데이터가 있는지' → '보고서 매출 데이터'
    if '에' in cleaned and ('데이터가' in cleaned or '있는지' in cleaned):
        # '있는지' 제거
        cleaned = re.sub(r'\s*있는지\s*', '', cleaned)
        # '에' → 공백으로 변경
        cleaned = re.sub(r'에\s+', ' ', cleaned)
        # '데이터가' → '데이터'
        cleaned = cleaned.replace('데이터가', '데이터')
    
    # '~가 있는지' 패턴 제거
    cleaned = re.sub(r'\s*가\s*있는지\s*', '', cleaned)
    cleaned = re.sub(r'\s*이\s*있는지\s*', '', cleaned)
    
    # 불필요한 조사 제거 (순서 중요)
    # '~들' 처리 (파일들, 문서들)
    if '들' in cleaned and ('파일들' in cleaned or '문서들' in cleaned):
        # '파일들', '문서들'은 유지
        pass
    else:
        # 다른 경우의 '들' 제거
        cleaned = re.sub(r'(\w+)들\s+', r'\1 ', cleaned)
    
    # 기타 조사 정리
    cleaned = re.sub(r'\s+을\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+를\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+이\s+', ' ', cleaned)
    cleaned = re.sub(r'\s+가\s+', ' ', cleaned)
    
    # 문장 끝 조사 제거
    cleaned = re.sub(r'\s*을\s*$', '', cleaned)
    cleaned = re.sub(r'\s*를\s*$', '', cleaned)
    cleaned = re.sub(r'\s*이\s*$', '', cleaned)
    cleaned = re.sub(r'\s*가\s*$', '', cleaned)
    
    # 중복 공백 제거
    cleaned = ' '.join(cleaned.split())
    
    return cleaned

def generate_search_description(search_term, language):
    if language.startswith('en'):
        description = f"Search for '{search_term}'."
    else:
        description = f"'{search_term}'에 대한 검색을 실행합니다."
    return description

def generate_summarize_description(command, context, language):
    """
    LLM을 사용하여 요약 작업에 대한 설명 문장을 생성하는 함수
    
    Args:
        command: 사용자의 자연어 명령
        context: 작업 컨텍스트 정보 (selectedFiles 포함)
        language: 사용자 언어 ('ko' 또는 'en')
        
    Returns:
        str: "파일명의 주요 내용을 요약합니다." 또는 "선택한 X개 문서의 주요 내용을 요약합니다." 형태의 설명 문장
    """
    
    # 선택된 파일이 없는 경우 처리
    if not context.selectedFiles or len(context.selectedFiles) == 0:
        if language.startswith('en'):
            return "No documents selected."
        else:
            return "선택된 문서가 없습니다."
    
    # 선택된 파일 정보를 문자열로 변환
    selected_files_str = ""
    files_list = []
    for file in context.selectedFiles:
        files_list.append(f"Name: {file.name}, Type: {file.type}")
    selected_files_str = "\n".join(files_list)
    
    file_count = len(context.selectedFiles)
    
    prompt_template = """
        <Instructions>
        User's Language: {language}
        
You must respond in the language specified by the user's language setting:
- If language is "ko" or starts with "ko", respond in Korean
- If language is "en" or starts with "en", respond in English

Based on the user's command and the selected files, generate a clear description of what summarization will be performed.

Rules for description generation:
1. For single file:
   - Korean: "[파일명]의 주요 내용을 요약합니다."
   - English: "Summarize the main content of [filename]."
   - Remove file extensions for more natural sentences

2. For multiple files (3 or fewer):
   - Korean: "[파일명1], [파일명2]의 주요 내용을 요약합니다."
   - English: "Summarize the main content of [filename1], [filename2]."
   - List all file names, removing extensions

3. For many files (more than 3):
   - Korean: "선택한 {count}개 문서의 주요 내용을 요약합니다."
   - English: "Summarize the main content of {count} selected documents."

Important notes:
- Remove file extensions from names for cleaner sentences
- Use appropriate counting and grammar for the target language
- Keep the description concise and informative
- File count: {file_count}

Output format:
<description>Your description here</description>
        </Instructions>
        
        <User's command>{command}</User's command>
        
        <Selected files (total: {file_count})>
{selected_files}
        </Selected files>
        
        <Description format>
<description>Your description here</description>
        </Description format>
        
        Answer:
        """
    
    prompt = PromptTemplate.from_template(prompt_template)
    
    # OpenAI 모델 객체 생성
    llm = ChatOpenAI(
        temperature=0.1,
        max_tokens=1000,
        model_name="gpt-4o-mini"
    )
    
    # 체인 생성
    chain = prompt | llm | StrOutputParser()
    
    # 체인 실행
    try:
        result = chain.invoke({
            "command": command,
            "selected_files": selected_files_str,
            "file_count": file_count,
            "language": language
        })
        
        # 모델 출력에서 description 추출
        if "<description>" in result and "</description>" in result:
            description = result.split("<description>")[1].split("</description>")[0].strip()
            return description
        else:
            logger.warning(f"Could not parse description from LLM output: {result}")
            # 기본 설명 생성
            if language.startswith('en'):
                if file_count == 1:
                    return "Summarize the main content of the selected document."
                else:
                    return f"Summarize the main content of {file_count} selected documents."
            else:
                if file_count == 1:
                    return "선택된 문서의 주요 내용을 요약합니다."
                else:
                    return f"선택한 {file_count}개 문서의 주요 내용을 요약합니다."
            
    except Exception as e:
        logger.error(f"Error in generate_summarize_description: {e}")
        # 기본 설명 생성
        if language.startswith('en'):
            if file_count == 1:
                return "Summarize the main content of the selected document."
            else:
                return f"Summarize the main content of {file_count} selected documents."
        else:
            if file_count == 1:
                return "선택된 문서의 주요 내용을 요약합니다."
            else:
                return f"선택한 {file_count}개 문서의 주요 내용을 요약합니다."


# ===== 작업 실행 로직 헬퍼 함수들 =====

async def execute_operation_logic(operation_type: str, operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """
    작업 타입별 실제 실행 로직
    
    Args:
        operation_type: 작업 타입
        operation: 작업 상세 정보
        user_options: 사용자 옵션
        current_user: 현재 사용자
        db: 데이터베이스 세션
        
    Returns:
        dict: 실행 결과 정보
    """
    logger.info(f"Executing operation logic for type: {operation_type}")
    
    try:
        if operation_type == "move":
            return await execute_move_logic(operation, user_options, current_user, db)
        elif operation_type == "copy":
            return await execute_copy_logic(operation, user_options, current_user, db)
        elif operation_type == "delete":
            return await execute_delete_logic(operation, user_options, current_user, db)
        elif operation_type == "rename":
            return await execute_rename_logic(operation, user_options, current_user, db)
        elif operation_type == "create_folder":
            return await execute_create_folder_logic(operation, user_options, current_user, db)
        elif operation_type == "search":
            return await execute_search_logic(operation, user_options, current_user, db)
        elif operation_type == "summarize":
            return await execute_summarize_logic(operation, user_options, current_user, db)
        else:
            logger.error(f"Unknown operation type: {operation_type}")
            return {
                "message": f"지원되지 않는 작업 타입입니다: {operation_type}",
                "undoAvailable": False
            }
            
    except Exception as e:
        logger.error(f"Error executing {operation_type} operation: {e}")
        return {
            "message": f"작업 실행 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_move_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """이동 작업 실행 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    user_id = current_user.id
    targets = operation.get("targets", [])
    destination = operation.get("destination", "/")
    
    logger.info(f"Moving {len(targets)} files to {destination}")
    
    try:
        # process_directory_operations 형식에 맞게 데이터 준비
        operations = []
        for target in targets:
            operations.append({
                "operation_type": "move",
                "item_id": target.get("id"),
                "name": target.get("name"),
                "target_path": destination,
                "path": destination  # target_path와 path 둘 다 사용하는 경우를 위해
            })
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        success_count = sum(1 for r in results if r.get("status") == "success")
        failed_count = len(results) - success_count
        
        if failed_count > 0:
            message = f"{success_count}개 파일이 이동되었습니다. {failed_count}개 실패"
        else:
            message = f"{len(targets)}개 파일이 {destination}로 이동되었습니다"
        
        # 결과를 Pydantic 모델로 변환
        operation_results = [
            op_schemas.OperationResult(
                status=r.get("status", "unknown"),
                message=r.get("message", ""),
                item_id=r.get("item_id")
            ) for r in results
        ]
        
        return {
            "message": message,
            "undoAvailable": True,
            "undoDeadline": (datetime.now() + timedelta(minutes=10)).isoformat(),
            "results": operation_results
        }
        
    except Exception as e:
        logger.error(f"Error executing move operation: {e}")
        return {
            "message": f"파일 이동 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_copy_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """복사 작업 실행 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    user_id = current_user.id
    targets = operation.get("targets", [])
    destination = operation.get("destination", "/")
    
    logger.info(f"Copying {len(targets)} files to {destination}")
    
    try:
        # process_directory_operations 형식에 맞게 데이터 준비
        operations = []
        for target in targets:
            operations.append({
                "operation_type": "copy",
                "item_id": target.get("id"),
                "name": target.get("name"),
                "target_path": destination,
                "path": destination
            })
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        success_count = sum(1 for r in results if r.get("status") == "success")
        failed_count = len(results) - success_count
        
        if failed_count > 0:
            message = f"{success_count}개 파일이 복사되었습니다. {failed_count}개 실패"
        else:
            message = f"{len(targets)}개 파일이 {destination}로 복사되었습니다"
        
        # 결과를 Pydantic 모델로 변환
        operation_results = [
            op_schemas.OperationResult(
                status=r.get("status", "unknown"),
                message=r.get("message", ""),
                item_id=r.get("item_id")
            ) for r in results
        ]
        
        return {
            "message": message,
            "undoAvailable": False,  # 복사는 일반적으로 undo 불가
            "results": operation_results
        }
        
    except Exception as e:
        logger.error(f"Error executing copy operation: {e}")
        return {
            "message": f"파일 복사 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_delete_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """삭제 작업 실행 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    user_id = current_user.id
    targets = operation.get("targets", [])
    
    logger.info(f"Deleting {len(targets)} files")
    
    try:
        # process_directory_operations 형식에 맞게 데이터 준비
        operations = []
        for target in targets:
            operations.append({
                "operation_type": "delete",
                "item_id": target.get("id"),
                "name": target.get("name")
            })
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        success_count = sum(1 for r in results if r.get("status") == "success")
        failed_count = len(results) - success_count
        
        if failed_count > 0:
            message = f"{success_count}개 파일이 삭제되었습니다. {failed_count}개 실패"
        else:
            message = f"{len(targets)}개 파일이 삭제되었습니다"
        
        # 결과를 Pydantic 모델로 변환
        operation_results = [
            op_schemas.OperationResult(
                status=r.get("status", "unknown"),
                message=r.get("message", ""),
                item_id=r.get("item_id")
            ) for r in results
        ]
        
        return {
            "message": message,
            "undoAvailable": False,  # 삭제는 일반적으로 undo 불가 (복구 어려움)
            "results": operation_results
        }
        
    except Exception as e:
        logger.error(f"Error executing delete operation: {e}")
        return {
            "message": f"파일 삭제 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_rename_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """이름 변경 작업 실행 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    user_id = current_user.id
    # rename은 target이 배열이 아닌 단일 객체 또는 배열의 첫 번째 요소
    target = operation.get("target", {})
    if isinstance(target, list) and len(target) > 0:
        target = target[0]
    
    new_name = operation.get("newName", "")
    
    logger.info(f"Renaming file to {new_name}")
    
    try:
        # process_directory_operations 형식에 맞게 데이터 준비
        operations = [{
            "operation_type": "rename",
            "item_id": target.get("id"),
            "name": new_name  # 새로운 이름
        }]
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        if results and results[0].get("status") == "success":
            message = f"파일 이름이 '{new_name}'으로 변경되었습니다"
        else:
            message = "파일 이름 변경에 실패했습니다"
        
        # 결과를 Pydantic 모델로 변환
        operation_results = [
            op_schemas.OperationResult(
                status=r.get("status", "unknown"),
                message=r.get("message", ""),
                item_id=r.get("item_id")
            ) for r in results
        ]
        
        return {
            "message": message,
            "undoAvailable": True,
            "undoDeadline": (datetime.now() + timedelta(minutes=10)).isoformat(),
            "results": operation_results
        }
        
    except Exception as e:
        logger.error(f"Error executing rename operation: {e}")
        return {
            "message": f"파일 이름 변경 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_create_folder_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """폴더 생성 작업 실행 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    user_id = current_user.id
    folder_name = operation.get("folderName", "")
    parent_path = operation.get("parentPath", "/")
    
    logger.info(f"Creating folder '{folder_name}' in {parent_path}")
    
    try:
        # process_directory_operations 형식에 맞게 데이터 준비
        operations = [{
            "operation_type": "create",
            "name": folder_name,
            "path": parent_path,
            "target_path": parent_path  # create 작업은 path 또는 target_path 사용
        }]
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        if results and results[0].get("status") == "success":
            message = f"'{folder_name}' 폴더가 생성되었습니다"
        else:
            message = "폴더 생성에 실패했습니다"
        
        # 결과를 Pydantic 모델로 변환
        operation_results = [
            op_schemas.OperationResult(
                status=r.get("status", "unknown"),
                message=r.get("message", ""),
                item_id=r.get("item_id")
            ) for r in results
        ]
        
        return {
            "message": message,
            "undoAvailable": True,
            "undoDeadline": (datetime.now() + timedelta(minutes=10)).isoformat(),
            "results": operation_results
        }
        
    except Exception as e:
        logger.error(f"Error executing create folder operation: {e}")
        return {
            "message": f"폴더 생성 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_search_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """검색 작업 실행 로직"""
    from rag.document_service import process_query
    from rag.llm import get_llms_answer
    from db.database import engine
    user_id = current_user.id
    search_term = operation.get("searchTerm", "")
    
    logger.info(f"Searching for: {search_term}")
    
    try:
        # RAG 검색 실행
        # process_query는 유사한 문서 청크들을 반환
        docs = process_query(db, user_id, search_term, engine)
        
        # LLM을 통해 자연스러운 답변 생성
        answer = get_llms_answer(docs, search_term)
        
        # 검색된 문서 정보 추출
        found_documents = []
        for doc in docs:
            if hasattr(doc, 'metadata') and doc.metadata:
                found_documents.append({
                    "name": doc.metadata.get('document_name', '알 수 없음'),
                    "path": doc.metadata.get('document_path', '/')
                })
        
        # 중복 제거
        unique_documents = []
        seen = set()
        for doc in found_documents:
            doc_key = (doc['name'], doc['path'])
            if doc_key not in seen:
                seen.add(doc_key)
                unique_documents.append(doc)
        
        # Pydantic 모델로 변환
        search_documents = [
            op_schemas.SearchDocument(
                name=doc["name"],
                path=doc["path"]
            ) for doc in unique_documents
        ]
        
        search_result_data = op_schemas.SearchResultData(
            answer=answer,
            documents=search_documents,
            documentCount=len(unique_documents)
        )
        
        return {
            "message": f"'{search_term}' 검색이 완료되었습니다",
            "undoAvailable": False,  # 검색은 undo 불필요
            "searchResults": search_result_data
        }
        
    except Exception as e:
        logger.error(f"Error executing search operation: {e}")
        return {
            "message": f"검색 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


async def execute_summarize_logic(operation: dict, user_options: dict, current_user: User, db: Session) -> dict:
    """요약 작업 실행 로직"""
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import PromptTemplate
    from db import crud
    import boto3
    from config.settings import S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
    
    user_id = current_user.id
    targets = operation.get("targets", [])
    
    logger.info(f"Summarizing {len(targets)} documents")
    
    try:
        # S3 클라이언트 초기화
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_DEFAULT_REGION
        )
        
        # 각 문서의 내용을 가져와서 요약
        summaries = []
        
        for target in targets:
            target_id = target.get("id")
            target_name = target.get("name", "문서")
            
            # 파일인 경우에만 처리 (폴더는 건너뛰기)
            if target.get("type") == "folder":
                continue
            
            # S3 키 가져오기
            s3_key = crud.get_s3_key_by_id(db, target_id, user_id)
            
            if not s3_key:
                logger.warning(f"S3 key not found for document {target_id}")
                continue
            
            # S3에서 파일 내용 가져오기
            try:
                response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
                file_content = response['Body'].read()
                
                # 파일 타입에 따라 텍스트 추출
                file_extension = target_name.split('.')[-1].lower()
                
                if file_extension == 'pdf':
                    from rag.file_load import load_pdf
                    documents = await load_pdf(file_content)
                elif file_extension == 'docx':
                    from rag.file_load import load_docx
                    documents = await load_docx(file_content)
                elif file_extension in ['hwp', 'hwpx']:
                    from rag.file_load import load_hwp
                    documents = await load_hwp(file_content, file_extension)
                else:
                    logger.warning(f"Unsupported file type: {file_extension}")
                    continue
                
                # 문서 내용 합치기
                full_text = "\n".join(documents)
                
                # 텍스트가 너무 길면 잘라내기
                if len(full_text) > 10000:
                    full_text = full_text[:10000] + "..."
                
                # LLM으로 요약
                llm = ChatOpenAI(
                    temperature=0.3,
                    max_tokens=500,
                    model_name="gpt-4o-mini"
                )
                
                prompt = PromptTemplate.from_template(
                    """다음 문서의 내용을 한국어로 요약해주세요. 핵심 내용을 중심으로 3-5개의 문장으로 요약하세요.
                    
                    문서명: {document_name}
                    
                    내용:
                    {content}
                    
                    요약:"""
                )
                
                chain = prompt | llm
                summary = chain.invoke({
                    "document_name": target_name,
                    "content": full_text
                })
                
                summaries.append({
                    "name": target_name,
                    "summary": summary.content if hasattr(summary, 'content') else str(summary)
                })
                
            except Exception as e:
                logger.error(f"Error summarizing document {target_name}: {e}")
                summaries.append({
                    "name": target_name,
                    "summary": f"요약 중 오류 발생: {str(e)}"
                })
        
        # 결과 메시지 생성
        if len(summaries) == 0:
            message = "요약할 수 있는 문서가 없습니다"
        else:
            message = f"{len(summaries)}개 문서의 요약이 완료되었습니다"
        
        # Pydantic 모델로 변환
        summary_data_list = [
            op_schemas.SummaryData(
                name=summary["name"],
                summary=summary["summary"]
            ) for summary in summaries
        ]
        
        return {
            "message": message,
            "undoAvailable": False,  # 요약은 undo 불필요
            "summaries": summary_data_list
        }
        
    except Exception as e:
        logger.error(f"Error executing summarize operation: {e}")
        return {
            "message": f"문서 요약 중 오류가 발생했습니다: {str(e)}",
            "undoAvailable": False
        }


# ===== Undo 로직 헬퍼 함수들 =====

async def execute_undo_logic(operation_type: str, undo_data: dict, reason: str, current_user: User, db: Session) -> dict:
    """
    작업 타입별 undo 로직
    
    Args:
        operation_type: 원본 작업 타입
        undo_data: undo를 위한 데이터
        reason: undo 사유
        current_user: 현재 사용자
        db: 데이터베이스 세션
        
    Returns:
        dict: undo 결과 정보
    """
    user_id = current_user.id
    # operation_type이 제대로 전달되지 않은 경우 undo_data에서 추출
    if not operation_type and undo_data:
        operation = undo_data.get("operation", {})
        operation_type = operation.get("type")
    
    logger.info(f"Executing undo logic for type: {operation_type}, reason: {reason}")
    
    try:
        if operation_type == "move":
            return await undo_move_logic(undo_data, reason, user_id, db)
        elif operation_type == "rename":
            return await undo_rename_logic(undo_data, reason, user_id, db)
        elif operation_type == "create_folder":
            return await undo_create_folder_logic(undo_data, reason, user_id, db)
        else:
            return op_schemas.UndoResult(
                success=False,
                error=f"'{operation_type}' 작업은 되돌리기를 지원하지 않습니다"
            ).dict()
            
    except Exception as e:
        logger.error(f"Error undoing {operation_type} operation: {e}")
        return op_schemas.UndoResult(
            success=False,
            error=f"되돌리기 중 오류가 발생했습니다: {str(e)}"
        ).dict()


async def undo_move_logic(undo_data: dict, reason: str, user_id: int, db: Session) -> dict:
    """이동 작업 되돌리기 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    from db import crud
    
    # undo_data에서 필요한 정보 추출
    operation = undo_data.get("operation", {})
    targets = operation.get("targets", [])
    original_destination = operation.get("destination", "/")
    
    logger.info(f"Undoing move operation: moving {len(targets)} files back from {original_destination}")
    
    try:
        # 각 파일의 원래 위치 찾기
        operations = []
        for target in targets:
            target_id = target.get("id")
            target_name = target.get("name")
            original_path = target.get("path", "/")
            
            # 원래 경로에서 부모 디렉토리 추출
            if original_path == "/" or original_path == f"/{target_name}":
                original_parent_path = "/"
            else:
                # 파일명을 제거하여 부모 경로 얻기
                path_parts = original_path.rstrip('/').split('/')
                if path_parts[-1] == target_name:
                    path_parts = path_parts[:-1]
                original_parent_path = '/'.join(path_parts) if path_parts else '/'
            
            operations.append({
                "operation_type": "move",
                "item_id": target_id,
                "name": target_name,
                "target_path": original_parent_path,
                "path": original_parent_path
            })
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        success_count = sum(1 for r in results if r.get("status") == "success")
        
        if success_count == len(targets):
            return op_schemas.UndoResult(
                success=True,
                message=f"{len(targets)}개 파일이 원래 위치로 되돌려졌습니다"
            ).dict()
        else:
            return op_schemas.UndoResult(
                success=False,
                error=f"일부 파일을 되돌리는데 실패했습니다 (성공: {success_count}/{len(targets)})"
            ).dict()
            
    except Exception as e:
        logger.error(f"Error undoing move operation: {e}")
        return op_schemas.UndoResult(
            success=False,
            error=f"이동 작업 되돌리기 중 오류가 발생했습니다: {str(e)}"
        ).dict()


async def undo_rename_logic(undo_data: dict, reason: str, user_id: int, db: Session) -> dict:
    """이름 변경 작업 되돌리기 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    from db import crud
    
    # undo_data에서 필요한 정보 추출
    operation = undo_data.get("operation", {})
    target = operation.get("target", {})
    if isinstance(target, list) and len(target) > 0:
        target = target[0]
    
    new_name = operation.get("newName", "")
    target_id = target.get("id")
    original_name = target.get("name", "")
    
    logger.info(f"Undoing rename operation: changing '{new_name}' back to '{original_name}'")
    
    try:
        # 이름을 원래대로 되돌리기
        operations = [{
            "operation_type": "rename",
            "item_id": target_id,
            "name": original_name  # 원래 이름으로 되돌리기
        }]
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        if results and results[0].get("status") == "success":
            return op_schemas.UndoResult(
                success=True,
                message=f"파일 이름이 '{original_name}'으로 되돌려졌습니다"
            ).dict()
        else:
            return op_schemas.UndoResult(
                success=False,
                error="파일 이름을 되돌리는데 실패했습니다"
            ).dict()
            
    except Exception as e:
        logger.error(f"Error undoing rename operation: {e}")
        return op_schemas.UndoResult(
            success=False,
            error=f"이름 변경 작업 되돌리기 중 오류가 발생했습니다: {str(e)}"
        ).dict()


async def undo_create_folder_logic(undo_data: dict, reason: str, user_id: int, db: Session) -> dict:
    """폴더 생성 작업 되돌리기 로직"""
    from fast_api.endpoints.documents import process_directory_operations
    from db import crud
    
    # undo_data에서 필요한 정보 추출
    operation = undo_data.get("operation", {})
    folder_name = operation.get("folderName", "")
    parent_path = operation.get("parentPath", "/")
    
    # 생성된 폴더의 전체 경로 계산
    if parent_path == "/":
        folder_path = f"/{folder_name}"
    else:
        folder_path = f"{parent_path}/{folder_name}"
    
    logger.info(f"Undoing create folder operation: deleting folder '{folder_name}' at {folder_path}")
    
    try:
        # 폴더의 ID 찾기
        folder_id = crud.get_directory_id_by_path(db, folder_path, user_id)
        
        if not folder_id:
            return op_schemas.UndoResult(
                success=False,
                error=f"삭제할 폴더를 찾을 수 없습니다: {folder_path}"
            ).dict()
        
        # 폴더 삭제
        operations = [{
            "operation_type": "delete",
            "item_id": folder_id,
            "name": folder_name
        }]
        
        # 작업 실행
        results = await process_directory_operations(operations, user_id, db)
        
        # 결과 확인
        if results and results[0].get("status") == "success":
            return op_schemas.UndoResult(
                success=True,
                message=f"생성된 폴더 '{folder_name}'가 삭제되었습니다"
            ).dict()
        else:
            return op_schemas.UndoResult(
                success=False,
                error="폴더를 삭제하는데 실패했습니다"
            ).dict()
            
    except Exception as e:
        logger.error(f"Error undoing create folder operation: {e}")
        return op_schemas.UndoResult(
            success=False,
            error=f"폴더 생성 작업 되돌리기 중 오류가 발생했습니다: {str(e)}"
        ).dict()
