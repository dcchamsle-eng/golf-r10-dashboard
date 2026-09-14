"""index.html을 GitHub Pages로 공개 배포. generate_dashboard.py 다음 단계로 실행.

golf_dashboard/publish_app.py와 동일한 구조 (표준 라이브러리 urllib만 사용, git 불필요).
"""

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
REPO_NAME = "golf-r10-dashboard"
API_BASE = "https://api.github.com"
TIMEOUT = 30

FILES_TO_UPLOAD = ["index.html"]


class GitHubAPIError(Exception):
    pass


def load_env():
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def api_request(method, path, token, body=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "golf-r10-dashboard-publisher")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw.decode("utf-8", errors="replace")
        if e.code == 404:
            return 404, detail
        raise GitHubAPIError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise GitHubAPIError(f"연결 실패: {e}") from None


def ensure_repo(username, token):
    status, _ = api_request("GET", f"/repos/{username}/{REPO_NAME}", token)
    if status == 404:
        print(f"[저장소] {REPO_NAME} 없음 -> 새로 생성(public)")
        api_request(
            "POST",
            "/user/repos",
            token,
            {
                "name": REPO_NAME,
                "description": "R10 드라이빙레인지 훈련 데이터 대시보드 (자동 배포, 비밀번호로 보호됨)",
                "private": False,
                "auto_init": True,
            },
        )
    else:
        print(f"[저장소] {REPO_NAME} 이미 있음")


def ensure_pages(username, token):
    status, _ = api_request("GET", f"/repos/{username}/{REPO_NAME}/pages", token)
    if status == 404:
        print("[Pages] 활성화 시도")
        api_request(
            "POST",
            f"/repos/{username}/{REPO_NAME}/pages",
            token,
            {"source": {"branch": "main", "path": "/"}},
        )
    else:
        print("[Pages] 이미 활성화됨")


def upload_file(username, token, filename):
    path = BASE_DIR / filename
    content = path.read_bytes()
    encoded = base64.b64encode(content).decode("ascii")

    status, existing = api_request("GET", f"/repos/{username}/{REPO_NAME}/contents/{filename}", token)
    body = {"message": f"{filename} 자동 갱신", "content": encoded, "branch": "main"}
    if status != 404 and "sha" in existing:
        body["sha"] = existing["sha"]

    api_request("PUT", f"/repos/{username}/{REPO_NAME}/contents/{filename}", token, body)
    print(f"[업로드] {filename} 갱신 완료")


def main():
    env = load_env()
    token = env.get("GITHUB_TOKEN", "").strip()
    username = env.get("GITHUB_USERNAME", "").strip()

    if not token or not username:
        print("[오류] .env에 GITHUB_TOKEN / GITHUB_USERNAME이 설정되어 있지 않습니다.")
        sys.exit(1)

    missing = [f for f in FILES_TO_UPLOAD if not (BASE_DIR / f).exists()]
    if missing:
        print(f"[오류] 다음 파일이 없습니다: {missing}. generate_dashboard.py를 먼저 실행하세요.")
        sys.exit(1)

    try:
        ensure_repo(username, token)
        ensure_pages(username, token)
        for f in FILES_TO_UPLOAD:
            upload_file(username, token, f)
    except GitHubAPIError as e:
        print(f"[오류] {e}")
        sys.exit(1)

    print(f"[완료] 공개 URL: https://{username}.github.io/{REPO_NAME}/")
    print("(GitHub Pages는 첫 배포 후 반영까지 1~2분 정도 걸릴 수 있습니다)")


if __name__ == "__main__":
    main()
