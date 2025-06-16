from rag.embeddings import get_embeddings


def save_to_vector_store(db, documents, file_name, file_path, user_id: int):
    """문서를 PostgreSQL 벡터 스토어에 저장합니다."""
    from db.database import SessionLocal
    from db import crud
    # import asyncio
    from debug import debugging
    
    
    try:
        # 임베딩 모델
        embeddings = get_embeddings()
        


        # 각 청크에 대해
        tasks = []
        for chunk in documents:
            # 해당 문서 찾기
            content = chunk.page_content
            
            
            # 문서 ID 찾기. 문서 이름이 db에 존재하면 해당 문서의 레코드 반환. # 더 효율적인 방법으로 수정하기.
            document = crud.get_file_info_by_filename(db, file_name, user_id)
            
            # 테스트 후 주석처리 하기
            if document:
                # 메타데이터에서 필요한 정보 추출
                metadata_text = f"<The name of this document>{file_name}</The name of this document> <The path of this document>{file_path}</The path of this document>"
                # 콘텐츠와 메타데이터 결합
                combined_text = f"{metadata_text} {content}"
                
                embedding_vector = embeddings.embed_query(combined_text)

                # DB에 저장
                crud.add_document_chunk(
                    db=db,
                    document_id=document.id,
                    content=content,
                    user_id=user_id,
                    embedding=embedding_vector
                )
                # 임베딩 비동기 처리
                # tasks.append(start_embedding(db, embeddings, combined_text, document, content))
                # await asyncio.gather(*tasks)
        print(f"총 {len(documents)}개의 청크가 PostgreSQL에 저장되었습니다.")
        return document.id
    except Exception as e:
        print(f"PostgreSQL 저장 오류: {str(e)}")
        raise e
    finally:
        db.close()

# async def start_embedding(db, embeddings, combined_text, document, content):
#     from db import crud

#     embedding_vector = embeddings.embed_query(combined_text)
    
#     # DB에 저장
#     crud.add_document_chunk(
#         db=db,
#         document_id=document.id,
#         content=content,
#         embedding=embedding_vector
#     )    


def manually_create_vector_extension(engine):
    """pgvector 익스텐션을 수동으로 생성합니다"""
    from sqlalchemy import text
    from config.settings import DATABASE_URL
    import os
    try:
        # 클라이언트 인코딩 옵션 추가
        modified_url = DATABASE_URL
        
        # Docker 컨테이너 환경에서는 'db'를 사용, 로컬 개발 환경에서는 'localhost'를 사용
        # getaddrinfo 오류 방지를 위한 조치
        if 'db:5432' in modified_url and not os.environ.get('DOCKER_ENV'):
            modified_url = modified_url.replace('db:5432', 'localhost:5432')
        
        if 'postgresql' in modified_url and not modified_url.startswith('postgresql+psycopg://'):
            modified_url = modified_url.replace('postgresql://', 'postgresql+psycopg://')
            
        if '?' in modified_url:
            modified_url = f"{modified_url}&client_encoding=utf8"
        else:
            modified_url = f"{modified_url}?client_encoding=utf8"
        
        print(f"Vector extension 연결 URL: {modified_url}")
                
        with engine.connect() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.commit()
        return True
    except Exception as e:
        print(f"수동 벡터 확장 생성 오류: {str(e)}")
        return False
    
# 디버깅 stop 시 다음 코드 강제 실행 불가하도록 하는 함수.
def stop_debugger():
    """q누르면 루프를 강제 종료한다."""
    while 1:
        # 키 입력 받기
        key = input("프로그램이 중단되었습니다. 끝내려면 'q', 계속하려면 'g'.")
        # q 키를 누르면 예외를 발생시켜 프로그램을 강제 종료
        if key.lower() == 'q':
            raise Exception("사용자에 의해 강제 종료되었습니다.")
        elif key.lower() == 'g':
            break