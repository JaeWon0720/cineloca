"""이용 안내 버전 (FR-701). 화면 frontend/src/api/notice.ts의 NOTICE_VERSION과 같아야 한다.

안내 내용이 바뀌면 두 곳을 함께 올린다. AI를 쓰는 요청은 사용자가 확인한 버전을
X-Notice-Version 헤더로 보내고, 이 값과 다르면 403으로 안내를 다시 보게 한다(SDD 3.2).
"""

NOTICE_VERSION = "2026-10"
