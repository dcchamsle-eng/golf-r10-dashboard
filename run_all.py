"""update_data.py -> generate_dashboard.py -> publish_site.py 를 순서대로 실행하는 진입점.

새 CSV를 golf 폴더에 놓은 뒤 이 스크립트 하나로 대시보드 갱신 + 배포까지 끝낸다.
"""

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STEPS = ["update_data.py", "generate_dashboard.py", "publish_site.py"]


def main():
    for step in STEPS:
        print(f"\n=== {step} ===")
        result = subprocess.run([sys.executable, str(BASE_DIR / step)])
        if result.returncode != 0:
            print(f"[중단] {step} 실패 (exit {result.returncode})")
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
