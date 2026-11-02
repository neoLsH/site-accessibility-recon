"""cookies — 本轮内的会话状态，按 (臂, host) 隔离。

只保留目标站点在本次运行中自己下发的 cookie，用于“同一客户端臂连续访问同一
站点”的真实会话连续性（例如服务端在首跳种下的会话标识）。约束：

    * 仅内存/临时目录，进程退出即消失，不做任何持久化；
    * 臂之间、host 之间互不可见，避免把另一个身份的状态带进当前臂；
    * cookie 名与值不进入证据行、档案或日志，调用方只拿文件路径/会话对象；
    * 不构造、不伪造、不复用外部 cookie；不做验证码或鉴权相关的求解。

env=0 或 enabled=False 时整个对象是空操作，调用方可以无条件使用。
"""

import hashlib
import os
import tempfile

CURL_HEADER = "# Netscape HTTP Cookie File\n"


class CookieStore:
    def __init__(self, enabled=True):
        self.enabled = bool(enabled)
        self._tmp = (tempfile.TemporaryDirectory(prefix="recon-cookies-")
                     if self.enabled else None)
        self._curl = {}
        self._cffi = {}

    @property
    def root(self):
        return self._tmp.name if self._tmp else None

    @staticmethod
    def _slug(arm, host):
        raw = f"{arm}\0{host}".encode("utf-8", "replace")
        return hashlib.sha256(raw).hexdigest()[:20]

    def _jar_path(self, arm, host):
        return os.path.join(self.root, self._slug(arm, host) + ".jar")

    def curl_jar(self, arm, host):
        if not self.enabled:
            return None
        key = (arm, host)
        if key not in self._curl:
            path = self._jar_path(arm, host)
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_EXCL, 0o600)
            try:
                os.write(fd, CURL_HEADER.encode())
            finally:
                os.close(fd)
            self._curl[key] = path
        return self._curl[key]

    def cffi_session(self, arm, host):
        if not self.enabled:
            return None
        key = (arm, host)
        if key not in self._cffi:
            try:
                from curl_cffi import requests as creq
            except ImportError:
                self._cffi[key] = None
            else:
                self._cffi[key] = creq.Session()
        return self._cffi[key]

    def close(self):
        for session in self._cffi.values():
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        self._cffi.clear()
        self._curl.clear()
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None

    def __del__(self):
        # 调用方忘了 close 时也不要把临时会话目录留在盘上
        try:
            self.close()
        except Exception:
            pass
