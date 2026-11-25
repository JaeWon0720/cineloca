"""API-019 데이터 출처 스키마 (FR-705, UI-09).

화면 타입 frontend/src/api/types.ts의 SourcesInfo와 같다.
"""

from pydantic import BaseModel, Field

from app.api.schemas.common import SourceCard
from app.core.sources import Policy


class LedgerEntry(SourceCard):
    license: str = Field(description="이용조건")
    policy: Policy = Field(description="저장 정책: STORE 저장 · ID_ONLY ID만 · NO_STORE 저장 안 함")


class LicenseItem(BaseModel):
    name: str = Field(description="오픈소스·모델 이름")
    license: str


class SourcesResponse(BaseModel):
    ledger: list[LedgerEntry] = Field(description="출처 대장(저장 출처 다음에 저장하지 않는 출처)")
    notice_version: str = Field(description="지금 이용 안내 버전(FR-701)")
    licenses: list[LicenseItem]
