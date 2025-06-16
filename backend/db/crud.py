from sqlalchemy.orm import Session
from . import models
from datetime import datetime
from sqlalchemy import select, func, delete, text
from db.models import User
"""인덱스

인덱스는 임시로 작성한 정보이다.
gpt에게 이 소스 코드를 주고 각 함수의 실제 행위를 설명하라고 해서 각 함수에 작성된 주석과 실제 코드의 내용이 맞는지 확인해야 함.


아이템의 id로 해당 아이템 레코드에서 is_directory 필드의 값을 가져온다.
(select 문은 이 코드 구조를 기반으로 수정하기.(안정적인 코드이기 때문이다.))
get_file_is_directory_by_id

특정 디렉토리의 모든 하위(재귀) 중 파일(is_directory=False)인 레코드의 id만 리스트로 반환
get_child_file_ids

(자식들 리스트업)parent_id == directory_id인 레코드의 id 값을 모두 가져와서 리스트로 반환.
get_file_ids_by_parent_directory_id

(자식 여부 확인)directories테이블의 parent_id필드 값 == item_id인 레코드를 선택하여 가져온다.
get_directory_by_parent_id

아이템의 id로 해당 아이템 레코드에서 name 필드의 값을 가져온다.
get_file_name_by_id

아이템의 path로 id를 가져오기
get_directory_id_by_path

아이템의 id로 아이템의 이름과 경로를 업데이트하기
update_item_name_and_path

아이템의 id로 해당 아이템 레코드에서 parent_id 필드의 값을 가져온다.
get_parent_id_by_id

아이템의 id로 해당 아이템 레코드에서 path 필드의 값을 가져온다.
get_file_path_by_id

아이템의 id로 해당 아이템의 경로와 parent_id를 업데이트한다.
update_item_path_and_parent_id

아이템의 id로 해당 아이템의 경로와 부모 ID를 업데이트한다.
update_directory_path_and_parent

아이템의 id로 해당 아이템의 이름, 경로, 부모id를 업데이트.
update_directory_name_path_parent_id

Documents 테이블에 동일한 이름의 파일이 있는지 확인한다.
get_file_info_by_filename

Document 테이블에 filename이 존재하면 해당 레코드를 반환한다.
get_file_info_by_filename

새로운 정보로, directories 테이블에 새로운 레코드를 생성하고, 생성한 그 레코드를 반환한다.
create_directory
"""


# 사용자 관련 CRUD
def create_user(db: Session, username: str, email: str, password_hash: str):
    db_user = models.User(username=username, email=email, password_hash=password_hash)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# 사용자의 id로 사용자 정보를 가져오는 함수
def get_user_by_id(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()

# DB의 documents 테이블에 문서 정보 저장
def add_documents(db: Session, filename: str, s3_key: str, upload_time: datetime, user_id: int):
    db_document = models.Document(filename=filename, s3_key=s3_key, upload_time=upload_time, user_id=user_id)
    db.add(db_document)
    db.commit()
    db.refresh(db_document)
    return db_document

# 문서 ID로 문서 정보 가져오기
def get_document_by_id(db: Session, document_id: int, user_id: int):
    """문서 ID로 documents 테이블에서 문서 정보를 가져온다."""
    return db.query(models.Document).filter(models.Document.id == document_id, models.Document.user_id == user_id).first()

# 사용자 ID로 모든 문서 가져오기
def get_documents_by_user_id(db: Session, user_id: int):
    """사용자 ID로 해당 사용자의 모든 문서를 가져온다."""
    return db.query(models.Document).filter(models.Document.user_id == user_id).all()

# 문서 청크를 저장하는 함수
def add_document_chunk(db: Session, document_id: int, content: str, user_id: int, embedding=None):
    # 먼저 해당 document가 현재 사용자의 것인지 확인
    document = db.query(models.Document).filter(models.Document.id == document_id, models.Document.user_id == user_id).first()
    if not document:
        raise ValueError("Document not found or access denied")
    
    db_chunk = models.DocumentChunk(
        document_id=document_id,
        content=content,
        embedding=embedding
    )
    db.add(db_chunk)
    db.commit()
    db.refresh(db_chunk)
    return db_chunk

# 문서의 id로 문서 청크를 가져오는 함수
def get_document_chunks_by_document_id(db: Session, document_id: int, user_id: int):
    # 먼저 해당 document가 현재 사용자의 것인지 확인
    document = db.query(models.Document).filter(models.Document.id == document_id, models.Document.user_id == user_id).first()
    if not document:
        return []  # 접근 권한이 없으면 빈 리스트 반환
    
    return db.query(models.DocumentChunk).filter(models.DocumentChunk.document_id == document_id).all()

# 디렉토리 관련 CRUD
def create_directory(db: Session, id: str, name: str, path: str, is_directory: bool, parent_id: str, created_at: datetime, owner_id: int):
    """새로운 정보로, directories 테이블에 새로운 레코드를 생성하고, 생성한 그 레코드를 반환한다."""
    
    db_directory = models.Directory(
        id=id, 
        name=name, 
        path=path, 
        is_directory=is_directory, 
        parent_id=parent_id,
        created_at=created_at,
        owner_id=owner_id
    )
    db.add(db_directory)
    db.commit()
    db.refresh(db_directory)
    return db_directory

# 디렉토리의 정보만 가져오는 함수
def get_only_directory(db: Session, user_id: int):
    """
    현재 접속한 유저가 가진 디렉토리인 정보만 리스트로 가져온다.
    """
    stmt = select(models.Directory).where(models.Directory.is_directory == True, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    directories = result.scalars().all()
    return [{"id": d.id, "name": d.name, "path": d.path, "parent_id": d.parent_id} for d in directories]

def get_parent_id_by_id(db: Session, item_id: str, user_id: int):
    """아이템의 id로 해당 아이템 레코드에서 parent_id 필드의 값을 가져온다."""
    stmt = select(models.Directory.parent_id).where(models.Directory.id == item_id, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    return result.scalar()



def get_directory_id_by_path(db: Session, path: str, user_id: int):
    """아이템의 경로 값으로 해당 아이템의 id값을 가져온다.
    현재 접속한 사용자의 db에서만 검색함.
    """
    stmt = select(models.Directory.id).where(models.Directory.path == path, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    return result.scalar()

# 특정 파일의 경로에 존재하는 부모 디렉토리의 아이디를 가져오는 함수
def get_parent_id_by_path(db: Session, path: str, user_id: int):
    """아이템의 경로 값으로 해당 아이템의 parent_id필드의 값을 가져온다."""
    stmt = select(models.Directory.parent_id).where(models.Directory.path == path, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    return result.scalar()

# s3_key로 파일 존재 여부 확인
def get_file_info_by_s3_key(db: Session, s3_key: str, user_id: int):
    return db.query(models.Document).filter(models.Document.s3_key == s3_key, models.Document.user_id == user_id).first()

# # 동일한 파일 이름이 존재하는 지 확인 수정 전
# def get_file_info_by_filename(db: Session, filename: str):
#     """Document 테이블에 filename이 존재하면 해당 레코드를 반환한다."""
#     return db.query(models.Document).filter(models.Document.filename == filename).first()

# 동일한 파일 이름이 존재하는 지 확인 수정 후
def get_file_info_by_filename(db: Session, filename: str, user_id: int):
    """Document 테이블에 filename이 존재하면 해당 레코드를 반환한다.
    현재 접속한 사용자의 db에서만 검색함.
    """
    stmt = select(models.Document).where(models.Document.filename == filename, models.Document.user_id == user_id)
    result = db.execute(stmt).scalar_one_or_none()
    return result

# 동일한 디렉토리 이름이 존재하는 지 확인
def get_directory_info_by_name(db: Session, directory_name: str, user_id: int):
    """directories 테이블에 동일한 이름의 디렉토리가 있는지 확인한다."""
    return db.query(models.Directory).filter(models.Directory.name == directory_name, models.Directory.owner_id == user_id).first()

def delete_directory_by_id(db: Session, directory_id: str, user_id: int):
    """
    아이디를 기준으로 디렉토리를 삭제한다.
    """
    db.execute(
        text("DELETE FROM directories WHERE id = :dir_id AND owner_id = :user_id"),
        {"dir_id": directory_id, "user_id": user_id}
    )
    db.commit()

def delete_document_by_id(db: Session, document_id: int, user_id: int):
    """파일 id로 테이블에서 파일 정보를 document_chunks테이블, documents테이블, directories테이블 순으로 삭제한다."""
    try:
        # 먼저 해당 document가 현재 사용자의 것인지 확인
        document = db.query(models.Document).filter(models.Document.id == document_id, models.Document.user_id == user_id).first()
        if not document:
            raise ValueError("Document not found or access denied")
        
        # 개별 DELETE 문을 실행
        db.execute(
            text("DELETE FROM document_chunks WHERE document_id = :doc_id"),
            {"doc_id": document_id}
        )
        
        db.execute(
            text("DELETE FROM documents WHERE id = :doc_id AND user_id = :user_id"),
            {"doc_id": document_id, "user_id":user_id}
        )
        
        db.execute(
            text("DELETE FROM directories WHERE id = CAST(:doc_id AS TEXT) AND owner_id = :user_id"),
            {"doc_id": document_id, "owner_id":user_id}
        )
        
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        raise e

def get_s3_key_by_id(db: Session, document_id: any, user_id: int):
    """Documents 테이블에서 해당 아이템의 id로 s3_key 필드의 값을 가져온다."""
    if isinstance(document_id, str):
        document_id = int(document_id)
    return db.query(models.Document).filter(models.Document.id == document_id, models.Document.user_id == user_id).first().s3_key

def get_file_path_by_id(db: Session, item_id: any, user_id: int):
    """아이템의 id로 해당 아이템 레코드에서 path 필드의 값을 가져온다.
    접속중인 유저의 db에서만 검색하도록 수정*
    """
    if isinstance(item_id, int):
        item_id = str(item_id)
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first().path

def get_file_name_by_id(db: Session, item_id: any, user_id: int):
    """아이템의 id로 해당 아이템 레코드에서 name 필드의 값을 가져온다.
    접속중인 유저의 db에서만 검색하도록 수정*
    """
    if isinstance(item_id, int):
        item_id = str(item_id)    
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first().name

from sqlalchemy import select

def get_file_is_directory_by_id(db: Session, item_id: any, user_id: int):
    """아이템의 id로 해당 아이템 레코드에서 is_directory 필드의 값을 가져온다."""
    if isinstance(item_id, int):
        item_id = str(item_id)
    stmt = select(models.Directory.is_directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id)
    result = db.execute(stmt).scalar_one_or_none()
    return result


def update_directory_path_and_parent(db: Session, item_id: str, target_new_path: str, target_new_parent_id: str, user_id: int):
    """아이템의 id로 해당 아이템의 경로와 부모 ID를 업데이트한다."""
    from sqlalchemy import update
    stmt = update(models.Directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id).values(
        path=target_new_path,
        parent_id=target_new_parent_id
    )
    db.execute(stmt)
    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first()


# directories테이블에서 parent_id필드 값이 target의 id값인 레코드를 선택하여 가져온다.
def get_directory_by_parent_id(db: Session, item_id: any, user_id: int):
    """directories테이블의 parent_id필드 값 == item_id인 레코드를 선택하여 가져온다."""
    if isinstance(item_id, int):
        item_id = str(item_id)
    stmt = select(models.Directory).where(models.Directory.parent_id == item_id, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    return result.scalars().all()


def update_directory_with_sql_file_safe(db: Session, item_id: str, target_item_path: str, target_new_path: str, target_new_parent_id: str, user_id: int):
    """SQL 스크립트를 개별 명령으로 분리하여 실행하여 디렉토리 정보를 업데이트한다. (추가 설명 필요.)"""
    from sqlalchemy import text
    
    # 트랜잭션 시작
    db.execute(text("BEGIN;"))
    
    # 1. 부모 ID 업데이트
    update_parent_sql = """
    UPDATE directories
    SET parent_id = :new_parent_id
    WHERE id = :tgt_id
    AND owner_id = :user_id;
    """
    db.execute(
        text(update_parent_sql),
        {"tgt_id": item_id, "new_parent_id": target_new_parent_id, "user_id":user_id}
    )
    
    # 2. 경로 업데이트를 위한 재귀 쿼리
    update_path_sql = """
WITH RECURSIVE subtree AS (
    SELECT id, path
    FROM directories
    WHERE id = :tgt_id
    AND owner_id = :user_id
    UNION ALL
    SELECT d.id, d.path
    FROM directories d
    JOIN subtree s ON d.parent_id = s.id
    WHERE d.owner_id = :user_id
)
UPDATE directories AS d
SET path = regexp_replace(d.path,
                         '^' || :old_prefix,
                         :new_prefix)
FROM subtree
WHERE d.id = subtree.id
AND d.owner_id = :user_id;
"""
    db.execute(
        text(update_path_sql),
        {"tgt_id": item_id, "old_prefix": target_item_path, "new_prefix": target_new_path, "user_id": user_id}
    )
    
    # 트랜잭션 커밋
    db.commit()
    
    # # 선택적으로 확인용 SELECT 쿼리 실행
    # check_sql = """
    # SELECT id, path, parent_id
    # FROM directories
    # WHERE path LIKE :new_prefix || '%';
    # """
    # result = db.execute(
    #     text(check_sql),
    #     {"new_prefix": target_new_path}
    # )
    
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first()


def update_item_name_and_path(db: Session, item_id: any, new_name: str, new_path: str, user_id: int):
    """아이템의 id로 해당 아이템의 이름과 경로를 업데이트한다."""
    from sqlalchemy import update
    if isinstance(item_id, int):
        item_id = str(item_id)    
    stmt = update(models.Directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id).values(
        name=new_name,
        path=new_path
    )
    db.execute(stmt)
    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first()

def update_item_path_and_parent_id(db: Session, item_id: any, new_path: str, new_parent_id: str, user_id: int):
    """아이템의 id로 해당 아이템의 경로와 parent_id를 업데이트한다."""
    from sqlalchemy import update
    if isinstance(item_id, int):
        item_id = str(item_id)    
    stmt = update(models.Directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id).values(
        path=new_path,
        parent_id=new_parent_id
    )
    db.execute(stmt)
    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first()


# target 디렉토리 id의 값으로 해당 디렉토리의 자식 중 파일의 id만 모두 가져와서 리스트로 반환하는 함수.
def get_file_ids_by_parent_directory_id(db: Session, directory_id: any, user_id: int):
    """parent_id == directory_id인 레코드의 id 값을 모두 가져와서 리스트로 반환."""
    if isinstance(directory_id, int):
        directory_id = str(directory_id)
    stmt = select(models.Directory.id).where(models.Directory.parent_id == directory_id, models.Directory.owner_id == user_id)
    result = db.execute(stmt)
    return result.scalars().all()


def get_child_file_ids(db: Session, target_directory_id: str, user_id: int):
    """
    특정 디렉토리의 모든 하위(재귀) 중 파일(is_directory=False)인 레코드의 id만 리스트로 반환
    """
    from sqlalchemy import text

    sql = """
    WITH RECURSIVE subtree AS (
        SELECT id, is_directory
        FROM directories
        WHERE parent_id = :parent_id
        AND owner_id = :user_id
        UNION ALL
        SELECT d.id, d.is_directory
        FROM directories d
        JOIN subtree s ON d.parent_id = s.id
        WHERE d.owner_id = :user_id
    )
    SELECT id
    FROM subtree
    WHERE is_directory = FALSE;
    """

    result = db.execute(text(sql), {"parent_id": target_directory_id, "user_id": user_id})
    return [row[0] for row in result.fetchall()]


def get_child_directory_ids(db: Session, target_directory_id: str, user_id: int):
    """
    특정 디렉토리의 모든 하위(재귀) 중 디렉토리(is_directory=True)인 레코드의 id만 리스트로 반환
    """
    from sqlalchemy import text

    sql = """
    WITH RECURSIVE subtree AS (
        SELECT id, is_directory
        FROM directories
        WHERE parent_id = :parent_id
        AND owner_id = :user_id
        UNION ALL
        SELECT d.id, d.is_directory
        FROM directories d
        JOIN subtree s ON d.parent_id = s.id
        WHERE d.owner_id = :user_id
    )
    SELECT id
    FROM subtree
    WHERE is_directory = TRUE;
    """

    result = db.execute(text(sql), {"parent_id": target_directory_id, "user_id": user_id})
    return [row[0] for row in result.fetchall()]


def update_directory_and_child_dirs(
    db: Session,
    target_id: str,
    old_path: str,
    new_name: str,
    new_path: str,
    user_id: int
):
    """
    target 디렉토리의 이름과 경로를 변경하고,
    target의 자식들 중 is_directory=True인 레코드들의 path를 새로운 이름/경로에 맞춰 일괄 변경
    """
    from sqlalchemy import text

    # 1. target의 이름(name)과 경로(path) 변경
    update_target_sql = """
    UPDATE directories
    SET name = :new_name, path = :new_path
    WHERE id = :target_id
    AND owner_id = :user_id;
    """
    db.execute(
        text(update_target_sql),
        {"new_name": new_name, "new_path": new_path, "target_id": target_id, "owner_id":user_id}
    )

    # 2. 자식 디렉토리들(is_directory=True)의 path 일괄 변경 (재귀)
    update_children_sql = """
    WITH RECURSIVE child_dirs AS (
        SELECT id, path
        FROM directories
        WHERE parent_id = :target_id AND is_directory = TRUE
        AND owner_id = :user_id
        UNION ALL
        SELECT d.id, d.path
        FROM directories d
        JOIN child_dirs c ON d.parent_id = c.id
        WHERE d.is_directory = TRUE
        AND d.owner_id = :user_id
    )
    UPDATE directories AS d
    SET path = regexp_replace(d.path, '^' || :old_path, :new_path)
    FROM child_dirs
    WHERE d.id = child_dirs.id;
    """
    db.execute(
        text(update_children_sql),
        {"target_id": target_id, "old_path": old_path, "new_path": new_path, "user_id": user_id}
    )

    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == target_id, models.Directory.owner_id == user_id).first()



def update_directory_name_path_parent_id(db: Session, item_id: any, new_name: str, new_path: str, new_parent_id: str, user_id: int):
    """아이템의 id로 해당 아이템의 이름, 경로, 부모id를 업데이트한다."""
    from sqlalchemy import update
    if isinstance(item_id, int):
        item_id = str(item_id)
    stmt = update(models.Directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id).values(
        name=new_name, path=new_path, parent_id=new_parent_id)
    db.execute(stmt)
    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == item_id, models.Directory.owner_id == user_id).first()

def update_target_directory_path_parent_id_and_child_dirs(
    db: Session,
    target_id: str,
    parent_path_of_target: str,
    user_id: int
):
    '''
    target 디렉토리의 자식들 중 is_directory=True인 레코드들의 path 값을 일괄 변경
    자식의 기존 path값에서 parent_path_of_target 값을 빈 문자열로 교체
    '''
    from sqlalchemy import text

    # 자식 디렉토리들(is_directory=True)의 path 일괄 변경 (재귀)
    update_children_sql = '''
    WITH RECURSIVE child_dirs AS (
        SELECT id, path
        FROM directories
        WHERE parent_id = :target_id AND is_directory = TRUE
        AND owner_id = :user_id
        UNION ALL
        SELECT d.id, d.path
        FROM directories d
        JOIN child_dirs c ON d.parent_id = c.id
        WHERE d.is_directory = TRUE
        AND d.owner_id = :user_id
    )
    UPDATE directories AS d
    SET path = regexp_replace(d.path, '^' || :parent_path_of_target, '')
    FROM child_dirs
    WHERE d.id = child_dirs.id;
    '''
    db.execute(
        text(update_children_sql),
        {'target_id': target_id, 'parent_path_of_target': parent_path_of_target, 'user_id': user_id}
    )

    db.commit()
    return db.query(models.Directory).filter(models.Directory.id == target_id, models.Directory.owner_id == user_id).first()

def get_directory_by_id(db: Session, item_id: any, user_id: int):
    """아이템의 id로 해당 아이템 레코드를 가져온다."""
    if isinstance(item_id, int):
        item_id = str(item_id)
    
    if item_id == "root":
        # 가져오는 아이템이 root 디렉토리인 경우, root 디렉토리는 모든 유저가 공유하는 디렉토리이므로 유저 정보 없이 검색.
        stmt = select(models.Directory).where(models.Directory.id == item_id)
    else:
        stmt = select(models.Directory).where(models.Directory.id == item_id, models.Directory.owner_id == user_id)

    result = db.execute(stmt).scalar_one_or_none()
    return result    
