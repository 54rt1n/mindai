# mindai/server/modules/completion/route.py
# MindAI © 2025 by Martin Bukowski is licensed under CC BY-NC-SA 4.0 

import asyncio
import logging
import time
import uuid
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer
from fastapi.responses import StreamingResponse

from ....config import ChatConfig
from ....llm.models import LanguageModelV2, LLMProviderError, CompletionProvider
from .dto import CompletionRequest, CompletionResponse

logger = logging.getLogger(__name__)

class CompletionModule:
    def __init__(self, config: ChatConfig, security: HTTPBearer):
        self.router = APIRouter(prefix="/v1", tags=["completions"])
        self.security = security
        self.config = config
        self.models = LanguageModelV2.index_models(self.config)
        
        self.setup_routes()

    def setup_routes(self):
        @self.router.get("/models")
        async def chat_models():
            self.models = LanguageModelV2.index_models(self.config)
            return {"models": list(self.models.values())}

        @self.router.post("/completions")
        async def completions(request: CompletionRequest):
            try:
                provider = self.models[request.model].completion_factory(self.config)

                completion_params = request.model_dump(exclude_unset=True)
                response_id = str(uuid.uuid4())
                created_time = int(time.time())

                if completion_params.get("stream", False):
                    return StreamingResponse(
                        self._stream_response(provider, request, response_id, created_time),
                        media_type="text/event-stream"
                    )

                # Non-streaming response
                full_response = ""
                print("Response: ", end="")
                for chunk in provider.stream_completion(
                    config=self.config,
                    **completion_params
                ):
                    print(chunk, flush=True, end="")
                    if chunk:
                        full_response += chunk
                        
                print()  # Print a newline after the response is complete

                return CompletionResponse(
                    id=response_id,
                    created=created_time,
                    model=request.model,
                    choices=[{
                        "text": full_response,
                        "index": 0,
                        "logprobs": None,
                        "finish_reason": "stop"
                    }],
                    usage={
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0
                    }
                )

            except LLMProviderError as e:
                logger.error(f"Provider error: {str(e)}")
                raise HTTPException(status_code=400, detail=str(e))

            except Exception as e:
                logger.error(f"Completion error: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

    async def _stream_response(
        self,
        provider: CompletionProvider,
        request: CompletionRequest,
        response_id: str,
        created_time: int
    ) -> AsyncGenerator[str, None]:
        completion_params = request.model_dump(exclude_unset=True)
        full_response = ""

        try:
            for chunk in provider.stream_completion(
                config=self.config,
                **completion_params
            ):
                if chunk:
                    full_response += chunk
                    chunk_data = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [{
                            "text": chunk,
                            "index": 0,
                            "logprobs": None,
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                    await asyncio.sleep(0)

            # Send final chunk
            final_chunk = {
                "id": response_id,
                "object": "text_completion.chunk",
                "created": created_time,
                "model": request.model,
                "choices": [{
                    "text": "",
                    "index": 0,
                    "logprobs": None,
                    "finish_reason": "stop"
                }]
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            print("Client lost connection.")
            provider.running = False