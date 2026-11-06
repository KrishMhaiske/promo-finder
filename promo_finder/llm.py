"""Model-agnostic LLM adapter.

Swap models with env vars only — no code changes:
  LLM_PROVIDER=gemini            LLM_MODEL=gemini-3.8-flash      GEMINI_API_KEY=...
  LLM_PROVIDER=openai_compatible LLM_MODEL=gpt-4o / llama-3.3-70b-versatile / ...
                                 LLM_BASE_URL=https://api.openai.com/v1 (or Groq/OpenRouter/any)
                                 LLM_API_KEY=...
Every call returns parsed JSON. Responses are cached on disk (keyed by
provider+model+prompt) so re-runs are free and reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 min_interval: float | None = None, use_cache: bool = True):
        self.provider = (provider or os.getenv("LLM_PROVIDER", "gemini")).lower()
        default_model = "gemini-3.6-flash" if self.provider == "gemini" else "gpt-4o-mini"
        self.model = model or os.getenv("LLM_MODEL", default_model)
        # tried in order when the main model is overloaded (503), rate-limited (429) or times out
        default_fb = "gemini-3.5-flash,gemini-3.8-flash,gemini-3.5-flash-lite" if self.provider == "gemini" else ""
        fb = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", default_fb).split(",") if m.strip()]
        self.chain = [self.model] + [m for m in fb if m != self.model]
        self.timeout = float(os.getenv("LLM_TIMEOUT", "120"))
        self.models_used: dict[str, int] = {}
        self.exhausted: list[str] = []
        # each provider uses its own key, so switching providers never sends a key to the wrong service
        if self.provider == "gemini":
            self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        else:
            self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        # free-tier Gemini Flash is ~10 requests/minute -> space calls out
        self.min_interval = float(min_interval if min_interval is not None else os.getenv("LLM_MIN_INTERVAL", "6.5"))
        self.use_cache = use_cache
        self._last_call = 0.0
        self.calls = 0
        self.cache_hits = 0
        if not self.api_key:
            raise RuntimeError("No API key. Set GEMINI_API_KEY (or LLM_API_KEY) in .env or the environment.")
        if self.provider == "gemini":
            from google import genai
            from google.genai import types
            # SDK's own retries hid 503s and hung for minutes -> we handle retries + fallback ourselves
            self._client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(
                timeout=int(self.timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))

    @property
    def name(self) -> str:
        return f"{self.provider}:{self.model}"

    # ------------------------------------------------------------------ public
    def json(self, system: str, user: str, temperature: float = 0.4, retries: int | None = None) -> dict | list:
        key = hashlib.sha256(json.dumps([self.name, system, user, temperature]).encode()).hexdigest()[:24]
        cache_file = CACHE_DIR / f"{key}.json"
        if self.use_cache and cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))

        retries = retries or max(5, 2 * len(self.chain))
        last_err, idx = None, 0
        for attempt in range(retries):
            model = self.chain[idx % len(self.chain)]
            try:
                self._throttle()
                text = self._call(model, system, user, temperature)
                self.calls += 1
                data = _parse_json(text)
                self.models_used[model] = self.models_used.get(model, 0) + 1
                if self.use_cache:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                return data
            except Exception as e:
                last_err = e
                msg = str(e)
                head = msg[:80]
                if any(c in head for c in ("401", "403", "API_KEY_INVALID", "PERMISSION_DENIED")):
                    raise RuntimeError(f"Bad API key / permission ({model}): {msg[:300]}") from e
                if any(c in head for c in ("400", "404", "NOT_FOUND", "INVALID_ARGUMENT")):
                    # this model won't work for us -> drop it from the chain for the rest of the run
                    print(f"  [llm] {model} unusable ({head}); removing from chain")
                    self.chain = [m for m in self.chain if m != model]
                    if not self.chain:
                        raise RuntimeError(f"No usable model left. Last error: {msg[:300]}") from e
                    continue
                if "PerDay" in msg or "per day" in msg.lower():
                    # daily free-tier quota used up -> retrying this model today is pointless
                    print(f"  [llm] {model} daily quota exhausted; removing from chain")
                    self.exhausted.append(model)
                    self.chain = [m for m in self.chain if m != model]
                    if not self.chain:
                        raise RuntimeError("All models in the chain hit their daily quota. "
                                           "Wait for the reset or set LLM_MODEL/LLM_FALLBACK_MODELS.") from e
                    continue
                overloaded = any(c in msg for c in ("503", "UNAVAILABLE", "timed out", "Timeout", "429", "RESOURCE_EXHAUSTED"))
                if overloaded:
                    idx += 1  # move to next model in the fallback chain
                wait = 5 if overloaded else 3 * (attempt + 1)   # malformed JSON etc. -> retry same model
                print(f"  [llm] {model} attempt {attempt + 1} failed ({head}); "
                      f"{'switching to ' + self.chain[idx % len(self.chain)] if overloaded else 'retrying'} in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}")

    # ----------------------------------------------------------------- private
    def _throttle(self):
        gap = time.time() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.time()

    def _call(self, model: str, system: str, user: str, temperature: float) -> str:
        if self.provider == "gemini":
            from google.genai import types
            resp = self._client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )
            return resp.text or ""
        if self.provider in ("openai", "openai_compatible", "groq", "openrouter"):
            import requests
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                },
                timeout=self.timeout,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} {r.text[:300]}")
            return r.json()["choices"][0]["message"]["content"]
        raise ValueError(f"Unknown LLM_PROVIDER '{self.provider}'")


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
