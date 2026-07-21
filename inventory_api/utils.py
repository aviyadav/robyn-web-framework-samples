"""Small shared helpers."""

from robyn import Headers, Response
from robyn.jsonify import jsonify


def json_response(body: dict | list, status_code: int = 200) -> Response:
    """Builds a JSON ``Response`` with an explicit status code.

    Route handlers and exception handlers in the installed Robyn version
    don't correctly turn a ``(dict, headers, status_code)`` tuple into a
    response when the body is a plain dict/list, so non-200 JSON responses
    are built explicitly instead.
    """
    return Response(
        status_code=status_code,
        headers=Headers({"Content-Type": "application/json"}),
        description=jsonify(body),
    )
