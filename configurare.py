import json
import logging
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


JOURNAL_PATH = Path(__file__).with_name("jurnal.txt")
LOGGER_NAME = "configurare"
API_URL_TEMPLATE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
)
MODEL = "@cf/meta/llama-3.1-8b-instruct"


class CloudflareAPIError(Exception):
    def __init__(self, code, status, message):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(f"{code} {status}: {message}")


def _cloudflare_error(response_data, status_code, fallback_message):
    errors = response_data.get("errors", [])
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        error = errors[0]
        code = error.get("code") or status_code
        message = error.get("message") or fallback_message
    else:
        code = status_code
        message = fallback_message
    return CloudflareAPIError(code, f"HTTP_{status_code}", message)


def _request_cloudflare(messages, account_id, api_token):
    api_url = API_URL_TEMPLATE.format(account_id=account_id, model=MODEL)
    payload = json.dumps(
        {
            "messages": messages,
            "max_tokens": 1024,
        }
    ).encode("utf-8")
    request = Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        try:
            error_data = json.loads(response_body)
        except json.JSONDecodeError:
            error_data = {}
        if not isinstance(error_data, dict):
            error_data = {}
        raise _cloudflare_error(
            error_data,
            error.code,
            response_body or error.reason,
        ) from error

    if not isinstance(response_data, dict):
        raise ValueError("Răspunsul Cloudflare nu are format JSON obiect.")
    if not response_data.get("success", True):
        raise _cloudflare_error(response_data, 200, "Cererea a fost respinsă.")

    result = response_data.get("result")
    response_text = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("Răspunsul Cloudflare nu conține text.")
    return response_text


def configure_logging(log_path=JOURNAL_PATH):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    resolved_path = Path(log_path).resolve()
    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == resolved_path
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(resolved_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)

    return logger


def run_interaction(prompt, *, history=None, logger=None):
    logger = logger or configure_logging()
    if not prompt or not prompt.strip():
        logger.error("Promptul este gol.")
        raise ValueError("Promptul nu poate fi gol.")

    logger.info("Prompt: %s", prompt)

    api_token = None
    try:
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not account_id:
            raise ValueError("Variabila CLOUDFLARE_ACCOUNT_ID nu este configurată.")
        if not api_token:
            raise ValueError("Variabila CLOUDFLARE_API_TOKEN nu este configurată.")
        messages = list(history or [])
        messages.append({"role": "user", "content": prompt})
        response_text = _request_cloudflare(messages, account_id, api_token)
    except Exception as error:
        details = []
        for attribute in ("code", "status", "message"):
            value = getattr(error, attribute, None)
            if value is not None:
                details.append(f"{attribute}={value}")

        diagnostic = "; ".join(details)
        if api_token and diagnostic:
            diagnostic = diagnostic.replace(api_token, "[REDACTED]")
        diagnostic = diagnostic.replace("\r", " ").replace("\n", " ")[:500]
        suffix = f": {diagnostic}" if diagnostic else ""
        logger.error(
            "Apelul API a eșuat (%s)%s.",
            type(error).__name__,
            suffix,
        )
        raise

    logger.info("Răspuns: %s", response_text)
    return response_text
