"""Lazy OpenAI client wrapper shared by model-consuming services."""

from __future__ import annotations

from functools import cached_property
from typing import Any, Sequence, TypeVar

from openai import OpenAI
from pydantic import BaseModel


ParsedModelT = TypeVar("ParsedModelT", bound=BaseModel)


class OpenAIGateway:
    """Create the SDK client only when a model operation is requested."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @cached_property
    def client(self) -> OpenAI:
        return OpenAI(api_key=self._api_key)

    def create_response(self, *, model: str, input: Any, **kwargs: Any) -> Any:
        return self.client.responses.create(model=model, input=input, **kwargs)

    def create_embeddings(
        self, *, model: str, input: str | Sequence[str], **kwargs: Any
    ) -> Any:
        return self.client.embeddings.create(model=model, input=input, **kwargs)

    def parse_response(
        self,
        *,
        model: str,
        input: Any,
        text_format: type[ParsedModelT],
        **kwargs: Any,
    ) -> Any:
        """Request a Responses API result parsed into a Pydantic model."""

        return self.client.responses.parse(
            model=model,
            input=input,
            text_format=text_format,
            **kwargs,
        )
