"""Error-response normalization shared by both tiers' FastAPI apps
(cloud/api/server.py and agent/bff/app.py) — every error response body is
guaranteed a plain-string `detail` field so the frontend's shared
apiFetch()/friendlyMessage() (frontend/shared/static/js/http.js) never has
to render a raw traceback or FastAPI's default `[{loc, msg, type}, ...]`
validation-error array."""

from fastapi.exceptions import RequestValidationError


def format_validation_errors(exc: RequestValidationError) -> str:
    """Flattens FastAPI's default 422 detail (a list of {loc, msg, type}
    dicts) into one readable string, e.g. 'email: field required;
    password: ensure this value has at least 8 characters'."""
    parts = []
    for err in exc.errors():
        loc = err.get("loc", ())
        # loc[0] is usually "body"/"query"/"path" — skip it, keep the field name(s).
        field = ".".join(str(p) for p in loc[1:]) or ".".join(str(p) for p in loc) or "value"
        parts.append(f"{field}: {err.get('msg', 'invalid value')}")
    return "; ".join(parts) if parts else "Invalid request."
