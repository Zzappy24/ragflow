#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import json
import logging
import os
import re
import time
from abc import ABC
from functools import partial

import requests

from agent.component.base import ComponentBase, ComponentParamBase
from common.connection_utils import timeout
from deepdoc.parser import HtmlParser


class InvokeParam(ComponentParamBase):
    """
    Define the Invoke component parameters.
    """

    def __init__(self):
        super().__init__()
        self.proxy = None
        self.headers = ""
        self.method = "get"
        self.variables = []
        self.url = ""
        self.timeout = 60
        self.clean_html = False
        self.datatype = "json"

    def check(self):
        self.check_valid_value(self.method.lower(), "Type of content from the crawler", ["get", "post", "put"])
        self.check_empty(self.url, "End point URL")
        self.check_positive_integer(self.timeout, "Timeout time in second")
        self.check_boolean(self.clean_html, "Clean HTML")
        self.check_valid_value(self.datatype.lower(), "Data post type", ["json", "formdata"])  # Check for valid datapost value


class Invoke(ComponentBase, ABC):
    component_name = "Invoke"
    header_variable_ref_patt = r"\{([a-zA-Z_][a-zA-Z0-9_.@-]*)\}"

    @staticmethod
    def _coerce_json_arg_if_possible(key, value):
        raw_value = value
        if isinstance(value, str):
            try:
                value = json.loads(value)
                logging.debug(
                    "Invoke JSON arg coercion succeeded. key=%s parsed_type=%s",
                    key,
                    type(value).__name__,
                )
            except json.JSONDecodeError as exc:
                # CUSTOM B2B SaaS — jamais la valeur brute dans les logs (clés API
                # en paramètres de requête, contenu utilisateur ; audit 2026-09-06).
                logging.info(
                    "Invoke JSON arg coercion skipped; value is not valid JSON. key=%s len=%d error=%s",
                    key,
                    len(raw_value) if isinstance(raw_value, str) else -1,
                    exc,
                )
                return raw_value

        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            logging.warning(
                "Invoke JSON arg is not JSON-serializable. key=%s value_type=%s value=%r error=%s",
                key,
                type(value).__name__,
                value,
                exc,
            )
            raise ValueError(f"Invoke JSON argument '{key}' is not JSON-serializable.") from exc

        return value

    @staticmethod
    def _coerce_json_arg_if_possible(key, value):
        raw_value = value
        if isinstance(value, str):
            try:
                value = json.loads(value)
                logging.debug(
                    "Invoke JSON arg coercion succeeded. key=%s parsed_type=%s",
                    key,
                    type(value).__name__,
                )
            except json.JSONDecodeError as exc:
                # CUSTOM B2B SaaS — jamais la valeur brute dans les logs (clés API
                # en paramètres de requête, contenu utilisateur ; audit 2026-09-06).
                logging.info(
                    "Invoke JSON arg coercion skipped; value is not valid JSON. key=%s len=%d error=%s",
                    key,
                    len(raw_value) if isinstance(raw_value, str) else -1,
                    exc,
                )
                return raw_value

        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            logging.warning(
                "Invoke JSON arg is not JSON-serializable. key=%s value_type=%s value=%r error=%s",
                key,
                type(value).__name__,
                value,
                exc,
            )
            raise ValueError(f"Invoke JSON argument '{key}' is not JSON-serializable.") from exc

        return value

    def get_input_form(self) -> dict[str, dict]:
        res = {}
        for item in self._param.variables or []:
            if not isinstance(item, dict):
                continue
            ref = (item.get("ref") or "").strip()
            if not ref or ref in res:
                continue

            elements = self.get_input_elements_from_text("{" + ref + "}")
            element = elements.get(ref, {})
            res[ref] = {
                "type": "line",
                "name": element.get("name") or item.get("key") or ref,
            }
        return res

    def _resolve_variable_value(self, variable_name: str, kwargs: dict | None = None):
        kwargs = kwargs or {}
        value = kwargs.get(variable_name, self._canvas.get_variable_value(variable_name))
        if isinstance(value, partial):
            value = "".join(value())
            self.set_input_value(variable_name, value)
        return "" if value is None else value

    def _render_template(self, content: str, pattern: str, kwargs: dict | None = None, *, flags: int = 0) -> str:
        content = content or ""
        if not content:
            return content

        def replace_variable(match_obj):
            return str(self._resolve_variable_value(match_obj.group(1), kwargs))

        return re.sub(pattern, replace_variable, content, flags=flags)

    def _resolve_template_text(self, content: str, kwargs: dict | None = None) -> str:
        return self._render_template(content, self.variable_ref_patt, kwargs, flags=re.DOTALL)

    def _resolve_header_text(self, content: str, kwargs: dict | None = None) -> str:
        # Headers support plain {token} placeholders, so they cannot reuse the canvas variable regex.
        return self._render_template(content, self.header_variable_ref_patt, kwargs)

    def _resolve_arg_value(self, para: dict, kwargs: dict) -> object:
        ref = (para.get("ref") or "").strip()
        if ref and (ref in kwargs or self._canvas.get_variable_value(ref) is not None):
            return self._resolve_variable_value(ref, kwargs)

        if para.get("value") is not None:
            value = para["value"]
            if isinstance(value, str):
                return self._resolve_template_text(value, kwargs)
            return value

        if ref:
            return self._resolve_variable_value(ref, kwargs)

        return ""

    def _is_json_mode(self) -> bool:
        return self._param.datatype.lower() == "json"

    def _build_request_args(self, kwargs: dict) -> dict:
        args = {}
        for para in self._param.variables:
            key = para["key"]
            value = self._resolve_arg_value(para, kwargs)
            if self._is_json_mode():
                # JSON mode accepts stringified JSON so complex payloads can be passed through variables.
                value = self._coerce_json_arg_if_possible(key, value)
            args[key] = value

            if para.get("ref"):
                self.set_input_value(para["ref"], value)
        return args

    def _build_url(self, kwargs: dict) -> str:
        url = self._resolve_template_text(self._param.url.strip(), kwargs)
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        return url

    def _build_headers(self, kwargs: dict) -> dict:
        if not self._param.headers:
            return {}

        headers = json.loads(self._param.headers)
        if not isinstance(headers, dict):
            raise ValueError("Invoke headers must be a JSON object.")

        return {key: self._resolve_header_text(value, kwargs) if isinstance(value, str) else value for key, value in headers.items()}

    def _build_proxies(self) -> dict | None:
        if not re.sub(r"https?:?/?/?", "", self._param.proxy):
            return None
        # CUSTOM B2B SaaS — un proxy interne au cluster est un SSRF vers ce
        # proxy (audit 2026-09-06) : même garde que l'URL.
        from common.ssrf_guard import assert_url_is_safe
        proxy = self._param.proxy if "://" in self._param.proxy else "http://" + self._param.proxy
        assert_url_is_safe(proxy)
        return {"http": self._param.proxy, "https": self._param.proxy}

    _MAX_REDIRECTS = 5

    def _send_request(self, url: str, args: dict, headers: dict, proxies: dict | None):
        # CUSTOM B2B SaaS — Invoke exécute une requête HTTP vers l'URL du DSL
        # (utilisateur client) depuis le pod api, qui joint tout le cluster :
        # vLLM, ES, LiteLLM, l'API Kubernetes, les métadonnées cloud… avec le
        # corps de réponse renvoyé dans le chat (audit 2026-09-06). Hôte public
        # obligatoire, DNS épinglé, chaque redirection re-validée.
        from urllib.parse import urljoin
        from common.ssrf_guard import assert_url_is_safe, pin_dns

        method = self._param.method.lower()
        request = getattr(requests, method)
        current, hops = url, 0
        while True:
            host, ip = assert_url_is_safe(current)
            request_kwargs = {
                "url": current,
                "headers": headers,
                "proxies": proxies,
                "timeout": self._param.timeout,
                "allow_redirects": False,
            }
            # GET sends query params; POST/PUT send either JSON or form data based on datatype.
            if method == "get":
                request_kwargs["params"] = args
            else:
                request_kwargs["json" if self._is_json_mode() else "data"] = args
            with pin_dns(host, ip):
                response = request(**request_kwargs)
            location = response.headers.get("Location") if response.is_redirect else None
            if location and hops < self._MAX_REDIRECTS:
                current, hops = urljoin(current, location), hops + 1
                continue
            return response

    def _format_response(self, response) -> str:
        if not self._param.clean_html:
            return response.text

        # HtmlParser keeps the Invoke output text-focused when the endpoint returns HTML.
        sections = HtmlParser()(None, response.content)
        return "\n".join(sections)

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 3)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("Invoke processing"):
            return

        is_json_mode = self._param.datatype.lower() == "json"
        args = {}
        for para in self._param.variables:
            key = para["key"]
            if "value" in para and para.get("value") is not None:
                value = para["value"]
            elif para.get("ref") in kwargs:
                value = kwargs[para["ref"]]
            else:
                value = self._canvas.get_variable_value(para["ref"])

            coerced_value = self._coerce_json_arg_if_possible(key, value) if is_json_mode else value
            args[key] = coerced_value

            if para.get("ref"):
                self.set_input_value(para["ref"], coerced_value)
    
        url = self._param.url.strip()

        def replace_variable(match):
            var_name = match.group(1)
            try:
                value = self._canvas.get_variable_value(var_name)
                return str(value or "")
            except Exception:
                return ""

        variable_pattern = r"\{([a-zA-Z_][a-zA-Z0-9_.@-]*)\}"

        # {base_url} or {component_id@variable_name}
        url = re.sub(variable_pattern, replace_variable, url)

        if url.find("http") != 0:
            url = "http://" + url

        method = self._param.method.lower()
        headers = {}
        if self._param.headers:
            try:
                parsed_headers = json.loads(self._param.headers)
            except json.JSONDecodeError as e:
                # CUSTOM B2B SaaS — les en-têtes contiennent des Authorization (audit 2026-09-06).
                logging.warning(
                    "Invoke headers are not valid JSON, ignoring headers. len=%d error=%s",
                    len(self._param.headers),
                    e,
                )
                parsed_headers = {}
            if not isinstance(parsed_headers, dict):
                logging.warning(
                    "Invoke headers JSON is of type %s, expected an object; ignoring headers.",
                    type(parsed_headers).__name__,
                )
                parsed_headers = {}
            headers = parsed_headers
            for key, value in list(headers.items()):
                if isinstance(value, str):
                    headers[key] = re.sub(variable_pattern, replace_variable, value)
        proxies = None
        if re.sub(r"https?:?/?/?", "", self._param.proxy):
            proxies = {"http": self._param.proxy, "https": self._param.proxy}

        last_e = ""
        for _ in range(self._param.max_retries + 1):
            if self.check_if_canceled("Invoke processing"):
                return

            try:
                response = self._send_request(url, args, headers, proxies)
                result = self._format_response(response)
                self.set_output("result", result)
                return result
            except Exception as e:
                if self.check_if_canceled("Invoke processing"):
                    return

                last_error = e
                logging.exception(f"Http request error: {e}")
                time.sleep(self._param.delay_after_error)

        if last_error:
            self.set_output("_ERROR", str(last_error))
            return f"Http request error: {last_error}"

    def thoughts(self) -> str:
        return "Waiting for the server respond..."
