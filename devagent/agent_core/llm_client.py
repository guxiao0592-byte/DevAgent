"""Unified LLM client supporting both OpenAI and DeepSeek.

Handles JSON repair for truncated responses.
"""

import json
import os
import re
import time
import requests
from typing import Optional


class LLMClient:
    """Unified LLM client with OpenAI and DeepSeek support."""

    def __init__(self, config: dict):
        self.config = config
        provider = config.get("provider", "deepseek")
        provider_config = config.get(provider, {})

        self.api_key = provider_config.get("api_key", "")
        if not self.api_key and provider == "openai":
            self.api_key = os.environ.get("OPENAI_API_KEY", "")

        self.base_url = provider_config.get("base_url", "https://api.deepseek.com")
        self.model = provider_config.get("model", "deepseek-chat")
        self.temperature = provider_config.get("temperature", 0.3)
        self.max_tokens = provider_config.get("max_tokens", 8192)
        self.provider = provider

        self.api_base = self.base_url.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base = f"{self.api_base}/v1"

    def chat(self, messages: list[dict], system_prompt: Optional[str] = None,
             response_format: Optional[dict] = None) -> str:
        """Send a chat completion request."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})

        payload["messages"].extend(messages)

        if response_format:
            payload["response_format"] = response_format

        last_error = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=180
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_error = e
                import time as _time
                _time.sleep(1.0 * (attempt + 1))  # 1s, 2s, 3s backoff
                continue
            except requests.exceptions.RequestException as e:
                last_error = e
                break  # Don't retry on non-transient errors

        error_msg = f"LLM API call failed: {last_error}"
        if hasattr(last_error, 'response') and last_error.response is not None:
            try:
                detail = last_error.response.json()
                error_msg += f" | Detail: {detail}"
            except (json.JSONDecodeError, AttributeError):
                error_msg += f" | Status: {last_error.response.status_code}"
        raise RuntimeError(error_msg)

    def chat_structured(self, messages: list[dict], system_prompt: Optional[str] = None) -> dict:
        """Send a chat completion and parse JSON response.

        Attempts multiple strategies to recover valid JSON:
        1. Direct json.loads
        2. Extract from markdown code blocks
        3. Repair truncated JSON (add missing closing brackets)
        """
        content = self.chat(
            messages=messages,
            system_prompt=system_prompt,
            response_format={"type": "json_object"}
        )

        result = self._parse_json_safe(content)
        if result is not None:
            return result

        raise ValueError(
            f"Failed to parse LLM response as JSON after all recovery attempts. "
            f"Response length: {len(content)} chars. "
            f"First 300 chars: {content[:300]}"
        )

    def _parse_json_safe(self, content: str) -> Optional[dict]:
        """Try multiple strategies to parse JSON from LLM output."""
        # Strategy 1: Direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Strategy 3: Find first { and try regions
        first_brace = content.find('{')
        if first_brace >= 0:
            candidate = content[first_brace:]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # Strategy 4: Try to repair truncated JSON
                repaired = self._repair_truncated_json(candidate)
                if repaired is not None:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass

        return None

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[str]:
        """Attempt to repair a truncated JSON string using character-level state tracking.

        Handles:
        - Truncated inside a string value (most common with LLM truncation)
        - Missing closing braces/brackets
        - Trailing commas before closing
        """
        # Quick check for already-valid
        text = text.rstrip()
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        # Character-level state machine
        in_string = False
        escape = False
        brace_depth = 0
        bracket_depth = 0
        last_non_ws = ''

        for c in text:
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                brace_depth += 1
            elif c == '}':
                brace_depth -= 1
            elif c == '[':
                bracket_depth += 1
            elif c == ']':
                bracket_depth -= 1
            if not c.isspace():
                last_non_ws = c

        repaired = text.rstrip().rstrip(',')

        # If we were inside a string, close it
        if in_string:
            repaired += '"'

        # Fix trailing colon or incomplete value (e.g.,  "key":  or  "key": "value)
        # by removing the incomplete last entry
        if last_non_ws == ':' or last_non_ws == ',':
            # Remove the last entry — find last complete key-value
            repaired = repaired.rstrip().rstrip(',').rstrip()

        # Balance braces/brackets (recount after our repairs)
        brace_open = repaired.count('{') - repaired.count('}')
        bracket_open = repaired.count('[') - repaired.count(']')

        repaired += '}' * max(0, brace_open)
        repaired += ']' * max(0, bracket_open)

        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            return None


    def chat_stream(self, messages: list[dict], system_prompt: Optional[str] = None,
                    on_token: Optional[callable] = None) -> str:
        """Stream chat completion, calling on_token for each content delta.

        Uses SSE streaming from OpenAI-compatible API endpoints.
        Supports both OpenAI and DeepSeek providers.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system-level instruction
            on_token: Callback receiving each token string as it arrives.
                      Signature: on_token(token: str)

        Returns:
            The complete response text (all tokens concatenated)
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }

        if system_prompt:
            payload["messages"].append({"role": "system", "content": system_prompt})
        payload["messages"].extend(messages)

        try:
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=300,
                stream=True
            )
            resp.raise_for_status()

            full_content: list[str] = []
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                if on_token:
                                    on_token(content)
                                full_content.append(content)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

            return "".join(full_content)

        except requests.exceptions.RequestException as e:
            error_msg = f"LLM streaming call failed: {e}"
            if hasattr(e, 'response') and e.response is not None:
                try:
                    detail = e.response.json()
                    error_msg += f" | Detail: {detail}"
                except (json.JSONDecodeError, AttributeError):
                    error_msg += f" | Status: {e.response.status_code}"
            raise RuntimeError(error_msg)

    def supports_streaming(self) -> bool:
        """Check if the configured provider supports streaming."""
        return True

    def test_connection(self) -> tuple[bool, str]:
        """Test the LLM API connection with a minimal request. Returns (ok, message)."""
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
            }
            resp = requests.post(
                f"{self.api_base}/chat/completions",
                headers=headers, json=payload, timeout=10
            )
            if resp.status_code == 200:
                return True, f"OK (model={self.model})"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)[:200]
