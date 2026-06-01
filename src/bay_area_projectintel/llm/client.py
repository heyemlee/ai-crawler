from __future__ import annotations

import json

from openai import OpenAI

from bay_area_projectintel.config import RuntimeSettings
from bay_area_projectintel.llm.prompts import classification_prompt
from bay_area_projectintel.models import Category, ClassificationResult


class DeepSeekClassifier:
    def __init__(self, settings: RuntimeSettings):
        self.settings = settings
        self.enabled = bool(settings.deepseek_api_key)
        self._client = (
            OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
            if self.enabled
            else None
        )

    def classify(self, description: str) -> ClassificationResult | None:
        if not self._client:
            return None
        response = self._client.chat.completions.create(
            model=self.settings.deepseek_model,
            messages=[{"role": "user", "content": classification_prompt(description)}],
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
        return ClassificationResult(
            category=Category(data["category"]),
            confidence=float(data.get("confidence", 0.7)),
            reason=str(data.get("reason", "DeepSeek classification")),
        )
