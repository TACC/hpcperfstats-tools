"""Reusable REST API client for hpcperfstats-tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests

from .api_auth import apply_api_key_header


@dataclass
class ApiResult:
  ok: bool
  status_code: int
  data: Optional[Any] = None
  error: Optional[str] = None


class ApiClient:
  """Small API client used by tool CLIs and helper libraries."""

  def __init__(
      self,
      base_url: str,
      api_key: Optional[str] = None,
      verify_tls: bool = True,
      timeout: int = 30,
      session: Optional[requests.Session] = None,
  ):
    self.base_url = self._normalize_base_url(base_url)
    self.api_key = api_key
    self.verify_tls = verify_tls
    self.timeout = timeout
    self.session = session or requests.Session()

  @staticmethod
  def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").rstrip("/") + "/"

  def _url(self, path: str) -> str:
    return urljoin(self.base_url, path.lstrip("/"))

  @staticmethod
  def _is_same_origin(source_url: str, target_url: str) -> bool:
    src = urlparse(source_url)
    dst = urlparse(target_url)
    return (src.scheme, src.netloc) == (dst.scheme, dst.netloc)

  def _headers(self, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    return apply_api_key_header(dict(base or {}), self.api_key)

  def get_json(self, path: str) -> ApiResult:
    url = self._url(path)
    try:
      resp = self.session.get(
          url,
          timeout=self.timeout,
          verify=self.verify_tls,
          headers=self._headers(),
      )
    except requests.RequestException as exc:
      return ApiResult(ok=False, status_code=0, error=str(exc))
    try:
      data = resp.json()
    except ValueError:
      data = None
    if not resp.ok:
      err = resp.text if not data else str(data)
      return ApiResult(ok=False, status_code=resp.status_code, error=err, data=data)
    return ApiResult(ok=True, status_code=resp.status_code, data=data)

  def post_text(self, path: str, body: str, timeout: int = 300) -> ApiResult:
    """POST plain text. Follows one same-origin redirect preserving method/body."""
    url = self._url(path)
    headers = self._headers({"Content-Type": "text/plain; charset=utf-8"})
    try:
      resp = self.session.post(
          url,
          data=body,
          headers=headers,
          timeout=timeout,
          verify=self.verify_tls,
          allow_redirects=False,
      )
      if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location")
        if location:
          redirect_url = urljoin(url, location)
          if not self._is_same_origin(url, redirect_url):
            return ApiResult(ok=False, status_code=resp.status_code, error=f"Cross-origin redirect blocked: {redirect_url}")
          resp = self.session.post(
              redirect_url,
              data=body,
              headers=headers,
              timeout=timeout,
              verify=self.verify_tls,
          )
    except requests.RequestException as exc:
      return ApiResult(ok=False, status_code=0, error=str(exc))

    try:
      data = resp.json()
    except ValueError:
      data = None

    if not resp.ok:
      err = resp.text if not data else str(data)
      return ApiResult(ok=False, status_code=resp.status_code, error=err, data=data)
    return ApiResult(ok=True, status_code=resp.status_code, data=data)
