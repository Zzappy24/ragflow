#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
"""
CUSTOM B2B SaaS — chat model factories that aren't upstream.

Currently houses:
  - OllamaHermesChat: works around Ollama's broken tool-call parsing for
    Qwen3-Instruct-2507 (and any other model that emits Hermes-style XML
    tool calls). Ollama tries to parse `<tool_call>...</tool_call>` blocks
    when the request includes `tools`, fails on the Qwen3 template, and
    silently strips the entire response (`content:""`, `tool_calls:null`)
    while still billing the tokens. We bypass that by formatting the tool
    spec into the system prompt ourselves and parsing the raw output.

Why a separate factory rather than patching `Base`?
  - Zero-conflict on upstream merges (only a single import line in
    `rag/llm/chat_model.py` is touched).
  - Easy off-switch: when Ollama fixes upstream, the workspace admin just
    moves the model back to the standard "Ollama" factory.

Reference: https://github.com/SMFloris/ollama-qwen3-coder-proxy
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from copy import deepcopy
from types import SimpleNamespace

import json_repair

from common.misc_utils import thread_pool_exec
from common.token_utils import num_tokens_from_string, total_token_count_from_response
from rag.llm.chat_model import Base


# Match a single <tool_call>{...}</tool_call> block. The body is JSON; we use a
# non-greedy match so multiple back-to-back blocks parse correctly.
_HERMES_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _hermes_system_section(tools: list[dict]) -> str:
    """Render an OpenAI-style tool list as the Hermes system-prompt block.

    The Qwen3 chat template recognises this exact block and produces matching
    `<tool_call>` outputs. Format follows
    https://qwen.readthedocs.io/en/latest/framework/function_call.html.
    """
    tools_json = "\n".join(json.dumps(t, ensure_ascii=False) for t in tools)
    return (
        "\n\n# Tools\n\n"
        "You may call one or more functions to assist with the user query.\n\n"
        "You are provided with function signatures within <tools></tools> XML tags:\n"
        f"<tools>\n{tools_json}\n</tools>\n\n"
        "For each function call, return a json object with function name and "
        "arguments within <tool_call></tool_call> XML tags:\n"
        '<tool_call>\n{"name":"<function-name>","arguments":<args-json-object>}\n</tool_call>'
    )


def _parse_hermes_tool_calls(text: str) -> tuple[list, str]:
    """Extract `<tool_call>` blocks from `text`.

    Returns `(calls, cleaned_text)` where `calls` mimic the shape produced by
    the OpenAI SDK (`tc.function.name`, `tc.function.arguments` as a JSON
    string, plus `index`, `id`, `type`) so the rest of the tool-loop in
    `Base.async_chat_streamly_with_tools` can be reused unchanged.
    """
    matches = list(_HERMES_TOOL_CALL_RE.finditer(text))
    if not matches:
        return [], text
    cleaned = _HERMES_TOOL_CALL_RE.sub("", text).strip()
    calls = []
    for i, m in enumerate(matches):
        try:
            obj = json_repair.loads(m.group(1))
            if not isinstance(obj, dict):
                continue
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)
            calls.append(SimpleNamespace(
                index=i,
                id=f"call_hermes_{i}",
                type="function",
                function=SimpleNamespace(name=name, arguments=args_str),
            ))
        except Exception:
            logging.warning("OllamaHermesChat: failed to parse tool_call block: %s", m.group(1)[:200])
    return calls, cleaned


def _convert_history_for_hermes(history: list[dict]) -> list[dict]:
    """Translate OpenAI-style tool messages back into plain text the Hermes
    template understands.

    After round 0, `history` contains synthetic `assistant` messages with a
    `tool_calls` field and `role:"tool"` messages with results. Qwen3 with the
    Hermes template does NOT understand those roles — it expects everything as
    text. We fold tool_calls into `<tool_call>` text and wrap tool results in
    `<tool_response>`.
    """
    out = []
    for msg in history:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            chunks = [msg.get("content") or ""]
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                payload = {"name": fn.get("name", ""), "arguments": fn.get("arguments", "")}
                # arguments may be a JSON string; keep it as-is so the model
                # sees the same shape it produced.
                chunks.append("<tool_call>\n" + json.dumps(payload, ensure_ascii=False) + "\n</tool_call>")
            out.append({"role": "assistant", "content": "\n".join(c for c in chunks if c).strip()})
        elif role == "tool":
            out.append({
                "role": "user",
                "content": "<tool_response>\n" + (msg.get("content") or "") + "\n</tool_response>",
            })
        else:
            out.append(msg)
    return out


class OllamaHermesChat(Base):
    """OpenAI-API-Compatible client tailored for Ollama+Qwen3 tool-calling.

    Behaves identically to `Base` for plain chat. For tool-enabled requests,
    swaps out OpenAI's `tools` parameter for an in-prompt Hermes spec and
    parses `<tool_call>` blocks from the raw response — bypassing Ollama's
    broken native parser.
    """
    _FACTORY_NAME = "Ollama-Hermes"

    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("base_url cannot be None for Ollama-Hermes")
        # Default to Ollama's OpenAI-compat endpoint when the admin enters the
        # bare host. Keeps the UX close to the standard Ollama factory.
        if not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        # Strip the suffix the admin UI appends to OpenAI-API-Compatible names.
        model_name = model_name.split("___")[0]
        super().__init__(key, model_name, base_url, **kwargs)

    # The non-streaming variant: same trick, simpler control flow.
    async def async_chat_with_tools(self, system: str, history: list, gen_conf: dict = {}):
        gen_conf = self._clean_conf(gen_conf)
        tools = self.tools or []
        history = self._inject_hermes_prompt(system, history, tools)

        ans = ""
        tk_count = 0
        hist = deepcopy(history)
        for attempt in range(self.max_retries + 1):
            history = deepcopy(hist)
            try:
                for _round in range(self.max_rounds + 1):
                    api_history = _convert_history_for_hermes(history)
                    response = await self.async_client.chat.completions.create(
                        model=self.model_name,
                        messages=api_history,
                        **gen_conf,
                    )
                    tk_count += total_token_count_from_response(response)
                    if not response.choices or not response.choices[0].message:
                        raise Exception(f"500 response structure error. Response: {response}")

                    raw = response.choices[0].message.content or ""
                    calls, cleaned = _parse_hermes_tool_calls(raw)
                    if not calls:
                        ans += cleaned
                        if response.choices[0].finish_reason == "length":
                            ans = self._length_stop(ans)
                        return ans, tk_count

                    # Execute tool calls and append results to history.
                    history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "index": tc.index,
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        } for tc in calls],
                    })
                    for tc in calls:
                        try:
                            args = json_repair.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        try:
                            if hasattr(self.toolcall_session, "tool_call_async"):
                                result = await self.toolcall_session.tool_call_async(tc.function.name, args)
                            else:
                                result = await thread_pool_exec(self.toolcall_session.tool_call, tc.function.name, args)
                        except Exception as e:
                            result = f"Tool call failed: {e}"
                        result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                        history.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
                        ans += self._verbose_tool_use(tc.function.name, args, result)

                logging.warning(f"[OllamaHermes] Exceeded max rounds: {self.max_rounds}")
                return ans, tk_count
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    return e, tk_count

        assert False, "Shouldn't be here."

    async def async_chat_streamly_with_tools(self, system: str, history: list, gen_conf: dict = {}):
        gen_conf = self._clean_conf(gen_conf)
        tools = self.tools or []
        history = self._inject_hermes_prompt(system, history, tools)

        total_tokens = 0
        hist = deepcopy(history)

        for attempt in range(self.max_retries + 1):
            history = deepcopy(hist)
            try:
                for _round in range(self.max_rounds + 1):
                    api_history = _convert_history_for_hermes(history)
                    logging.info(f"[OllamaHermes] round={_round} model={self.model_name} tools={[t['function']['name'] for t in tools]}")

                    # Crucially: NO `tools` / `tool_choice` — Ollama would
                    # otherwise eat the response trying to parse them itself.
                    response = await self.async_client.chat.completions.create(
                        model=self.model_name,
                        messages=api_history,
                        stream=True,
                        **gen_conf,
                    )

                    answer = ""
                    async for resp in response:
                        if not resp.choices:
                            tol = total_token_count_from_response(resp)
                            if tol:
                                total_tokens = tol
                            continue
                        delta = resp.choices[0].delta
                        chunk = (delta.content or "") if hasattr(delta, "content") else ""
                        if chunk:
                            answer += chunk
                        tol = total_token_count_from_response(resp)
                        if tol:
                            total_tokens = tol
                        elif chunk:
                            total_tokens += num_tokens_from_string(chunk)

                    calls, cleaned = _parse_hermes_tool_calls(answer)
                    if not calls:
                        # Plain text answer — emit it and exit. We yield once at
                        # the end (rather than streaming token-by-token) so the
                        # user never sees any half-formed `<tool_call>` text;
                        # the model can also produce trailing chatter after a
                        # call, which we'd want hidden.
                        if cleaned:
                            yield cleaned
                        logging.info(f"[OllamaHermes] round={_round} text response, exiting")
                        yield total_tokens
                        return

                    # Tool-calls detected. Surface "Begin to call..." for each.
                    for tc in calls:
                        try:
                            args = json_repair.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        yield self._verbose_tool_use(tc.function.name, args, "Begin to call...")

                    # Execute in parallel.
                    async def _exec(tc):
                        try:
                            args = json_repair.loads(tc.function.arguments)
                        except Exception:
                            args = {}
                        try:
                            if hasattr(self.toolcall_session, "tool_call_async"):
                                result = await self.toolcall_session.tool_call_async(tc.function.name, args)
                            else:
                                result = await thread_pool_exec(self.toolcall_session.tool_call, tc.function.name, args)
                            return tc, args, result, None
                        except Exception as e:
                            logging.exception(f"[OllamaHermes] tool call failed: {tc.function.name}")
                            return tc, args, None, e

                    results = await asyncio.gather(*[_exec(tc) for tc in calls])

                    history.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "index": tc.index,
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        } for tc in calls],
                    })
                    for tc, args, result, err in results:
                        payload = err if err else result
                        result_str = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(payload)
                        history.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
                        yield self._verbose_tool_use(tc.function.name, args, payload)

                logging.warning(f"[OllamaHermes] Exceeded max rounds: {self.max_rounds}")
                yield total_tokens
                return

            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    logging.error(f"[OllamaHermes] giving up: {e}")
                    yield e
                    yield total_tokens
                    return

        assert False, "Shouldn't be here."

    @staticmethod
    def _inject_hermes_prompt(system: str, history: list, tools: list[dict]) -> list:
        """Ensure history starts with a system message that includes the
        Hermes tool spec (when tools are bound). Idempotent: appending more
        tools to an already-prepared history is a no-op."""
        history = list(history)
        if system and (not history or history[0].get("role") != "system"):
            history.insert(0, {"role": "system", "content": system})

        if not tools:
            return history

        spec = _hermes_system_section(tools)
        if history and history[0].get("role") == "system":
            if "<tools>" not in history[0]["content"]:
                history[0]["content"] = (history[0].get("content") or "") + spec
        else:
            history.insert(0, {"role": "system", "content": spec})
        return history
