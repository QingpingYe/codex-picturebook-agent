"""Strict validation for generated image prompt payloads."""


class PromptContractError(ValueError):
    pass


def validate_prompt_payload(payload: dict) -> None:
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise PromptContractError("pages must be a non-empty list")

    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise PromptContractError(f"page {index} must be an object")

        for field in ("page", "prompt", "prompt_text"):
            if field not in page:
                raise PromptContractError(f"page {index} missing {field}")

        if not isinstance(page["prompt"], list) or not page["prompt"]:
            raise PromptContractError(f"page {index} prompt must be a non-empty list")
        if not isinstance(page["prompt_text"], str) or not page["prompt_text"].strip():
            raise PromptContractError(f"page {index} prompt_text must be non-empty")

        staging = page.get("staging")
        if not isinstance(staging, dict) or not isinstance(
            staging.get("frame_laws"), list
        ):
            raise PromptContractError(f"page {index} missing staging.frame_laws")
