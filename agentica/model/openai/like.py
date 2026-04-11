from dataclasses import dataclass
from typing import Optional
from agentica.model.openai.chat import OpenAIChat
from agentica.utils.log import logger


@dataclass
class OpenAILike(OpenAIChat):
    id: str = "not-provided"
    name: str = "OpenAILike"
    api_key: Optional[str] = "not-provided"

    def __post_init__(self):
        super().__post_init__()
        # Warn early if api_key is still the placeholder — will fail at first API call with 401
        if self.api_key == "not-provided":
            logger.warning(
                f"OpenAILike(id='{self.id}'): api_key is 'not-provided'. "
                "Set api_key=<your key> or the appropriate environment variable, "
                "otherwise API calls will fail with a 401 authentication error."
            )
