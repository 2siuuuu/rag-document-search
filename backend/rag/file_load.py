# 텍스트를 추출한다. 
# RAG 프로세스 중 문서 로드 단계.

import os
import tempfile
import subprocess
import re

from langchain_community.document_loaders import PyPDFLoader, TextLoader
import docx2txt

from io import BytesIO
from PyPDF2 import PdfReader
from debug import debugging

def clean_text(text):
    """텍스트에서 NULL 문자 및 기타 문제가 될 수 있는 특수 문자 제거"""
    # NULL 문자 제거
    text = text.replace('\x00', '')
    
    # 제어 문자 제거 (탭, 줄바꿈, 캐리지 리턴은 유지)
    text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    return text

async def load_pdf(file_content):
    """PDF 파일 사전 처리"""
    print("Loading PDF file...")

    try:

        # PyPDFLoader 대신 PyPDFReader 사용
        reader = PdfReader(BytesIO(file_content)) # 이부분의 <pdf_file>는 BytesIO(<load_pdf의 매개변수>)로 대체.
        documents = []
        for page in reader.pages:
            text = page.extract_text()
            text = clean_text(text)
            documents.append(text)

        print(f"Extracted {len(documents)} pages from PDF")

        return documents
    
    except Exception as e:
        print(f"Error loading PDF file: {str(e)}")
        raise



async def load_docx(docx_content):
    """Word 문서 처리"""
    print("Loading DOCX file...")
    
    # 임시 파일 생성
    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as temp:
        temp.write(docx_content)
        temp_path = temp.name
    
    try:
        # docx2txt 패키지를 사용하여 문서 로드
        text = docx2txt.process(temp_path)
        
        # 결과를 PDF와 같은 형식으로 변환
        documents = []
        text = clean_text(text)
        documents.append(text)
        
        print(f"추출된 Word 문서 내용: {len(documents)} 페이지")
        return documents
    except Exception as e:
        print(f"DOCX 파일 처리 중 오류 발생: {str(e)}")
        raise
    finally:
        # 임시 파일 삭제
        os.unlink(temp_path)





async def load_hwp(file_content, file_extension="hwp"):
    """HWP/HWPX 문서 처리"""
    print(f"Loading {file_extension.upper()} file...")
    
    # 임시 파일 생성 (HWP/HWPX 파일용)
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_extension}") as hwp_temp:
        hwp_temp.write(file_content)
        hwp_path = hwp_temp.name
    # debugging.stop_debugger()
    # 텍스트 출력용 임시 파일
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as txt_temp:
        txt_path = txt_temp.name
    # debugging.stop_debugger()
    try:
        print(f"Running hwp5txt on temporary file with output to {txt_path}")
        subprocess.run(['hwp5txt', hwp_path, '--output', txt_path], check=True)
        
        # 텍스트 파일 읽기 및 정리
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
        
        # 텍스트 정리
        text = clean_text(text)
        
        # 페이지 구분 (빈 줄이 여러 개 있는 곳을 페이지 구분자로 간주)
        # 일반적으로 HWP 문서는 페이지 구분이 명확하지 않아 휴리스틱하게 처리
        page_delimiter = re.compile(r'\n{3,}')  # 3줄 이상의 빈 줄을 페이지 구분자로 간주
        pages = page_delimiter.split(text)
        
        # 빈 페이지 제거 및 공백 정리
        documents = [page.strip() for page in pages if page.strip()]
        
        # 페이지 분할이 제대로 되지 않은 경우 (페이지가 하나만 있는 경우)
        if len(documents) <= 1:
            # 약 1000자 단위로 페이지 분할
            text = documents[0] if documents else text
            page_size = 1000
            documents = [text[i:i+page_size] for i in range(0, len(text), page_size)]
        
        print(f"HWP 문서를 {len(documents)}개의 페이지로 분할했습니다.")
        # debugging.stop_debugger()
        return documents
    except Exception as e:
        print(f"{file_extension.upper()} 파일 처리 중 오류 발생: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise
    finally:
        # 임시 파일 삭제
        if os.path.exists(hwp_path):
            os.unlink(hwp_path)
        if os.path.exists(txt_path):
            os.unlink(txt_path)
