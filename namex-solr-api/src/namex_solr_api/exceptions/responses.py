# Copyright © 2023 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Exception responses."""
from http import HTTPStatus

from flask import current_app, jsonify

from .exceptions import BaseException


def bad_request_response(message: str, errors: list[dict[str, str]] | None = None):
    """Build generic bad request response."""
    return jsonify({"message": message, "details": errors or []}), HTTPStatus.BAD_REQUEST


def exception_response(exception: BaseException):
    """Build exception error response."""
    details = repr(exception)
    current_app.logger.error(details)
    try:
        message = exception.message or "Error processing request."
        status_code = exception.status_code or HTTPStatus.INTERNAL_SERVER_ERROR
    except Exception:
        current_app.logger.warning("Uncaught exception.")
        message = "Error processing request."
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    return jsonify({"message": message, "detail": details}), status_code
