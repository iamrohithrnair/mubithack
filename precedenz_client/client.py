"""A small synchronous client for a Precedenz server.

    from precedenz_client import Precedenz

    p = Precedenz("http://127.0.0.1:8000", api_token="...", caller="pipeline")
    receipt = p.decide("expenses.meal_claim", "EXP-1042",
                       facts={"amount_usd": 62.4, "receipt_attached": True})
    if receipt["verdict"] == "follow":
        ...  # carry out receipt["actions"], then p.report_outcome(...)
    else:
        ...  # a person decides: receipt["reasons"] says why

It speaks HTTP only and never imports the app, so it runs anywhere `httpx` does.
Person-only calls (answer, overrule, plant) also send the approval token; a
client built without one cannot make them.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

DEFAULT_URL = "http://127.0.0.1:8000"
USER_AGENT = "precedenz-client/0.1"


# ------------------------------------------------------------------ errors


class PrecedenzError(Exception):
    """Anything the server refused, or could not be reached for."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


class Unreachable(PrecedenzError):
    """The server did not answer at all."""


class AuthError(PrecedenzError):
    """401: missing or wrong API token."""


class ApprovalRequired(PrecedenzError):
    """403: only a person with the approval token can do this."""


class NotFound(PrecedenzError):
    """404: unknown workstream, decision, precedent or template."""


class Conflict(PrecedenzError):
    """409: the idempotency key was used with different input, or the state moved on."""


class InvalidRequest(PrecedenzError):
    """400 or 422: the request was malformed."""


class ServerError(PrecedenzError):
    """5xx: the server failed. A decision failure is never this: it is an ask."""


_BY_STATUS = {401: AuthError, 403: ApprovalRequired, 404: NotFound, 409: Conflict,
              400: InvalidRequest, 422: InvalidRequest}


def _detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # FastAPI validation errors
        parts = []
        for item in detail:
            if isinstance(item, dict):
                where = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                parts.append(f"{where}: {item.get('msg')}" if where else str(item.get("msg")))
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return json.dumps(detail, default=str)


def raise_for(response: httpx.Response) -> None:
    """Turn a refusal into its typed error, with the server's own reason."""
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        detail = response.text or response.reason_phrase
    cls = _BY_STATUS.get(response.status_code)
    if cls is None:
        cls = ServerError if response.status_code >= 500 else PrecedenzError
    raise cls(_detail_text(detail) or f"HTTP {response.status_code}",
              status_code=response.status_code, detail=detail)


def default_idempotency_key(case_ref: str) -> str:
    """One key per case, so sending a case again repeats its first decision."""
    return f"case:{case_ref}"


def _items(body: Any, *keys: str) -> list[Any]:
    """A list from a route that returns either a bare list or {"<key>": [...]}."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in keys:
            if isinstance(body.get(key), list):
                return body[key]
    return []


# ------------------------------------------------------------------ client


class Precedenz:
    """One server, one caller identity. Thread-safe as far as `httpx.Client` is."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        api_token: str | None = None,
        approval_token: str | None = None,
        caller: str = "api",
        *,
        timeout: float = 180.0,
        http: httpx.Client | None = None,
    ):
        self.url = url.rstrip("/")
        self.api_token = api_token
        self.approval_token = approval_token
        self.caller = caller
        headers = {"User-Agent": USER_AGENT, "X-Precedenz-Caller": caller}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._headers = headers
        # A decision can wait on a model call and a Sapling check: be patient.
        self._http = http or httpx.Client(base_url=self.url, timeout=timeout)
        self._owns_http = http is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Precedenz":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -------------------------------------------------------------- plumbing

    def _request(self, method: str, path: str, *, person: bool = False, **kwargs: Any) -> Any:
        headers = dict(self._headers)
        if person:
            if not self.approval_token:
                raise ApprovalRequired(
                    "this needs a person: no approval token (set PRECEDENZ_APPROVAL_TOKEN)",
                    status_code=403,
                )
            headers["X-Approval-Token"] = self.approval_token
        try:
            response = self._http.request(method, self._absolute(path), headers=headers, **kwargs)
        except httpx.TransportError as exc:
            raise Unreachable(f"cannot reach Precedenz at {self.url}: {exc}") from exc
        raise_for(response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _absolute(self, path: str) -> str:
        # Our own client and a caller-supplied one with a base URL (a test client)
        # take the path; a bare caller-supplied client needs the whole URL.
        return path if self._http.base_url.host else f"{self.url}{path}"

    # ------------------------------------------------------------- callers

    def decide(
        self,
        workstream: str,
        case_ref: str,
        *,
        idempotency_key: str | None = None,
        facts: dict[str, Any] | None = None,
        text: str | None = None,
        source_revision: str | None = None,
    ) -> dict[str, Any]:
        """Follow or ask for one case. The same key with the same input repeats the receipt."""
        body: dict[str, Any] = {
            "workstream": workstream,
            "case_ref": case_ref,
            "idempotency_key": idempotency_key or default_idempotency_key(case_ref),
        }
        if facts is not None:
            body["facts"] = facts
        if text is not None:
            body["text"] = text
        if source_revision is not None:
            body["source_revision"] = source_revision
        return self._request("POST", "/v1/decide", json=body)

    def receipt(self, decision_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/decisions/{decision_id}")

    def open_questions(self, workstream: str | None = None) -> list[dict[str, Any]]:
        params = {"verdict": "ask", "open": "true"}
        if workstream:
            params["workstream"] = workstream
        body = self._request("GET", "/v1/decisions", params=params)
        return _items(body, "decisions", "questions", "items", "open")

    def report_outcome(self, decision_id: str, status: str, detail: str = "") -> dict[str, Any]:
        """Tell Precedenz what the caller did with a follow: executed or failed."""
        return self._request(
            "POST", f"/v1/decisions/{decision_id}/outcome", json={"status": status, "detail": detail}
        )

    def workstreams(self) -> list[dict[str, Any]]:
        return _items(self._request("GET", "/v1/workstreams"), "workstreams", "items")

    def templates(self) -> list[dict[str, Any]]:
        return _items(self._request("GET", "/v1/templates"), "templates", "items")

    # ------------------------------------------------------ a person only

    def answer(
        self,
        decision_id: str,
        answer: str,
        remember: bool = False,
        up_to: dict[str, Any] | None = None,
        ignore: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Answer an ask; with `remember`, the answer becomes a precedent after replay."""
        body: dict[str, Any] = {"answer": answer, "remember": remember, "note": note}
        if up_to:
            body["up_to"] = up_to
        if ignore:
            body["ignore"] = list(ignore)
        return self._request("POST", f"/v1/decisions/{decision_id}/answer", person=True, json=body)

    def overrule(self, precedent_id: str, reason: str) -> dict[str, Any]:
        """Stop a precedent at once; its kind of question asks again."""
        return self._request(
            "POST", f"/v1/precedents/{precedent_id}/overrule", person=True, json={"reason": reason}
        )

    def plant(self, template_id: str | None = None, contract: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plant a workstream from a template, or from a contract you wrote."""
        if not template_id and not contract:
            raise InvalidRequest("plant needs a template id or a contract")
        body: dict[str, Any] = {}
        if template_id:
            body["template_id"] = template_id
        if contract:
            body["contract"] = contract
        return self._request("POST", "/v1/workstreams", person=True, json=body)

    # ------------------------------------------------------------- the map

    def snapshot(self) -> dict[str, Any]:
        return self._request("GET", "/map/data")

    def events(self, since: int | None = 0, *, read_timeout: float = 45.0) -> Iterator[dict[str, Any]]:
        """Live events from /map/events, one dict each, until the stream ends.

        `since=0` replays every event the server still holds; `None` starts from now.
        The server sends a heartbeat every 15 seconds, so a read that waits longer
        than `read_timeout` means the connection is gone.
        """
        params = {} if since is None else {"since": since}
        headers = dict(self._headers) | {"Accept": "text/event-stream"}
        timeout = httpx.Timeout(10.0, read=read_timeout)
        try:
            with self._http.stream(
                "GET", self._absolute("/map/events"), params=params, headers=headers, timeout=timeout
            ) as response:
                raise_for(response)
                yield from parse_sse(response.iter_lines())
        except httpx.TransportError as exc:
            raise Unreachable(f"lost the event stream from {self.url}: {exc}") from exc


def parse_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Events from server-sent-event lines. Comments and heartbeats are skipped."""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                try:
                    event = json.loads("\n".join(data))
                except ValueError:
                    event = None
                data = []
                if isinstance(event, dict):
                    yield event
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
