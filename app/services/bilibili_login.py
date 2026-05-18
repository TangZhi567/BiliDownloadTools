from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json,text/plain,*/*",
}


@dataclass
class LoginStatus:
    logged_in: bool
    username: str = ""
    message: str = ""


@dataclass
class QrPollResult:
    state: str
    message: str
    cookie_file: str = ""
    username: str = ""


class BiliQrLoginSession:
    def __init__(self, cookie_file: str | None = None) -> None:
        self.cookie_file = cookie_file or str(get_default_cookie_file())
        self.cookie_jar = MozillaCookieJar(self.cookie_file)
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.qrcode_key = ""
        self.login_url = ""

    def generate(self) -> str:
        payload = _request_json(self.opener, GENERATE_URL)
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("message") or "二维码生成失败"))

        data = payload.get("data") or {}
        self.login_url = str(data.get("url") or "")
        self.qrcode_key = str(data.get("qrcode_key") or "")
        if not self.login_url or not self.qrcode_key:
            raise RuntimeError("二维码登录接口未返回有效登录地址")
        return self.login_url

    def poll(self) -> QrPollResult:
        if not self.qrcode_key:
            raise RuntimeError("二维码尚未生成")

        url = f"{POLL_URL}?{urlencode({'qrcode_key': self.qrcode_key})}"
        payload = _request_json(self.opener, url)
        if payload.get("code") != 0:
            return QrPollResult("waiting", str(payload.get("message") or "等待扫码"))

        data = payload.get("data") or {}
        code = int(data.get("code", -1))
        message = str(data.get("message") or "")
        if code == 0:
            self._save_cookie_jar()
            status = check_login_status(self.cookie_file)
            return QrPollResult(
                "success",
                status.message or "登录成功",
                cookie_file=self.cookie_file,
                username=status.username,
            )
        if code == 86101:
            return QrPollResult("waiting", message or "等待扫码")
        if code == 86090:
            return QrPollResult("scanned", message or "已扫码，等待确认")
        if code == 86038:
            return QrPollResult("expired", message or "二维码已过期")

        return QrPollResult("waiting", message or f"等待确认（{code}）")

    def _save_cookie_jar(self) -> None:
        path = Path(self.cookie_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_jar.save(str(path), ignore_discard=True, ignore_expires=True)


def get_default_cookie_file() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        base_dir = Path(local_app_data) / "BiliDownloader"
    else:
        base_dir = Path.home() / ".bili_downloader"
    return base_dir / "bilibili_cookies.txt"


def check_login_status(cookie_file: str | None = None) -> LoginStatus:
    cookie_path = Path(cookie_file or get_default_cookie_file())
    if not cookie_path.exists():
        return LoginStatus(False, message="未登录")

    cookie_jar = MozillaCookieJar(str(cookie_path))
    try:
        cookie_jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
        opener = build_opener(HTTPCookieProcessor(cookie_jar))
        payload = _request_json(opener, NAV_URL)
    except Exception as ex:
        return LoginStatus(False, message=f"登录态检查失败：{ex}")

    if payload.get("code") != 0:
        return LoginStatus(False, message=str(payload.get("message") or "未登录"))

    data = payload.get("data") or {}
    if bool(data.get("isLogin")):
        username = str(data.get("uname") or "")
        return LoginStatus(True, username=username, message=f"已登录：{username}" if username else "已登录")

    return LoginStatus(False, message="未登录或登录已失效")


def delete_saved_cookie(cookie_file: str | None = None) -> None:
    cookie_path = Path(cookie_file or get_default_cookie_file())
    try:
        cookie_path.unlink(missing_ok=True)
    except Exception:
        pass


def _request_json(opener, url: str) -> dict:
    req = Request(url, headers=REQ_HEADERS)
    with opener.open(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body)
