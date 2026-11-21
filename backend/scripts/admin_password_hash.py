"""관리자 비밀번호 해시 만들기 (FR-615).

backend 폴더에서: python scripts/admin_password_hash.py
비밀번호를 두 번 입력하면(화면에 안 보임) ADMIN_PASSWORD_HASH= 한 줄을 출력한다.
그 줄을 저장소 루트 .env에 넣고 api를 다시 시작한다(docker compose up -d api).
비밀번호 원문은 어디에도 저장하지 않는다.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.admin_auth import MAX_PASSWORD_CHARS, hash_password  # noqa: E402

MIN_CHARS = 12


def main() -> int:
    first = getpass.getpass("관리자 비밀번호(12자 이상): ")
    if not MIN_CHARS <= len(first) <= MAX_PASSWORD_CHARS:
        print(f"{MIN_CHARS}~{MAX_PASSWORD_CHARS}자로 정해 주세요.", file=sys.stderr)
        return 2
    if getpass.getpass("한 번 더: ") != first:
        print("두 번 입력한 비밀번호가 다릅니다.", file=sys.stderr)
        return 2
    print(f"ADMIN_PASSWORD_HASH={hash_password(first)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
