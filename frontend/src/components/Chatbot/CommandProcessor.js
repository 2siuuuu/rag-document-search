// ===== 수정된 CommandProcessor.js (백엔드 우선, 폴백 제거) =====

// ===== 기존 상수들 유지 및 확장 =====
export const COMMAND_TYPES = {
  DOCUMENT_SEARCH: 'DOCUMENT_SEARCH',
  MOVE_DOCUMENT: 'MOVE_DOCUMENT',
  COPY_DOCUMENT: 'COPY_DOCUMENT',
  DELETE_DOCUMENT: 'DELETE_DOCUMENT',
  CREATE_FOLDER: 'CREATE_FOLDER',
  SUMMARIZE_DOCUMENT: 'SUMMARIZE_DOCUMENT',
  RENAME_DOCUMENT: 'RENAME_DOCUMENT',
  UNDO: 'UNDO',
  UNKNOWN: 'UNKNOWN'
};

export const OPERATION_TYPES = {
  MOVE: 'move',
  COPY: 'copy',
  DELETE: 'delete',
  RENAME: 'rename',
  CREATE_FOLDER: 'create_folder',
  SEARCH: 'search',
  SUMMARIZE: 'summarize'
};

export const RISK_LEVELS = {
  LOW: 'low',
  MEDIUM: 'medium',
  HIGH: 'high',
  CRITICAL: 'critical'
};

// ===== 디버깅 헬퍼 함수들 =====
const debugLog = (category, message, data = null) => {
  const timestamp = new Date().toISOString();
  const logStyle = {
    stage: 'background: #007bff; color: white; padding: 2px 5px; border-radius: 3px;',
    execute: 'background: #28a745; color: white; padding: 2px 5px; border-radius: 3px;',
    cancel: 'background: #ffc107; color: black; padding: 2px 5px; border-radius: 3px;',
    undo: 'background: #dc3545; color: white; padding: 2px 5px; border-radius: 3px;',
    error: 'background: #dc3545; color: white; padding: 2px 5px; border-radius: 3px;',
    response: 'background: #6f42c1; color: white; padding: 2px 5px; border-radius: 3px;'
  };

  console.groupCollapsed(`%c🤖 [${category.toUpperCase()}] ${message}`, logStyle[category] || '');
  console.log('⏰ 시간:', timestamp);
  if (data) {
    console.log('📦 데이터:', data);
  }
  console.groupEnd();
};

const logNetworkRequest = (method, url, requestData) => {
  console.group(`%c🌐 네트워크 요청`, 'background: #17a2b8; color: white; padding: 2px 5px; border-radius: 3px;');
  console.log('🔗 Method:', method);
  console.log('🔗 URL:', url);
  console.log('📤 Request Headers:', {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${localStorage.getItem('token')?.substring(0, 20)}...`
  });
  console.log('📤 Request Body:', JSON.stringify(requestData, null, 2));
  console.groupEnd();
};

const logNetworkResponse = (url, responseData, status) => {
  const isSuccess = status >= 200 && status < 300;
  const style = isSuccess 
    ? 'background: #28a745; color: white; padding: 2px 5px; border-radius: 3px;'
    : 'background: #dc3545; color: white; padding: 2px 5px; border-radius: 3px;';
  
  console.group(`%c📡 네트워크 응답 ${status}`, style);
  console.log('🔗 URL:', url);
  console.log('📥 Response:', responseData);
  console.groupEnd();
};

// ===== API 서비스 클래스 (디버깅 추가) =====
class OperationService {
  constructor() {
    this.baseURL = process.env.REACT_APP_API_BASE_URL || "http://rag-alb-547296323.ap-northeast-2.elb.amazonaws.com/fast_api";
    debugLog('stage', '🔧 OperationService 초기화됨', { baseURL: this.baseURL });
  }

  async stageOperation(command, context) {
    const url = `${this.baseURL}/operations/stage`;
    const requestData = {
      command,
      context: {
        currentPath: context.currentPath,
        selectedFiles: context.selectedFiles,
        availableFolders: context.availableFolders,
        timestamp: new Date().toISOString()
      }
    };

    debugLog('stage', '📋 작업 준비 요청 시작', { command, context });
    logNetworkRequest('POST', url, requestData);

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        },
        body: JSON.stringify(requestData)
      });

      const responseData = await response.json();
      logNetworkResponse(url, responseData, response.status);

      if (!response.ok) {
        debugLog('error', '❌ Stage 요청 실패', { 
          status: response.status, 
          statusText: response.statusText,
          response: responseData 
        });
        throw new Error(`작업 준비 실패: ${response.statusText}`);
      }

      debugLog('stage', '✅ 작업 준비 성공', responseData);
      return responseData;

    } catch (error) {
      debugLog('error', '💥 Stage 네트워크 오류', { 
        error: error.message,
        url,
        requestData 
      });
      throw error;
    }
  }

  async executeOperation(operationId, userConfirmation = {}) {
    const url = `${this.baseURL}/operations/${operationId}/execute`;
    const requestData = {
      confirmed: true,
      userOptions: userConfirmation,
      executionTime: new Date().toISOString()
    };

    debugLog('execute', '🚀 작업 실행 요청 시작', { operationId, userConfirmation });
    logNetworkRequest('POST', url, requestData);

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        },
        body: JSON.stringify(requestData)
      });

      const responseData = await response.json();
      logNetworkResponse(url, responseData, response.status);

      if (!response.ok) {
        debugLog('error', '❌ Execute 요청 실패', { 
          operationId,
          status: response.status, 
          statusText: response.statusText,
          response: responseData 
        });
        throw new Error(`작업 실행 실패: ${response.statusText}`);
      }

      debugLog('execute', '✅ 작업 실행 성공', responseData);
      return responseData;

    } catch (error) {
      debugLog('error', '💥 Execute 네트워크 오류', { 
        operationId,
        error: error.message,
        url,
        requestData 
      });
      throw error;
    }
  }

  async cancelOperation(operationId) {
    const url = `${this.baseURL}/operations/${operationId}/cancel`;

    debugLog('cancel', '⏹️ 작업 취소 요청 시작', { operationId });
    logNetworkRequest('POST', url, {});

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        }
      });

      const responseData = await response.json();
      logNetworkResponse(url, responseData, response.status);

      if (!response.ok) {
        debugLog('error', '❌ Cancel 요청 실패', { 
          operationId,
          status: response.status, 
          statusText: response.statusText,
          response: responseData 
        });
        throw new Error(`작업 취소 실패: ${response.statusText}`);
      }

      debugLog('cancel', '✅ 작업 취소 성공', responseData);
      return responseData;

    } catch (error) {
      debugLog('error', '💥 Cancel 네트워크 오류', { 
        operationId,
        error: error.message,
        url 
      });
      throw error;
    }
  }

  async undoOperation(operationId, reason = '') {
    const url = `${this.baseURL}/operations/${operationId}/undo`;
    const requestData = {
      reason,
      undoTime: new Date().toISOString()
    };

    debugLog('undo', '↩️ 작업 되돌리기 요청 시작', { operationId, reason });
    logNetworkRequest('POST', url, requestData);

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`
        },
        body: JSON.stringify(requestData)
      });

      const responseData = await response.json();
      logNetworkResponse(url, responseData, response.status);

      if (!response.ok) {
        debugLog('error', '❌ Undo 요청 실패', { 
          operationId,
          status: response.status, 
          statusText: response.statusText,
          response: responseData 
        });
        throw new Error(`작업 취소 실패: ${response.statusText}`);
      }

      debugLog('undo', '✅ 작업 되돌리기 성공', responseData);
      return responseData;

    } catch (error) {
      debugLog('error', '💥 Undo 네트워크 오류', { 
        operationId,
        error: error.message,
        url,
        requestData 
      });
      throw error;
    }
  }
}

// ===== 기존 패턴들 유지 (호환성용, 실제로는 사용되지 않음) =====
const PATTERNS = {
  SEARCH: [
    /찾아/i, /검색/i, /어디에?\s*있(어|나|습니까)/i, 
    /위치/i, /경로/i, /어디\s*있/i
  ],
  MOVE: [
    /이동/i, /(옮기|옮겨)/i, /위치\s*변경/i, /경로\s*변경/i,
    /(.*)(을|를|파일|문서|폴더)?\s*(.*)(으로|로)?\s*이동/i
  ],
  COPY: [
    /복사/i, /복제/i, /사본/i, /카피/i,
    /(.*)(을|를|파일|문서)?\s*(.*)(으로|로)?\s*복사/i
  ],
  DELETE: [
    /삭제/i, /제거/i, /지우/i, /없애/i, /휴지통/i,
    /(.*)(을|를|파일|문서)?\s*삭제/i
  ],
  CREATE_FOLDER: [
    /폴더\s*(를|을)?\s*(만들|생성|추가)/i, /(디렉토리|디렉터리)\s*(를|을)?\s*(만들|생성|추가)/i,
    /(새|신규)\s*폴더/i, /(.*)(에|위치에|경로에)?\s*폴더\s*(를|을)?\s*(만들|생성|추가)/i
  ],
  SUMMARIZE: [
    /요약/i, /줄이/i, /정리/i, /핵심/i, /중요\s*내용/i,
    /(.*)(을|를|파일|문서)?\s*요약/i
  ],
  RENAME: [
    /이름\s*변경/i, /이름\s*바꾸/i, /바꿔/i, /rename/i,
    /(.*)(을|를|파일|문서)?\s*(.*)(으로|로)?\s*(이름\s*변경|바꿔)/i
  ],
  UNDO: [
    /되돌리/i, /취소/i, /원래대로/i, /방금.*되돌리/i, /실행.*취소/i, /undo/i
  ]
};

// ===== extractors (호환성용, 실제로는 사용되지 않음) =====
const extractors = {
  extractFileName: (message, selectedFiles = []) => {
    debugLog('stage', '⚠️ extractFileName 호출됨 (사용되지 않음)', { message, selectedFiles });
    return null;
  },
  
  extractPath: (message, currentPath = '/') => {
    debugLog('stage', '⚠️ extractPath 호출됨 (사용되지 않음)', { message, currentPath });
    return currentPath;
  },
  
  extractNewFolderName: (message) => {
    debugLog('stage', '⚠️ extractNewFolderName 호출됨 (사용되지 않음)', { message });
    return null;
  },

  extractNewName: (message) => {
    debugLog('stage', '⚠️ extractNewName 호출됨 (사용되지 않음)', { message });
    return null;
  },

  getTargetFiles: (message, selectedFiles = [], allFiles = []) => {
    debugLog('stage', '⚠️ getTargetFiles 호출됨 (사용되지 않음)', { message, selectedFilesCount: selectedFiles.length });
    return [];
  }
};

// ===== 수정된 CommandProcessor (백엔드 우선, 폴백 제거) =====
export const CommandProcessor = {
  operationService: new OperationService(),

  // 수정된 분석 함수 (백엔드 연동, 폴백 제거)
  analyzeCommand: async function(message, context) {
    debugLog('stage', '🧠 백엔드 명령어 분석 시작', { message, context });
    
    try {
      // 백엔드에서 명령 분석 및 작업 준비
      const stagedOperation = await this.operationService.stageOperation(message, context);
      
      const result = {
        success: true,
        operationId: stagedOperation.operationId,
        operation: stagedOperation.operation,
        requiresConfirmation: stagedOperation.requiresConfirmation,
        riskLevel: stagedOperation.riskLevel,
        preview: stagedOperation.preview
      };

      debugLog('stage', '✅ 백엔드 명령 분석 성공', result);
      return result;

    } catch (error) {
      debugLog('error', '❌ 백엔드 명령 분석 실패', { 
        error: error.message,
        message,
        context 
      });
      
      // 백엔드 실패 시 에러를 다시 throw (폴백 사용 안함)
      throw new Error(`백엔드 분석 실패: ${error.message}`);
    }
  },

  // 작업 실행
  executeOperation: async function(operationId, userConfirmation) {
    debugLog('execute', '🚀 작업 실행 시작', { operationId, userConfirmation });
    
    try {
      const result = await this.operationService.executeOperation(operationId, userConfirmation);
      
      const successResult = {
        success: true,
        result
      };
      
      debugLog('execute', '✅ 작업 실행 성공', successResult);
      return successResult;

    } catch (error) {
      const errorResult = {
        success: false,
        error: error.message
      };
      
      debugLog('error', '❌ 작업 실행 실패', errorResult);
      return errorResult;
    }
  },

  // 작업 취소
  cancelOperation: async function(operationId) {
    debugLog('cancel', '⏹️ 작업 취소 시작', { operationId });
    
    try {
      const result = await this.operationService.cancelOperation(operationId);
      
      const successResult = {
        success: true,
        result
      };
      
      debugLog('cancel', '✅ 작업 취소 성공', successResult);
      return successResult;

    } catch (error) {
      const errorResult = {
        success: false,
        error: error.message
      };
      
      debugLog('error', '❌ 작업 취소 실패', errorResult);
      return errorResult;
    }
  },

  // 작업 되돌리기
  undoOperation: async function(operationId, reason) {
    debugLog('undo', '↩️ 작업 되돌리기 시작', { operationId, reason });
    
    try {
      const result = await this.operationService.undoOperation(operationId, reason);
      
      const successResult = {
        success: true,
        result
      };
      
      debugLog('undo', '✅ 작업 되돌리기 성공', successResult);
      return successResult;

    } catch (error) {
      const errorResult = {
        success: false,
        error: error.message
      };
      
      debugLog('error', '❌ 작업 되돌리기 실패', errorResult);
      return errorResult;
    }
  },

  // ===== 복원된 processMessage 함수 (로컬 폴백용) =====
  processMessage: function(message, files = [], directories = [], context = {}) {
    debugLog('stage', '🔄 로컬 명령 분석 시작 (폴백 모드)', { 
      message, 
      fileCount: files.length, 
      dirCount: directories.length,
      context 
    });
    
    const { currentPath = '/', selectedFiles = [] } = context;
    const lowerMsg = message.toLowerCase();
    let commandType = COMMAND_TYPES.UNKNOWN;
    
    // 되돌리기 명령 체크
    if (PATTERNS.UNDO && PATTERNS.UNDO.some(pattern => pattern.test(lowerMsg))) {
      const result = {
        type: COMMAND_TYPES.UNDO,
        success: true,
        message: '최근 작업을 되돌리시겠습니까?'
      };
      debugLog('stage', '✅ 되돌리기 명령 인식', result);
      return result;
    }
    
    // 기존 패턴 매칭 로직
    if (PATTERNS.SEARCH.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.DOCUMENT_SEARCH;
    } else if (PATTERNS.MOVE.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.MOVE_DOCUMENT;
    } else if (PATTERNS.COPY.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.COPY_DOCUMENT;
    } else if (PATTERNS.DELETE.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.DELETE_DOCUMENT;
    } else if (PATTERNS.CREATE_FOLDER.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.CREATE_FOLDER;
    } else if (PATTERNS.SUMMARIZE.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.SUMMARIZE_DOCUMENT;
    } else if (PATTERNS.RENAME && PATTERNS.RENAME.some(pattern => pattern.test(lowerMsg))) {
      commandType = COMMAND_TYPES.RENAME_DOCUMENT;
    }
    
    debugLog('stage', '🎯 명령 타입 인식 (로컬)', { commandType });

    // 실제 extractors 함수들 복원
    const localExtractors = {
      extractFileName: (message, selectedFiles = []) => {
        // "선택된 파일" "이 파일" "선택한 문서" 등의 표현 처리
        const selectedPatterns = [
          /선택(된|한)\s*(파일|문서|항목)(들?)/i,
          /(이|그)\s*(파일|문서)(들?)/i,
          /현재\s*선택(된|한)/i,
          /지금\s*선택(된|한)/i
        ];
        
        for (const pattern of selectedPatterns) {
          if (pattern.test(message) && selectedFiles.length > 0) {
            return selectedFiles[0].name;
          }
        }
        
        const quotedMatch = message.match(/"([^"]+)"|'([^']+)'/);
        if (quotedMatch) {
          return quotedMatch[1] || quotedMatch[2];
        }
        
        const extensionMatch = message.match(/\b[\w\s-]+\.(pdf|docx?|xlsx?|pptx?|txt|jpg|png|hwp|zip)\b/i);
        if (extensionMatch) {
          return extensionMatch[0];
        }
        
        return null;
      },
      
      extractPath: (message, currentPath = '/') => {
        // "여기에" "현재 위치에" "이 폴더에" 등의 표현 처리
        const herePatterns = [
          /여기(에|로|서)/i,
          /현재\s*(위치|폴더|경로)(에|로)/i,
          /이\s*(위치|폴더|경로)(에|로)/i,
          /지금\s*(여기|위치)(에|로)/i
        ];
        
        for (const pattern of herePatterns) {
          if (pattern.test(message)) {
            return currentPath;
          }
        }
        
        const commonFolders = ['문서', '사진', '다운로드', '음악', '비디오', '프로젝트', '재무', '마케팅', '인사', '개인', '아카이브', '백업', '사과'];
        for (const folder of commonFolders) {
          if (message.includes(folder)) {
            return `/${folder}`;
          }
        }
        
        return currentPath;
      },
      
      getTargetFiles: (message, selectedFiles = [], allFiles = []) => {
        // "선택된" "이" "현재" 등의 표현이 있고 선택된 파일이 있으면 선택된 파일들 사용
        const selectedPatterns = [
          /선택(된|한)\s*(파일|문서|항목)(들?)/i,
          /(이|그)\s*(파일|문서)(들?)/i,
          /현재\s*선택(된|한)/i
        ];
        
        for (const pattern of selectedPatterns) {
          if (pattern.test(message) && selectedFiles.length > 0) {
            return selectedFiles;
          }
        }
        
        // 구체적인 파일명이 언급된 경우
        const fileName = localExtractors.extractFileName(message, selectedFiles);
        if (fileName) {
          const matchedFile = allFiles.find(file => 
            file.name.toLowerCase().includes(fileName.toLowerCase())
          );
          if (matchedFile) {
            return [matchedFile];
          }
        }
        
        // 아무것도 없으면 선택된 파일들 반환
        if (selectedFiles.length > 0) {
          return selectedFiles;
        }
        
        return [];
      }
    };

    // 명령 타입별 처리
    switch (commandType) {
      case COMMAND_TYPES.DOCUMENT_SEARCH: {
        const fileName = localExtractors.extractFileName(message, selectedFiles);
        const searchTerm = fileName || message.replace(/찾아|검색|어디에|있어|있나|위치|경로/g, '').trim();
        
        const searchResults = files
          .filter(file => {
            if (!searchTerm) return false;
            return file.name.toLowerCase().includes(searchTerm.toLowerCase());
          })
          .slice(0, 5);
        
        const result = {
          type: COMMAND_TYPES.DOCUMENT_SEARCH,
          query: searchTerm,
          results: searchResults,
          success: true,
          operation: {
            type: OPERATION_TYPES.SEARCH,
            searchTerm: searchTerm,
            requiresConfirmation: false
          }
        };
        
        debugLog('stage', '✅ 검색 명령 처리 완료 (로컬)', result);
        return result;
      }
      
      case COMMAND_TYPES.MOVE_DOCUMENT: {
        const targetFiles = localExtractors.getTargetFiles(message, selectedFiles, files);
        const targetPath = localExtractors.extractPath(message, currentPath);
        
        if (targetFiles.length === 0) {
          const errorResult = {
            type: COMMAND_TYPES.MOVE_DOCUMENT,
            success: false,
            error: '이동할 파일을 찾을 수 없습니다. 파일을 선택하거나 파일명을 명시해주세요.'
          };
          debugLog('error', '❌ 이동할 파일 없음 (로컬)', errorResult);
          return errorResult;
        }
        
        const result = {
          type: COMMAND_TYPES.MOVE_DOCUMENT,
          documents: targetFiles,
          targetPath: targetPath,
          previewAction: `${targetFiles.length}개 파일을 "${targetPath}" 경로로 이동합니다.`,
          success: true,
          operation: {
            type: OPERATION_TYPES.MOVE,
            targets: targetFiles,
            destination: targetPath,
            requiresConfirmation: true
          },
          riskLevel: RISK_LEVELS.MEDIUM,
          requiresConfirmation: true
        };
        
        debugLog('stage', '✅ 이동 명령 처리 완료 (로컬)', result);
        return result;
      }
      
      case COMMAND_TYPES.COPY_DOCUMENT: {
        const targetFiles = localExtractors.getTargetFiles(message, selectedFiles, files);
        const targetPath = localExtractors.extractPath(message, currentPath);
        
        if (targetFiles.length === 0) {
          const errorResult = {
            type: COMMAND_TYPES.COPY_DOCUMENT,
            success: false,
            error: '복사할 파일을 찾을 수 없습니다. 파일을 선택하거나 파일명을 명시해주세요.'
          };
          debugLog('error', '❌ 복사할 파일 없음 (로컬)', errorResult);
          return errorResult;
        }
        
        const result = {
          type: COMMAND_TYPES.COPY_DOCUMENT,
          documents: targetFiles,
          targetPath: targetPath,
          previewAction: `${targetFiles.length}개 파일을 "${targetPath}" 경로로 복사합니다.`,
          success: true,
          operation: {
            type: OPERATION_TYPES.COPY,
            targets: targetFiles,
            destination: targetPath,
            requiresConfirmation: true
          },
          riskLevel: RISK_LEVELS.LOW,
          requiresConfirmation: true
        };
        
        debugLog('stage', '✅ 복사 명령 처리 완료 (로컬)', result);
        return result;
      }
      
      case COMMAND_TYPES.DELETE_DOCUMENT: {
        const targetFiles = localExtractors.getTargetFiles(message, selectedFiles, files);
        
        if (targetFiles.length === 0) {
          const errorResult = {
            type: COMMAND_TYPES.DELETE_DOCUMENT,
            success: false,
            error: '삭제할 파일을 찾을 수 없습니다. 파일을 선택하거나 파일명을 명시해주세요.'
          };
          debugLog('error', '❌ 삭제할 파일 없음 (로컬)', errorResult);
          return errorResult;
        }
        
        const result = {
          type: COMMAND_TYPES.DELETE_DOCUMENT,
          documents: targetFiles,
          previewAction: `${targetFiles.length}개 파일을 삭제합니다.`,
          success: true,
          operation: {
            type: OPERATION_TYPES.DELETE,
            targets: targetFiles,
            requiresConfirmation: true
          },
          riskLevel: RISK_LEVELS.HIGH,
          requiresConfirmation: true
        };
        
        debugLog('stage', '✅ 삭제 명령 처리 완료 (로컬)', result);
        return result;
      }
      
      default:
        const unknownResult = {
          type: COMMAND_TYPES.UNKNOWN,
          success: false,
          error: '인식할 수 없는 명령입니다. "도움말"을 입력하여 사용 가능한 명령어를 확인해주세요.'
        };
        debugLog('stage', '❌ 알 수 없는 명령 (로컬)', unknownResult);
        return unknownResult;
    }
  },
  
  // 기존 findDocuments 함수 유지 (호환성)
  findDocuments: function(query, files) {
    debugLog('stage', '⚠️ findDocuments 호출됨 (사용되지 않음)', { query, fileCount: files.length });
    
    const results = files.filter(file => 
      file.name.toLowerCase().includes(query.toLowerCase())
    );
    
    return results;
  }
};