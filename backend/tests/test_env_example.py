"""저장소 루트 .env.example 형식 검사.

docker compose는 `이름=   # 설명`처럼 값이 빈 줄에서 # 뒤를 주석이 아니라 값으로 읽는다
(예: GEMINI_DAILY_LIMIT='# 비우면 20…' → AI 설정 오류로 AI가 꺼짐,
ADMIN_PASSWORD_HASH가 설명 글이 됨).
그래서 값이 빈 줄에는 같은 줄 설명을 두지 않는다. 값이 있는 줄(`AI_PROVIDER=gemini   # …`)은 괜찮다.
"""

import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
EMPTY_WITH_COMMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\s+#")


def test_empty_values_have_no_inline_comment() -> None:
    bad = [
        f"{n}: {line}"
        for n, line in enumerate(ENV_EXAMPLE.read_text(encoding="utf-8").splitlines(), start=1)
        if EMPTY_WITH_COMMENT.match(line)
    ]
    assert bad == [], "값이 빈 줄의 설명은 윗줄 주석으로 옮겨 주세요:\n" + "\n".join(bad)
