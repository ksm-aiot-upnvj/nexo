from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.event_manager import generate_dynamic_event_message


@pytest.mark.asyncio
async def test_generate_dynamic_event_message_fallback_on_unsupported():
    res = await generate_dynamic_event_message(
        event_type="unknown_type",
        event_name="Workshop AIoT",
        fallback_text="Fallback text",
    )
    assert res == "Fallback text"


@pytest.mark.asyncio
async def test_generate_dynamic_event_message_fallback_on_exception():
    with patch(
        "services.llm.client.ai_client.chat.completions.create",
        side_effect=Exception("LLM offline"),
    ):
        res = await generate_dynamic_event_message(
            event_type="completed",
            event_name="Webinar IoT ESP32",
            event_description="Membahas telemetri sensor",
            fallback_text="Pesan statis fallback",
            role_mention="<@&123456>",
            timeout_sec=1.0,
        )
        assert res == "Pesan statis fallback"


@pytest.mark.asyncio
async def test_generate_dynamic_event_message_success_with_role():
    mock_choice = MagicMock()
    mock_choice.message.content = "Terima kasih banyak atas kehadiran teman-teman di sesi Webinar IoT hari ini! Sampai jumpa di event berikutnya! 🙌"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch(
        "services.llm.client.ai_client.chat.completions.create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response
        res = await generate_dynamic_event_message(
            event_type="completed",
            event_name="Webinar IoT ESP32",
            event_description="Membahas telemetri sensor",
            fallback_text="Pesan statis fallback",
            role_mention="<@&123456>",
            timeout_sec=10.0,
        )
        assert "Terima kasih banyak atas kehadiran" in res
        assert "<@&123456>" in res
