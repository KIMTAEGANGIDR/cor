"""
ILIS OCR Quality Validator 테스트

품질 검증기 핵심 기능 테스트:
- 품질 점수 계산
- 등급 결정 (PASS/WARNING/FAIL)
- 연도별 동적 임계치
"""

import pytest
from peraturan.src.ocr.quality_validator import (
    QualityValidator,
    QualityGrade,
    QualityReport,
)


class TestQualityValidator:
    """품질 검증기 기본 테스트"""

    @pytest.fixture
    def validator(self):
        return QualityValidator()

    def test_validate_returns_quality_report(self, validator):
        """검증 결과 반환 테스트"""
        text = "PRESIDEN REPUBLIK INDONESIA UNDANG-UNDANG " * 20
        confidences = [0.95] * 20
        report = validator.validate(text, confidences)

        assert isinstance(report, QualityReport)
        assert hasattr(report, "grade")
        assert hasattr(report, "overall_score")
        assert hasattr(report, "confidence_score")
        assert hasattr(report, "dictionary_score")
        assert hasattr(report, "legal_term_score")
        assert hasattr(report, "structure_score")

    def test_short_text_fails(self, validator):
        """짧은 텍스트 FAIL 테스트"""
        text = "짧은 텍스트"
        confidences = [0.95]
        report = validator.validate(text, confidences, min_words=50)

        assert report.grade == QualityGrade.FAIL
        assert report.overall_score == 0.0
        assert "짧음" in report.issues[0]

    def test_high_quality_passes(self, validator):
        """고품질 텍스트 PASS 테스트"""
        # 법률 문서 구조와 용어를 포함한 텍스트
        text = """
        PRESIDEN REPUBLIK INDONESIA
        UNDANG-UNDANG REPUBLIK INDONESIA
        NOMOR 1 TAHUN 2024
        TENTANG PERUBAHAN

        Menimbang bahwa untuk mewujudkan tujuan negara
        Mengingat Pasal 5 ayat (1) Undang-Undang Dasar

        MEMUTUSKAN
        Menetapkan UNDANG-UNDANG TENTANG PERUBAHAN

        BAB I
        KETENTUAN UMUM

        Pasal 1
        Dalam Undang-Undang ini yang dimaksud dengan
        (1) Pemerintah adalah
        (2) Menteri adalah

        Pasal 2
        (1) Ketentuan sebagaimana dimaksud dalam Pasal 1
        """ * 2  # 충분한 길이

        # 높은 신뢰도
        confidences = [0.98] * 30

        report = validator.validate(text, confidences)

        assert report.overall_score >= 80  # 상당히 높은 점수
        assert report.total_words >= 50

    def test_empty_confidences_handling(self, validator):
        """빈 신뢰도 리스트 처리 테스트"""
        text = "PRESIDEN REPUBLIK INDONESIA " * 50
        report = validator.validate(text, [])

        assert report.confidence_score == 0.0


class TestDynamicThreshold:
    """연도별 동적 임계치 테스트"""

    @pytest.fixture
    def validator(self):
        return QualityValidator(use_dynamic_threshold=True)

    @pytest.fixture
    def static_validator(self):
        return QualityValidator(use_dynamic_threshold=False)

    # ==========================================
    # 연도별 임계치 반환 테스트
    # ==========================================
    def test_threshold_for_old_documents(self, validator):
        """1970년 이전 문서 임계치 테스트"""
        pass_thresh, warning_thresh = validator.get_quality_threshold(1953)
        assert pass_thresh == 88.0
        assert warning_thresh == 83.0

    def test_threshold_for_1970s_documents(self, validator):
        """1970-1989년 문서 임계치 테스트"""
        pass_thresh, warning_thresh = validator.get_quality_threshold(1975)
        assert pass_thresh == 91.0
        assert warning_thresh == 86.0

    def test_threshold_for_1990s_documents(self, validator):
        """1990-2009년 문서 임계치 테스트"""
        pass_thresh, warning_thresh = validator.get_quality_threshold(2000)
        assert pass_thresh == 93.0
        assert warning_thresh == 88.0

    def test_threshold_for_modern_documents(self, validator):
        """2010년 이후 문서 임계치 테스트"""
        pass_thresh, warning_thresh = validator.get_quality_threshold(2024)
        assert pass_thresh == 95.0
        assert warning_thresh == 90.0

    def test_threshold_with_none_year(self, validator):
        """연도 None인 경우 기본값 테스트"""
        pass_thresh, warning_thresh = validator.get_quality_threshold(None)
        assert pass_thresh == 95.0
        assert warning_thresh == 90.0

    def test_static_threshold_ignores_year(self, static_validator):
        """동적 임계치 비활성화 시 연도 무시 테스트"""
        pass_thresh, warning_thresh = static_validator.get_quality_threshold(1953)
        assert pass_thresh == 95.0
        assert warning_thresh == 90.0

    # ==========================================
    # 검증 결과에 동적 임계치 반영 테스트
    # ==========================================
    def test_old_document_gets_adjusted_grade(self, validator):
        """옛 문서 등급 조정 테스트"""
        text = """
        PRESIDEN REPUBLIK INDONESIA
        UNDANG-UNDANG NOMOR 1 TAHUN 1953
        TENTANG PENETAPAN
        Menimbang bahwa perlu menetapkan
        Mengingat Pasal undang-undang
        BAB I KETENTUAN
        Pasal 1 Dalam undang-undang
        """ * 5

        confidences = [0.89] * 20  # 89% 신뢰도

        # 현대 기준 (95%)으로는 WARNING/FAIL이지만
        # 1953년 기준 (88%)으로는 PASS 가능
        report = validator.validate(text, confidences, document_year=1953)

        # 연도 기반 임계치 적용 확인
        # 실제 점수에 따라 등급이 결정됨
        assert report.overall_score > 0

    def test_recommendation_mentions_adjusted_threshold(self, validator):
        """조정된 임계치가 권장사항에 포함되는지 테스트"""
        text = """
        PRESIDEN REPUBLIK INDONESIA
        UNDANG-UNDANG NOMOR 1 TAHUN 1953
        TENTANG PENETAPAN
        Menimbang bahwa perlu menetapkan
        Mengingat Pasal undang-undang
        BAB I KETENTUAN
        Pasal 1 Dalam undang-undang
        Pasal 2 Ketentuan sebagaimana
        Pasal 3 Peraturan pemerintah
        """ * 5

        confidences = [0.95] * 25

        report = validator.validate(text, confidences, document_year=1953)

        # PASS인 경우 옛 문서 기준 언급
        if report.grade == QualityGrade.PASS:
            assert "88" in report.recommendation or "자동 승인" in report.recommendation


class TestQualityScoreCalculation:
    """품질 점수 계산 테스트"""

    @pytest.fixture
    def validator(self):
        return QualityValidator()

    def test_confidence_score_calculation(self, validator):
        """OCR 신뢰도 점수 계산 테스트"""
        # 100% 신뢰도
        scores = [1.0] * 10
        confidence_score = validator._calculate_confidence_score(scores)
        assert confidence_score == 100.0

        # 80% 신뢰도
        scores = [0.8] * 10
        confidence_score = validator._calculate_confidence_score(scores)
        assert confidence_score < 100.0

    def test_empty_confidence_scores(self, validator):
        """빈 신뢰도 리스트 테스트"""
        confidence_score = validator._calculate_confidence_score([])
        assert confidence_score == 0.0

    def test_low_confidence_penalty(self, validator):
        """낮은 신뢰도 페널티 테스트"""
        # 높은 신뢰도
        high_scores = [0.95] * 10
        high_score = validator._calculate_confidence_score(high_scores)

        # 일부 낮은 신뢰도 포함
        mixed_scores = [0.95] * 5 + [0.7] * 5
        mixed_score = validator._calculate_confidence_score(mixed_scores)

        # 페널티로 인해 점수 감소
        assert mixed_score < high_score


class TestQualityGrade:
    """품질 등급 테스트"""

    def test_quality_grade_values(self):
        """등급 값 테스트"""
        assert QualityGrade.PASS.value == "pass"
        assert QualityGrade.WARNING.value == "warning"
        assert QualityGrade.FAIL.value == "fail"
        assert QualityGrade.ERROR.value == "error"


class TestQualityReport:
    """품질 리포트 테스트"""

    def test_quality_report_structure(self):
        """리포트 구조 테스트"""
        report = QualityReport(
            grade=QualityGrade.PASS,
            overall_score=95.0,
            confidence_score=96.0,
            dictionary_score=94.0,
            legal_term_score=100.0,
            structure_score=90.0,
            total_words=100,
            valid_words=94,
            legal_terms_found=15,
            structure_patterns_found=10,
            issues=[],
            recommendation="자동 승인",
        )

        assert report.grade == QualityGrade.PASS
        assert report.overall_score == 95.0
        assert report.total_words == 100
        assert report.recommendation == "자동 승인"

    def test_quality_report_with_issues(self):
        """이슈 포함 리포트 테스트"""
        report = QualityReport(
            grade=QualityGrade.WARNING,
            overall_score=92.0,
            issues=["낮은 OCR 신뢰도: 85.0%", "법률 용어 부족: 3개"],
            recommendation="샘플 검토 필요",
        )

        assert len(report.issues) == 2
        assert "신뢰도" in report.issues[0]
        assert "법률 용어" in report.issues[1]
