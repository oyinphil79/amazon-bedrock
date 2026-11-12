"""Model registry: short aliases -> Amazon Bedrock model IDs.

Switching models is just a matter of passing a different alias (or any raw
Bedrock model / inference-profile ID) to the agent. Everything goes through the
Bedrock Converse API, so the agent code is identical for every model.

Check which models are enabled in your account/region with:
    aws bedrock list-foundation-models --region us-west-2 \
        --query "modelSummaries[?contains(modelId,'qwen') || contains(modelId,'gpt')].modelId"
"""

import os

MODELS: dict[str, str] = {
    # Qwen (Alibaba) models on Bedrock
    "qwen": "qwen.qwen3-32b-v1:0",
    "qwen-large": "qwen.qwen3-235b-a22b-2507-v1:0",
    "qwen-coder": "qwen.qwen3-coder-30b-a3b-v1:0",
    # OpenAI open-weight GPT models on Bedrock
    "gpt": "openai.gpt-oss-120b-1:0",
    "gpt-small": "openai.gpt-oss-20b-1:0",
}

DEFAULT_MODEL = os.environ.get("SQL_AGENT_MODEL", "qwen")
DEFAULT_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"


def resolve_model_id(name: str) -> str:
    """Return the Bedrock model ID for an alias; unknown names pass through unchanged
    so any Bedrock model ID or inference profile ARN can be used directly."""
    return MODELS.get(name, name)
