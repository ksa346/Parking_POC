"""
Chat Service — GPT-5.2 powered parking assistant using pydantic-ai
"""
import logging
import os
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.models.schemas import ChatRequest, ChatResponse, ParkingContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are the **Elmhurst Public Library Smart Parking Assistant**.
Respond concisely. You may use simple HTML tags (<strong>, <em>, <ul>, <li>) for formatting.

Current live data (refreshed every 15 s):
- Total spots: {total_spots}
- Occupied: {occupied_spots}
- Available: {available_spots}
- Occupancy: {occupancy_percent:.1f}%
- Detection confidence: {confidence}
- Detection method: {detection_method}
{zone_lines}
{stats_lines}
{history_section}
{forecast_section}

Guidelines:
1. When asked about availability, reference the live numbers above.
2. When asked about a specific zone, give its occupied/total/available.
3. When asked about past occupancy, trends, or "what was it like X hours ago",
   use the HISTORY DATA above.
4. When asked about future occupancy, best time to come, or forecasts,
   use the FORECAST DATA above.
5. When asked for analysis, patterns, or insights, use both history and forecast data.
6. Keep answers friendly, brief, and accurate.
7. If the data shows no history/forecast yet, let the user know the system is
   still collecting data and will have better answers soon.
8. Never fabricate numbers — only use data provided above.
"""


class ChatService:
    """GPT-5.2 powered parking chat assistant"""

    def __init__(self):
        self.api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.model_name: str = os.getenv("OPENAI_MODEL", "gpt-4.1")
        self.configured = bool(self.api_key)
        self._agent: Optional[Agent] = None

        if self.configured:
            try:
                provider = OpenAIProvider(api_key=self.api_key)
                model = OpenAIModel(self.model_name, provider=provider)
                self._agent = Agent(
                    model=model,
                    system_prompt="You are a parking assistant.",  # overridden per-call
                )
                logger.info(f"ChatService initialised with model {self.model_name}")
            except Exception as exc:
                logger.error(f"Failed to initialise ChatService: {exc}")
                self.configured = False
        else:
            logger.warning("OPENAI_API_KEY not set — chat will use fallback mode")

    # ------------------------------------------------------------------
    def _build_system_prompt(self, ctx: ParkingContext) -> str:
        zone_lines = "\n".join(
            f"  - Zone {z.zone_id}: {z.occupied}/{z.total} occupied, {z.available} available"
            for z in ctx.zones
        ) if ctx.zones else "  (zone data unavailable)"

        stats_parts = []
        if ctx.peak_hour is not None:
            h = ctx.peak_hour
            label = f"{h} AM" if h < 12 else ("12 PM" if h == 12 else f"{h-12} PM")
            if h == 0:
                label = "12 AM"
            stats_parts.append(f"- Today's peak hour: {label}")
        if ctx.today_average is not None:
            stats_parts.append(f"- Today's average occupancy: {ctx.today_average:.1f} vehicles")
        stats_lines = "\n".join(stats_parts) if stats_parts else ""

        history_section = ""
        if ctx.history_summary:
            history_section = f"\n--- HISTORY DATA ---\n{ctx.history_summary}"

        forecast_section = ""
        if ctx.forecast_summary:
            forecast_section = f"\n--- FORECAST DATA ---\n{ctx.forecast_summary}"

        return SYSTEM_PROMPT_TEMPLATE.format(
            total_spots=ctx.total_spots,
            occupied_spots=ctx.occupied_spots,
            available_spots=ctx.available_spots,
            occupancy_percent=ctx.occupancy_percent,
            confidence=ctx.confidence,
            detection_method=ctx.detection_method,
            zone_lines=zone_lines,
            stats_lines=stats_lines,
            history_section=history_section,
            forecast_section=forecast_section,
        )

    # ------------------------------------------------------------------
    async def chat(
        self,
        request: ChatRequest,
        context: ParkingContext,
    ) -> ChatResponse:
        """Send a user message to GPT with live parking context."""
        if not self.configured or self._agent is None:
            return self._fallback(request.message, context)

        system_prompt = self._build_system_prompt(context)

        # Build conversation history for context
        user_message = request.message
        if request.history:
            history_lines = []
            for msg in request.history:
                prefix = "User" if msg.role == "user" else "Assistant"
                history_lines.append(f"{prefix}: {msg.content}")
            history_text = "\n".join(history_lines)
            user_message = (
                f"Previous conversation:\n{history_text}\n\n"
                f"Current question: {request.message}"
            )

        try:
            result = await self._agent.run(
                user_message,
                instructions=system_prompt,
            )
            reply_text: str = result.output if hasattr(result, "output") else str(result.data)

            tokens_used = None
            if hasattr(result, "usage") and result.usage:
                tokens_used = getattr(result.usage, "total_tokens", None)

            return ChatResponse(
                reply=reply_text,
                model=self.model_name,
                tokens_used=tokens_used,
            )
        except Exception as exc:
            logger.error(f"GPT chat error: {exc}")
            return self._fallback(request.message, context)

    # ------------------------------------------------------------------
    def _fallback(self, message: str, ctx: ParkingContext) -> ChatResponse:
        """Keyword-based fallback when OpenAI is unavailable."""
        q = message.lower()

        if any(w in q for w in ("available", "open", "free", "empty")):
            zone_str = ", ".join(
                f"Zone {z.zone_id}: {z.available} open" for z in ctx.zones
            )
            reply = (
                f"There are currently <strong>{ctx.available_spots}</strong> spots "
                f"available out of {ctx.total_spots}. {zone_str}."
            )
        elif any(w in q for w in ("occupied", "full", "busy", "crowded")):
            reply = (
                f"The lot is <strong>{ctx.occupancy_percent:.1f}%</strong> occupied "
                f"— {ctx.occupied_spots} vehicles detected."
            )
        elif any(w in q for w in ("handicap", "accessible", "ada")):
            zone = next((z for z in ctx.zones if z.zone_id == "H"), None)
            if zone:
                reply = f"There are <strong>{zone.available}</strong> accessible spots open out of {zone.total}."
            else:
                reply = "Handicap zone data is currently unavailable."
        elif any(w in q for w in ("peak", "busiest")):
            if ctx.peak_hour is not None:
                h = ctx.peak_hour
                label = f"{h} AM" if h < 12 else ("12 PM" if h == 12 else f"{h-12} PM")
                if h == 0:
                    label = "12 AM"
                reply = f"Today's busiest hour so far is <strong>{label}</strong>."
            else:
                reply = "Not enough data yet to determine the peak hour."
        else:
            reply = (
                f"The lot currently has <strong>{ctx.available_spots}</strong> of "
                f"{ctx.total_spots} spots open ({ctx.occupancy_percent:.1f}% full). "
                "Ask me about available spots, zone info, peak hours, or accessibility!"
            )

        return ChatResponse(
            reply=reply,
            model="fallback",
            tokens_used=None,
        )
