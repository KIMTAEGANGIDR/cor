"""
ILIS OCR Pipeline Database Schema

파이프라인 단계:
- STAGE 0: 헤더 추출 + 클러스터링
- STAGE 0.5: 패턴 분석 (Human-in-the-loop)
- STAGE 0.6: 패턴 검증 (샘플 테스트)
- STAGE 0.7: 심층 분석 (반려된 패턴)
- STAGE 1: 패턴 룰 적용
- STAGE 2: PaddleOCR 처리
- STAGE 3: Akoma Ntoso 변환
- STAGE 4: 검증
"""

SCHEMA_SQL = """
-- ============================================
-- 1. 문서 테이블 (peraturan.db에서 마이그레이션)
-- ============================================
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,                    -- slug (peraturan.db) 또는 id (bpk)
    source TEXT NOT NULL,                   -- 'peraturan.go.id' 또는 'bpk.go.id'

    -- 기본 메타데이터
    jenis TEXT NOT NULL,                    -- 법령 유형 (UU, PP, Perpres 등)
    nomor TEXT,                             -- 법령 번호
    tahun INTEGER,                          -- 연도
    tentang TEXT,                           -- 제목/주제

    -- PDF 정보
    pdf_path TEXT,                          -- 로컬 PDF 경로
    pdf_url TEXT,                           -- 원본 PDF URL
    page_count INTEGER,                     -- 총 페이지 수
    file_size INTEGER,                      -- 파일 크기 (bytes)

    -- 처리 상태
    stage TEXT DEFAULT 'pending',           -- 현재 단계
    priority INTEGER DEFAULT 0,             -- 처리 우선순위 (높을수록 먼저)

    -- 타임스탬프
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_jenis ON documents(jenis);
CREATE INDEX IF NOT EXISTS idx_documents_stage ON documents(stage);
CREATE INDEX IF NOT EXISTS idx_documents_priority ON documents(priority DESC);

-- ============================================
-- 2. 헤더 추출 테이블 (STAGE 0)
-- ============================================
CREATE TABLE IF NOT EXISTS headers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,

    -- 헤더 이미지
    image_path TEXT,                        -- 헤더 이미지 경로
    header_ratio REAL DEFAULT 0.3,          -- 추출 비율 (상단 30%)

    -- OCR 결과
    raw_text TEXT,                          -- 원본 OCR 텍스트
    lines TEXT,                             -- JSON: 라인별 텍스트 배열
    ocr_confidence REAL,                    -- OCR 신뢰도

    -- 클러스터링
    cluster_id INTEGER,                     -- 배정된 클러스터 ID

    -- 상태
    status TEXT DEFAULT 'pending',          -- pending, extracted, clustered, error
    error_message TEXT,

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_headers_document ON headers(document_id);
CREATE INDEX IF NOT EXISTS idx_headers_cluster ON headers(cluster_id);
CREATE INDEX IF NOT EXISTS idx_headers_status ON headers(status);

-- ============================================
-- 3. 클러스터 테이블 (STAGE 0)
-- ============================================
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 클러스터 정보
    name TEXT,                              -- 클러스터 이름 (예: PP_1945_style)
    description TEXT,                       -- 설명

    -- 통계
    document_count INTEGER DEFAULT 0,       -- 포함된 문서 수
    sample_documents TEXT,                  -- JSON: 샘플 문서 ID 목록

    -- 대표 패턴
    representative_text TEXT,               -- 대표 헤더 텍스트
    centroid_vector TEXT,                   -- JSON: TF-IDF 중심 벡터

    -- 상태
    status TEXT DEFAULT 'draft',            -- draft, pending_validation, approved,
                                           -- needs_revision, manual_review

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================================
-- 4. 패턴 룰 테이블 (STAGE 0.5)
-- ============================================
CREATE TABLE IF NOT EXISTS pattern_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,

    -- 라인별 분류
    noise_lines TEXT,                       -- JSON: 노이즈 라인 인덱스 [0, 5, ...]
    metadata_lines TEXT,                    -- JSON: 메타데이터 라인 인덱스
    body_start_line INTEGER,                -- 본문 시작 라인

    -- 메타데이터 매핑
    metadata_mapping TEXT,                  -- JSON: {"1": "law_type", "2": "law_number"}

    -- 추가 패턴 (정규식)
    noise_patterns TEXT,                    -- JSON: 추가 노이즈 정규식 패턴
    header_regex TEXT,                      -- 헤더 매칭 정규식

    -- 구조 파싱 규칙
    structure_config TEXT,                  -- JSON: 구조 파싱 설정

    -- 검토 정보
    reviewed_by TEXT,
    reviewed_at TEXT,
    notes TEXT,

    -- 버전 관리
    version INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,

    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

CREATE INDEX IF NOT EXISTS idx_pattern_rules_cluster ON pattern_rules(cluster_id);
CREATE INDEX IF NOT EXISTS idx_pattern_rules_active ON pattern_rules(is_active);

-- ============================================
-- 5. 패턴 검증 이력 (STAGE 0.6, 0.7)
-- ============================================
CREATE TABLE IF NOT EXISTS validation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL,
    pattern_rule_id INTEGER,

    -- 검증 정보
    round INTEGER DEFAULT 1,                -- 검증 라운드
    stage TEXT,                             -- '0.6' 또는 '0.7'

    -- 샘플 문서
    sample_documents TEXT,                  -- JSON: 테스트한 문서 ID 목록

    -- 결과
    result TEXT,                            -- approved, rejected
    rejection_reason TEXT,                  -- 반려 사유
    issues TEXT,                            -- JSON: 발견된 문제들

    -- 수정 내역
    changes_made TEXT,                      -- JSON: 수정된 내용

    -- 검토자
    reviewed_by TEXT,
    reviewed_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (cluster_id) REFERENCES clusters(id),
    FOREIGN KEY (pattern_rule_id) REFERENCES pattern_rules(id)
);

CREATE INDEX IF NOT EXISTS idx_validation_cluster ON validation_history(cluster_id);
CREATE INDEX IF NOT EXISTS idx_validation_result ON validation_history(result);

-- ============================================
-- 6. 페이지별 OCR 결과 (STAGE 2)
-- ============================================
CREATE TABLE IF NOT EXISTS ocr_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,

    -- 이미지
    image_path TEXT,                        -- 페이지 이미지 경로

    -- OCR 결과
    raw_text TEXT,                          -- 원본 OCR 텍스트
    boxes TEXT,                             -- JSON: 바운딩 박스 정보
    confidence REAL,                        -- OCR 신뢰도

    -- 분류된 텍스트
    noise_removed TEXT,                     -- 노이즈 제거 후 텍스트
    metadata_extracted TEXT,                -- JSON: 추출된 메타데이터
    body_text TEXT,                         -- 본문 텍스트

    -- 상태
    status TEXT DEFAULT 'pending',          -- pending, processed, error
    error_message TEXT,

    created_at TEXT DEFAULT (datetime('now')),

    UNIQUE(document_id, page_number),
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_ocr_pages_document ON ocr_pages(document_id);
CREATE INDEX IF NOT EXISTS idx_ocr_pages_status ON ocr_pages(status);

-- ============================================
-- 7. 문서별 OCR 결과 (STAGE 2 완료)
-- ============================================
CREATE TABLE IF NOT EXISTS ocr_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,

    -- 전체 텍스트
    full_text TEXT,                         -- 전체 OCR 텍스트 (노이즈 제거됨)

    -- 메타데이터
    extracted_metadata TEXT,                -- JSON: 추출된 메타데이터

    -- 통계
    total_pages INTEGER,
    processed_pages INTEGER,
    failed_pages INTEGER,
    average_confidence REAL,

    -- 상태
    status TEXT DEFAULT 'pending',          -- pending, processing, completed, error
    error_message TEXT,

    -- 적용된 패턴
    pattern_rule_id INTEGER,
    cluster_id INTEGER,

    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,

    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (pattern_rule_id) REFERENCES pattern_rules(id)
);

CREATE INDEX IF NOT EXISTS idx_ocr_results_status ON ocr_results(status);

-- ============================================
-- 8. 구조 파싱 결과 (STAGE 3 중간)
-- ============================================
CREATE TABLE IF NOT EXISTS parsed_structure (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,

    -- 파싱된 구조
    structure_json TEXT,                    -- JSON: 전체 구조

    -- 통계
    bab_count INTEGER DEFAULT 0,            -- BAB (장) 수
    pasal_count INTEGER DEFAULT 0,          -- Pasal (조) 수
    ayat_count INTEGER DEFAULT 0,           -- Ayat (항) 수
    huruf_count INTEGER DEFAULT 0,          -- Huruf (호) 수
    angka_count INTEGER DEFAULT 0,          -- Angka (목) 수

    -- 섹션
    preamble_text TEXT,                     -- 전문 (Menimbang, Mengingat)
    body_text TEXT,                         -- 본문
    closing_text TEXT,                      -- 종결 조항

    -- 상태
    status TEXT DEFAULT 'pending',
    error_message TEXT,

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_parsed_status ON parsed_structure(status);

-- ============================================
-- 9. Akoma Ntoso 출력 (STAGE 3 완료)
-- ============================================
CREATE TABLE IF NOT EXISTS akn_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,

    -- XML 출력
    xml_content TEXT,                       -- Akoma Ntoso XML
    xml_path TEXT,                          -- 저장된 XML 파일 경로

    -- 메타데이터
    akn_uri TEXT,                           -- Akoma Ntoso URI
    frbr_work TEXT,                         -- FRBR Work URI
    frbr_expression TEXT,                   -- FRBR Expression URI

    -- 상태
    status TEXT DEFAULT 'pending',          -- pending, generated, validated, error
    schema_valid INTEGER DEFAULT 0,         -- 스키마 검증 통과 여부

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_akn_status ON akn_outputs(status);

-- ============================================
-- 10. 최종 검증 결과 (STAGE 4)
-- ============================================
CREATE TABLE IF NOT EXISTS validation_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL UNIQUE,

    -- 검증 결과
    status TEXT DEFAULT 'pending',          -- pending, success, warning, error

    -- 텍스트 검증
    text_match_rate REAL,                   -- 원본 대비 텍스트 일치율
    missing_text TEXT,                      -- JSON: 누락된 텍스트

    -- 구조 검증
    structure_complete INTEGER DEFAULT 0,   -- 구조 완전성
    structure_issues TEXT,                  -- JSON: 구조 문제

    -- XML 검증
    schema_valid INTEGER DEFAULT 0,         -- 스키마 유효성
    schema_errors TEXT,                     -- JSON: 스키마 오류

    -- 수동 검토 필요 여부
    manual_review_needed INTEGER DEFAULT 0,
    review_notes TEXT,

    created_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT,

    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_validation_results_status ON validation_results(status);
CREATE INDEX IF NOT EXISTS idx_validation_manual_review ON validation_results(manual_review_needed);

-- ============================================
-- 11. 파이프라인 상태 (전체 진행 상황)
-- ============================================
CREATE TABLE IF NOT EXISTS pipeline_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),

    -- 전체 통계
    total_documents INTEGER DEFAULT 0,

    -- 단계별 진행 상황
    stage_0_pending INTEGER DEFAULT 0,      -- 헤더 추출 대기
    stage_0_completed INTEGER DEFAULT 0,    -- 헤더 추출 완료

    stage_05_pending INTEGER DEFAULT 0,     -- 패턴 분석 대기
    stage_05_completed INTEGER DEFAULT 0,   -- 패턴 분석 완료

    stage_06_pending INTEGER DEFAULT 0,     -- 패턴 검증 대기
    stage_06_approved INTEGER DEFAULT 0,    -- 패턴 승인
    stage_06_rejected INTEGER DEFAULT 0,    -- 패턴 반려

    stage_2_pending INTEGER DEFAULT 0,      -- OCR 대기
    stage_2_processing INTEGER DEFAULT 0,   -- OCR 처리 중
    stage_2_completed INTEGER DEFAULT 0,    -- OCR 완료

    stage_3_pending INTEGER DEFAULT 0,      -- AKN 변환 대기
    stage_3_completed INTEGER DEFAULT 0,    -- AKN 변환 완료

    stage_4_pending INTEGER DEFAULT 0,      -- 검증 대기
    stage_4_success INTEGER DEFAULT 0,      -- 검증 성공
    stage_4_warning INTEGER DEFAULT 0,      -- 검증 경고
    stage_4_error INTEGER DEFAULT 0,        -- 검증 오류

    -- 타임스탬프
    started_at TEXT,
    last_updated_at TEXT DEFAULT (datetime('now'))
);

-- 초기 상태 삽입
INSERT OR IGNORE INTO pipeline_state (id) VALUES (1);

-- ============================================
-- 12. 노이즈 패턴 사전 (공통)
-- ============================================
CREATE TABLE IF NOT EXISTS noise_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    pattern TEXT NOT NULL,                  -- 정규식 패턴
    pattern_type TEXT,                      -- regex, exact, contains
    description TEXT,                       -- 설명

    -- 범위
    scope TEXT DEFAULT 'global',            -- global, cluster_specific
    cluster_id INTEGER,                     -- cluster_specific인 경우

    is_active INTEGER DEFAULT 1,

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (cluster_id) REFERENCES clusters(id)
);

-- 기본 노이즈 패턴 삽입
INSERT OR IGNORE INTO noise_patterns (pattern, pattern_type, description) VALUES
    ('www\\.djpp\\.depkumham\\.go\\.id', 'regex', 'DJPP 워터마크 URL'),
    ('www\\.bphn\\.go\\.id', 'regex', 'BPHN 워터마크 URL'),
    ('ditjen Peraturan Perundang-undangan', 'contains', '기관명'),
    ('^\\s*\\d+\\s*$', 'regex', '페이지 번호 (숫자만)'),
    ('REPUBLIK INDONESIA', 'exact', '국가명 단독');

-- ============================================
-- 13. 처리 로그
-- ============================================
CREATE TABLE IF NOT EXISTS processing_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    document_id TEXT,
    stage TEXT,                             -- 단계
    action TEXT,                            -- 수행한 작업

    status TEXT,                            -- success, error
    message TEXT,
    details TEXT,                           -- JSON: 상세 정보

    duration_ms INTEGER,                    -- 처리 시간 (밀리초)

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE INDEX IF NOT EXISTS idx_logs_document ON processing_logs(document_id);
CREATE INDEX IF NOT EXISTS idx_logs_stage ON processing_logs(stage);
CREATE INDEX IF NOT EXISTS idx_logs_created ON processing_logs(created_at);

-- ============================================
-- 14. 테스트 실행 (OCR 파이프라인 검증)
-- ============================================
CREATE TABLE IF NOT EXISTS test_runs (
    run_id TEXT PRIMARY KEY,                -- 테스트 실행 ID (예: phase1_20241215_120000)
    phase TEXT NOT NULL,                    -- phase1, phase2, phase3

    -- 실행 정보
    started_at TEXT,                        -- 시작 시간
    completed_at TEXT,                      -- 완료 시간

    -- 샘플 통계
    total_samples INTEGER DEFAULT 0,        -- 총 샘플 수
    processed INTEGER DEFAULT 0,            -- 처리 완료
    failed INTEGER DEFAULT 0,               -- 실패

    -- 설정
    config_json TEXT,                       -- JSON: 실행 설정

    -- 결과 요약
    summary_json TEXT,                      -- JSON: 요약 통계
                                           -- avg_quality_score, manual_review_count 등

    -- 품질 통계
    avg_quality_score REAL,                 -- 평균 품질 점수
    median_quality_score REAL,              -- 중앙값 품질 점수
    min_quality_score REAL,                 -- 최소 품질 점수
    max_quality_score REAL,                 -- 최대 품질 점수

    -- 수동 검토 통계
    manual_review_count INTEGER DEFAULT 0,  -- 수동 검토 필요 페이지 수
    manual_review_ratio REAL,               -- 수동 검토 비율

    -- 처리 시간
    total_processing_time_ms INTEGER,       -- 총 처리 시간 (ms)
    avg_processing_time_ms REAL,            -- 평균 처리 시간 (ms)

    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_test_runs_phase ON test_runs(phase);
CREATE INDEX IF NOT EXISTS idx_test_runs_started ON test_runs(started_at);

-- ============================================
-- 15. 테스트 샘플 (개별 문서 결과)
-- ============================================
CREATE TABLE IF NOT EXISTS test_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,                   -- 테스트 실행 ID
    document_id TEXT NOT NULL,              -- 문서 ID

    -- 문서 정보
    category TEXT,                          -- 문서 카테고리 (uu, pp, perpres 등)
    year INTEGER,                           -- 문서 연도

    -- 품질 점수
    avg_quality_score REAL,                 -- 평균 품질 점수

    -- 페이지 통계
    total_pages INTEGER,                    -- 총 페이지 수
    pages_text INTEGER DEFAULT 0,           -- 내장 텍스트 사용 페이지
    pages_ocr INTEGER DEFAULT 0,            -- OCR 사용 페이지
    pages_hybrid INTEGER DEFAULT 0,         -- 하이브리드 페이지
    pages_skip INTEGER DEFAULT 0,           -- 스킵 페이지
    pages_manual INTEGER DEFAULT 0,         -- 수동 검토 필요 페이지

    -- 처리 상태
    status TEXT DEFAULT 'pending',          -- pending, completed, partial, failed
    processing_time_ms INTEGER,             -- 처리 시간 (ms)
    error_message TEXT,                     -- 오류 메시지

    -- 상세 결과
    result_json TEXT,                       -- JSON: 상세 결과 (페이지별 품질 등)

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (run_id) REFERENCES test_runs(run_id),
    FOREIGN KEY (document_id) REFERENCES documents(id),
    UNIQUE(run_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_test_samples_run ON test_samples(run_id);
CREATE INDEX IF NOT EXISTS idx_test_samples_document ON test_samples(document_id);
CREATE INDEX IF NOT EXISTS idx_test_samples_category ON test_samples(category);
CREATE INDEX IF NOT EXISTS idx_test_samples_status ON test_samples(status);
CREATE INDEX IF NOT EXISTS idx_test_samples_quality ON test_samples(avg_quality_score);

-- ============================================
-- 16. 테스트 임계값 (보정 이력)
-- ============================================
CREATE TABLE IF NOT EXISTS test_thresholds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,                            -- 보정에 사용된 테스트 실행 ID

    -- 임계값
    quality_threshold REAL,                 -- TEXT vs OCR 결정 임계값
    manual_review_trigger REAL,             -- 수동 검토 트리거 임계값
    broken_ratio_alert REAL,                -- 깨진 문자 비율 경고 임계값
    word_recognition_min REAL,              -- 최소 단어 인식률
    ocr_confidence_min REAL,                -- 최소 OCR 신뢰도

    -- 보정 정보
    is_current INTEGER DEFAULT 0,           -- 현재 사용 중인 임계값 여부
    calibration_notes TEXT,                 -- 보정 메모

    created_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (run_id) REFERENCES test_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_test_thresholds_current ON test_thresholds(is_current);

-- 기본 임계값 삽입
INSERT OR IGNORE INTO test_thresholds (id, quality_threshold, manual_review_trigger, broken_ratio_alert, word_recognition_min, ocr_confidence_min, is_current, calibration_notes)
VALUES (1, 0.92, 0.50, 0.10, 0.40, 0.60, 1, 'Initial default thresholds');
"""
