"""여러 API가 함께 쓰는 스키마: 출처 카드(FR-704), 오류 형식(SDD 3.2)."""

from datetime import date

from pydantic import BaseModel, Field


class SourceCard(BaseModel):
    """출처 카드. 외부 데이터를 보여 주는 결과 영역마다 붙인다(FR-704, BR-02)."""

    id: str = Field(description="출처 ID", examples=["DATA-01"])
    provider: str = Field(description="제공기관", examples=["부산영상위원회"])
    name: str = Field(description="데이터명", examples=["영화 속 부산정보"])
    base_date: date | None = Field(default=None, description="기준일")
    url: str = Field(description="원문 링크")


class ErrorBody(BaseModel):
    code: str = Field(description="오류 코드", examples=["LOCATION_NOT_FOUND"])
    message: str = Field(description="사용자에게 보여 줄 안내 문구")
    retry_after: int | None = Field(default=None, description="다시 시도까지 기다릴 초(429 등)")


class ErrorResponse(BaseModel):
    """모든 오류 응답의 형식: {"error": {"code", "message", "retry_after"}}."""

    error: ErrorBody
