// ===== 수정된 Chatbot.js (백엔드 우선, 폴백 제거) =====

import React, { useState, useEffect, useRef } from 'react';
import './Chatbot.css';
import ChatbotGuide from './ChatbotGuide';

// 새로 추가할 서비스들
import { CommandProcessor, OPERATION_TYPES } from './CommandProcessor';

// 새로 추가할 컴포넌트
import OperationPreviewModal from './OperationPreviewModal';

const Chatbot = ({ 
  isOpen, 
  toggleChatbot, 
  onQuery, 
  isQuerying,
  // 새로 추가되는 props들 (기존 App.js에서 전달)
  files = [],
  directories = [],
  selectedItems = [],
  currentPath = '/',
  onRefreshFiles,
  onShowNotification
}) => {
  const [messages, setMessages] = useState([
    { id: 1, text: '안녕하세요! 파일 관리 도우미입니다. 파일 검색, 이동, 복사, 삭제 등 다양한 작업을 도와드릴 수 있습니다. 명령어 예시가 필요하시면 "도움말"이라고 입력해보세요.', sender: 'bot' },
  ]);
  
  const [newMessage, setNewMessage] = useState('');
  const [showGuide, setShowGuide] = useState(false);
  
  // ===== 새로 추가되는 상태들 =====
  const [currentOperation, setCurrentOperation] = useState(null);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [isProcessingCommand, setIsProcessingCommand] = useState(false);
  const [recentOperations, setRecentOperations] = useState([]);
  
  const messagesEndRef = useRef(null);
  
  // 챗봇이 닫히면 가이드도 함께 닫기
  useEffect(() => {
    if (!isOpen) {
      setShowGuide(false);
    }
  }, [isOpen]);
  
  // 메시지 자동 스크롤
  useEffect(() => {
    scrollToBottom();
  }, [messages]);
  
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };
  
  // 메시지 추가 헬퍼 함수
  const addMessage = (text, sender = 'bot', data = null) => {
    const newMsg = {
      id: Date.now(),
      text,
      sender,
      timestamp: new Date(),
      data
    };
    setMessages(prev => [...prev, newMsg]);
    return newMsg;
  };
  
  const handleInputChange = (e) => {
    setNewMessage(e.target.value);
  };

  // 백엔드 응답에 따른 메시지 생성 함수
  const generateResponseText = (analysisResult, context) => {
    const { operation } = analysisResult;
    const { selectedFiles } = context;
    
    // 선택된 파일이 있을 때 더 구체적인 응답
    if (selectedFiles.length > 0) {
      const fileNames = selectedFiles.map(f => f.name).join(', ');
      
      switch (operation?.type) {
        case OPERATION_TYPES.MOVE:
          return `선택된 파일 "${fileNames}"을(를) "${operation.destination}" 경로로 이동하시겠습니까?`;
        case OPERATION_TYPES.COPY:
          return `선택된 파일 "${fileNames}"을(를) "${operation.destination}" 경로로 복사하시겠습니까?`;
        case OPERATION_TYPES.DELETE:
          return `선택된 파일 "${fileNames}"을(를) 삭제하시겠습니까?`;
        case OPERATION_TYPES.RENAME:
          return `선택된 파일 "${fileNames}"의 이름을 "${operation.newName}"으로 변경하시겠습니까?`;
        case OPERATION_TYPES.SUMMARIZE:
          return `선택된 ${selectedFiles.length}개 파일을 요약하시겠습니까?`;
        default:
          return `선택된 ${selectedFiles.length}개 파일에 대한 작업을 처리합니다.`;
      }
    } else {
      // 선택된 파일이 없을 때 기본 응답
      switch (operation?.type) {
        case OPERATION_TYPES.CREATE_FOLDER:
          return `현재 위치(${context.currentPath})에 "${operation.folderName}" 폴더를 생성하시겠습니까?`;
        case OPERATION_TYPES.SEARCH:
          return `"${operation.searchTerm}"에 대한 검색 결과입니다.`;
        case OPERATION_TYPES.MOVE:
          return `파일을 "${operation.destination}" 경로로 이동하시겠습니까?`;
        case OPERATION_TYPES.COPY:
          return `파일을 "${operation.destination}" 경로로 복사하시겠습니까?`;
        case OPERATION_TYPES.DELETE:
          return `파일을 삭제하시겠습니까?`;
        case OPERATION_TYPES.RENAME:
          return `파일 이름을 "${operation.newName}"으로 변경하시겠습니까?`;
        default:
          return '명령을 처리합니다.';
      }
    }
  };
  
  // ===== 수정된 handleSubmit 함수 =====
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (newMessage.trim() === '' || isQuerying || isProcessingCommand) return;
    
    // 사용자 메시지 추가
    addMessage(newMessage, 'user');
    const userCommand = newMessage;
    setNewMessage('');
    
    // 도움말 명령어 처리 (백엔드 요청 전에만 처리)
    if (userCommand.toLowerCase().includes('도움말') || 
        userCommand.toLowerCase().includes('도와줘') || 
        userCommand.toLowerCase().includes('명령어') || 
        userCommand.toLowerCase().includes('사용법')) {
      setShowGuide(true);
      addMessage('명령어 가이드를 표시합니다. 여기서 다양한 명령어 예시를 확인하실 수 있습니다.');
      return;
    }
    
    setIsProcessingCommand(true);
    
    try {
      // ===== 개선된 컨텍스트 정보 준비 =====
      const context = {
        currentPath,
        selectedFiles: selectedItems.map(id => files.find(f => f.id === id)).filter(Boolean),
        availableFolders: directories,
        allFiles: files
      };

      // 디버깅 로그 추가
      console.log('=== Chatbot 컨텍스트 정보 ===');
      console.log('현재 경로:', currentPath);
      console.log('선택된 파일 ID들:', selectedItems);
      console.log('선택된 파일 객체들:', context.selectedFiles);
      console.log('전체 파일 수:', files.length);
      console.log('사용자 명령:', userCommand);
      console.log('==========================');
      
      // 백엔드 요청 시도
      let analysisResult;
      const useBackendFallback = process.env.REACT_APP_USE_BACKEND_FALLBACK === 'true';
      
      try {
        analysisResult = await CommandProcessor.analyzeCommand(userCommand, context);
        
        // 백엔드 성공 시 폴백 코드 사용하지 않음
        if (analysisResult && analysisResult.success) {
          console.log('✅ 백엔드 처리 성공, 폴백 사용 안함');
          
          // 백엔드 결과로 응답 생성
          let responseText = generateResponseText(analysisResult, context);
          addMessage(responseText);
          
          // 확인이 필요한 작업인 경우 미리보기 모달 표시
          if (analysisResult.requiresConfirmation) {
            setCurrentOperation(analysisResult);
            setShowPreviewModal(true);
          } else {
            // 확인이 필요없는 작업은 바로 실행
            if (analysisResult.operationId) {
              await executeOperation(analysisResult.operationId, {});
            } else {
              // 로컬 처리 결과 표시 (검색 등)
              if (analysisResult.type === 'DOCUMENT_SEARCH') {
                if (analysisResult.results && analysisResult.results.length > 0) {
                  const resultText = `검색 결과 (${analysisResult.results.length}개):\n${analysisResult.results.map(file => `• ${file.name}`).join('\n')}`;
                  addMessage(resultText);
                } else {
                  addMessage('검색 결과가 없습니다.');
                }
              }
            }
          }
          
          return; // 백엔드 성공 시 여기서 종료
        }
      } catch (error) {
        console.error('❌ 백엔드 요청 실패:', error);
        
        // 백엔드 실패 시 폴백 처리 여부 확인
        if (useBackendFallback) {
          console.log('🔄 로컬 폴백 처리 시작');
          
          try {
            // 로컬 폴백 시도
            analysisResult = CommandProcessor.processMessage(userCommand, files, directories, context);
            
            if (analysisResult && analysisResult.success) {
              console.log('✅ 로컬 폴백 처리 성공');
              
              // 폴백 결과로 응답 생성
              let responseText = generateResponseText(analysisResult, context);
              addMessage(responseText + ' (로컬 처리)');
              
              // 확인이 필요한 작업인 경우 미리보기 모달 표시
              if (analysisResult.requiresConfirmation) {
                analysisResult.isLocalFallback = true; // 로컬 폴백임을 표시
                setCurrentOperation(analysisResult);
                setShowPreviewModal(true);
              } else {
                // 확인이 필요없는 작업은 바로 처리
                if (analysisResult.type === 'DOCUMENT_SEARCH') {
                  if (analysisResult.results && analysisResult.results.length > 0) {
                    const resultText = `검색 결과 (${analysisResult.results.length}개):\n${analysisResult.results.map(file => `• ${file.name}`).join('\n')}`;
                    addMessage(resultText);
                  } else {
                    addMessage('검색 결과가 없습니다.');
                  }
                }
              }
              
              return; // 로컬 폴백 성공 시 여기서 종료
            }
          } catch (fallbackError) {
            console.error('❌ 로컬 폴백도 실패:', fallbackError);
          }
        }
        
        // 백엔드 실패하고 폴백도 실패하거나 사용하지 않을 때 상태 확인 및 RAG 처리
        
        // 1. 상태 확인 명령인지 검사 (백엔드 실패 후에만)
        if (userCommand.toLowerCase().includes('상태') || 
            userCommand.toLowerCase().includes('현재') ||
            (userCommand.toLowerCase().includes('선택된') && 
             !userCommand.toLowerCase().includes('옮겨') &&
             !userCommand.toLowerCase().includes('이동') &&
             !userCommand.toLowerCase().includes('복사') &&
             !userCommand.toLowerCase().includes('삭제'))) {
          
          const statusMessage = `
현재 상태:
📂 경로: ${currentPath}
📄 전체 파일: ${files.length}개
✅ 선택된 파일: ${selectedItems.length}개
${selectedItems.length > 0 ? `\n선택된 파일들:\n${selectedItems.map(id => {
  const file = files.find(f => f.id === id);
  return file ? `• ${file.name}` : `• [ID:${id}]`;
}).join('\n')}` : ''}
          `;
          addMessage(statusMessage.trim());
          return;
        }
        
        // 2. 일반적인 RAG 질의로 처리 (백엔드 실패 후에만)
        console.log('🔄 RAG 질의로 폴백 처리');
        
        const typingMessage = {
          id: 'typing',
          text: '검색 중...',
          sender: 'bot',
          isTyping: true
        };
        
        setMessages(prev => [...prev, typingMessage]);
        
        try {
          const answer = await onQuery(userCommand);
          
          // 'typing' 메시지 제거
          setMessages(prev => prev.filter(msg => msg.id !== 'typing'));
          
          // 봇 응답 추가
          addMessage(answer || '죄송합니다, 답변을 찾을 수 없습니다.');
          
        } catch (ragError) {
          // RAG 오류 처리
          setMessages(prev => prev.filter(msg => msg.id !== 'typing'));
          addMessage('죄송합니다, 오류가 발생했습니다.');
        }
      }
      
    } catch (error) {
      console.error('명령 처리 중 오류:', error);
      addMessage('명령 처리 중 오류가 발생했습니다. 다시 시도해주세요.');
    } finally {
      setIsProcessingCommand(false);
    }
  };
  
  // ===== 새로 추가되는 작업 실행 함수들 =====
  
  const executeOperation = async (operationId, userOptions = {}) => {
    try {
      setIsProcessingCommand(true);
      
      const executionResult = await CommandProcessor.executeOperation(operationId, userOptions);
      
      if (executionResult.success) {
        const result = executionResult.result;
        
        // 성공 메시지 추가
        addMessage(result.message || '작업이 성공적으로 완료되었습니다.', 'bot', {
          operationId,
          canUndo: result.undoAvailable,
          undoDeadline: result.undoDeadline
        });

        // 실행된 작업 기록
        setRecentOperations(prev => [
          {
            id: operationId,
            timestamp: new Date(),
            canUndo: result.undoAvailable,
            undoDeadline: result.undoDeadline,
            description: result.message
          },
          ...prev.slice(0, 4) // 최근 5개만 유지
        ]);

        // FileDisplay 새로고침
        if (onRefreshFiles) {
          onRefreshFiles();
        }

        // 성공 알림
        if (onShowNotification) {
          onShowNotification(result.message || '작업이 완료되었습니다.');
        }

        // 되돌리기 가능한 작업의 경우 안내 메시지
        if (result.undoAvailable) {
          setTimeout(() => {
            addMessage(
              `💡 이 작업은 ${new Date(result.undoDeadline).toLocaleTimeString()}까지 되돌릴 수 있습니다. "방금 작업 되돌리기"라고 말씀해보세요.`, 
              'bot'
            );
          }, 1000);
        }

      } else {
        addMessage(`작업 실행 실패: ${executionResult.error}`);
      }

    } catch (error) {
      console.error('작업 실행 오류:', error);
      addMessage('작업 실행 중 오류가 발생했습니다.');
    } finally {
      setIsProcessingCommand(false);
    }
  };

  // 미리보기 모달 확인
  const handlePreviewConfirm = async (userOptions) => {
    if (!currentOperation) return;

    setShowPreviewModal(false);
    addMessage('작업을 실행합니다...');

    if (currentOperation.operationId) {
      // 백엔드 작업 실행
      await executeOperation(currentOperation.operationId, userOptions);
    } else if (currentOperation.isLocalFallback) {
      // 로컬 폴백 작업 실행
      try {
        await handleLocalOperation(currentOperation, userOptions);
      } catch (error) {
        console.error('로컬 작업 실행 오류:', error);
        addMessage('작업 실행 중 오류가 발생했습니다.');
      }
    } else {
      // 기타 로컬 작업 처리
      try {
        await handleLocalOperation(currentOperation, userOptions);
      } catch (error) {
        console.error('로컬 작업 실행 오류:', error);
        addMessage('작업 실행 중 오류가 발생했습니다.');
      }
    }
    
    setCurrentOperation(null);
  };

  // 로컬 작업 처리 함수 (실제 App.js 핸들러 연동 필요)
  const handleLocalOperation = async (operation, userOptions) => {
    // 실제 구현에서는 App.js의 핸들러 함수들을 호출해야 함
    // 현재는 시뮬레이션만 수행
    
    const { operation: op } = operation;
    
    switch (op?.type) {
      case OPERATION_TYPES.MOVE:
        addMessage(`로컬 작업 완료: ${op.targets.length}개 파일을 "${op.destination}" 경로로 이동했습니다.`);
        break;
      case OPERATION_TYPES.COPY:
        addMessage(`로컬 작업 완료: ${op.targets.length}개 파일을 "${op.destination}" 경로로 복사했습니다.`);
        break;
      case OPERATION_TYPES.DELETE:
        addMessage(`로컬 작업 완료: ${op.targets.length}개 파일을 삭제했습니다.`);
        break;
      default:
        addMessage(`로컬 작업 완료: ${operation.previewAction}`);
    }
    
    if (onRefreshFiles) {
      onRefreshFiles();
    }
    
    if (onShowNotification) {
      onShowNotification('작업이 완료되었습니다. (로컬 처리)');
    }
  };

  // 미리보기 모달 취소
  const handlePreviewCancel = async () => {
    if (!currentOperation) return;

    try {
      if (currentOperation.operationId) {
        await CommandProcessor.cancelOperation(currentOperation.operationId);
      }
      addMessage('작업이 취소되었습니다.');
    } catch (error) {
      addMessage('작업 취소 중 오류가 발생했습니다.');
    } finally {
      setShowPreviewModal(false);
      setCurrentOperation(null);
    }
  };

  // 작업 되돌리기
  const handleUndoOperation = async (operationId) => {
    try {
      setIsProcessingCommand(true);
      addMessage('작업을 되돌리고 있습니다...');

      const undoResult = await CommandProcessor.undoOperation(operationId, '사용자 요청');

      if (undoResult.success) {
        addMessage('작업이 성공적으로 되돌려졌습니다.');
        
        // 되돌린 작업을 기록에서 제거
        setRecentOperations(prev => prev.filter(op => op.id !== operationId));
        
        // FileDisplay 새로고침
        if (onRefreshFiles) {
          onRefreshFiles();
        }
      } else {
        addMessage(`작업 되돌리기 실패: ${undoResult.error}`);
      }

    } catch (error) {
      console.error('작업 되돌리기 오류:', error);
      addMessage('작업 되돌리기 중 오류가 발생했습니다.');
    } finally {
      setIsProcessingCommand(false);
    }
  };
  
  // ===== 기존 가이드 관련 함수들 유지 =====

  const handleGuideClose = () => {
    setShowGuide(false);
  };
  
  const handleTryExample = (exampleCommand) => {
    setNewMessage(exampleCommand);
    setShowGuide(false);
  };

  // ===== 메시지 렌더링 (되돌리기 버튼 포함) =====
  const renderMessage = (message) => {
    return (
      <div key={message.id} className={`message ${message.sender === 'bot' ? 'bot' : 'user'} ${message.isTyping ? 'typing' : ''}`}>
        <div className="message-content">{message.text}</div>
        {message.timestamp && (
          <div className="message-timestamp">
            {message.timestamp.toLocaleTimeString()}
          </div>
        )}
        
        {/* 되돌리기 가능한 작업에 대한 버튼 */}
        {message.data?.canUndo && new Date() < new Date(message.data.undoDeadline) && (
          <button 
            className="undo-btn"
            onClick={() => handleUndoOperation(message.data.operationId)}
            disabled={isProcessingCommand}
          >
            작업 되돌리기
          </button>
        )}
      </div>
    );
  };

  return (
    <div className="chatbot-wrapper">
      {/* 명령어 가이드 (기존 로직 유지) */}
      {isOpen && showGuide && (
        <div className="chatbot-guide-panel">
          <ChatbotGuide
            onClose={handleGuideClose}
            onTryExample={handleTryExample}
          />
        </div>
      )}
      
      {isOpen ? (
        <div className="enhanced-chatbot-container open">
          <div className="chatbot-header">
            <h3>🤖 스마트 파일 관리 도우미</h3>
            <div className="header-status">
              {selectedItems.length > 0 && (
                <span className="selected-count">{selectedItems.length}개 선택됨</span>
              )}
            </div>
            <button className="close-btn" onClick={toggleChatbot}>×</button>
          </div>
          
          <div className="chatbot-messages">
            {messages.map(renderMessage)}
            {(isQuerying || isProcessingCommand) && (
              <div className="message bot typing">
                <div className="typing-indicator">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          
          <form className="chatbot-input" onSubmit={handleSubmit}>
            <input
              type="text"
              placeholder="파일 작업을 자연어로 명령하거나 문서에 대해 질문해보세요..."
              value={newMessage}
              onChange={handleInputChange}
              disabled={isQuerying || isProcessingCommand}
            />
            <button 
              type="submit" 
              disabled={newMessage.trim() === '' || isQuerying || isProcessingCommand}
              className={(isQuerying || isProcessingCommand) ? 'loading' : ''}
            >
              {(isQuerying || isProcessingCommand) ? '처리 중...' : '전송'}
            </button>
          </form>

          {/* 최근 작업 되돌리기 패널 */}
          {recentOperations.filter(op => op.canUndo && new Date() < new Date(op.undoDeadline)).length > 0 && (
            <div className="recent-operations">
              <h4>되돌리기 가능한 작업:</h4>
              {recentOperations
                .filter(op => op.canUndo && new Date() < new Date(op.undoDeadline))
                .map(operation => (
                  <div key={operation.id} className="undo-item">
                    <span className="operation-desc">{operation.description}</span>
                    <button 
                      className="undo-btn-small"
                      onClick={() => handleUndoOperation(operation.id)}
                      disabled={isProcessingCommand}
                    >
                      되돌리기
                    </button>
                  </div>
                ))
              }
            </div>
          )}
        </div>
      ) : (
        <button className="chatbot-toggle" onClick={toggleChatbot}>
          🤖 파일 도우미
        </button>
      )}

      {/* 작업 미리보기 모달 */}
      <OperationPreviewModal
        operationData={currentOperation}
        onConfirm={handlePreviewConfirm}
        onCancel={handlePreviewCancel}
        onClose={() => setShowPreviewModal(false)}
        isVisible={showPreviewModal}
      />
    </div>
  );
};

export default Chatbot;