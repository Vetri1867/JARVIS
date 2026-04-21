import asyncio
import logging
import os
import json
from dotenv import load_dotenv
from livekit.agents import JobContext, WorkerOptions, cli, llm, voice
from livekit.plugins import openai, silero, google
from livekit import rtc

# Import existing actions from actions.py with correct names
from actions import (
    open_browser as actions_open_browser,
    open_terminal as actions_open_terminal,
    open_aider_in_project as actions_build_project,
    open_path as actions_open_path
)

load_dotenv()

logger = logging.getLogger("shadow-agent")
logger.setLevel(logging.INFO)

SHADOW_SYSTEM_PROMPT = """
You are SHADOW (Systemic Highly Advanced Digital Operational Warden), a sophisticated British-accented AI assistant.
You serve your master, referred to as 'my lord' or 'sir'.
Your personality is refined, efficient, and slightly formal, akin to a high-tech butler.

You have access to several tools:
1. browsing: Open URLs or search the web.
2. terminal: Execute commands in the system shell.
3. build: Create full-stack projects or websites using advanced scaffolding.
4. info: Get weather, news, or current time.

When a user asks to 'build' or 'create' a project, use the build_project tool.
Always be polite and proactive.
"""

class ShadowActions:
    @llm.function_tool
    async def open_browser(self, url: str):
        """Opens a specific URL in the web browser."""
        logger.info(f"Action: Opening browser -> {url}")
        return await actions_open_browser(url)

    @llm.function_tool
    async def web_search(self, query: str):
        """Searches the web for information."""
        logger.info(f"Action: Searching web -> {query}")
        from urllib.parse import quote
        url = f"https://www.google.com/search?q={quote(query)}"
        return await actions_open_browser(url)

    @llm.function_tool
    async def run_terminal(self, command: str):
        """Executes a command in the system terminal."""
        logger.info(f"Action: Running terminal -> {command}")
        return await actions_open_terminal(command)

    @llm.function_tool
    async def create_project(self, prompt: str):
        """Builds a full project, website, or application based on a description."""
        logger.info(f"Action: Building project -> {prompt}")
        from platform_utils import DESKTOP_PATH
        import re
        name = re.sub(r"[^a-zA-Z0-9\s-]", "", prompt).strip()
        project_name = re.sub(r"[\s]+", "-", name.lower())[:30] or "shadow-project"
        project_dir = str(DESKTOP_PATH / project_name)
        os.makedirs(project_dir, exist_ok=True)
        return await actions_build_project(project_dir, prompt)

    @llm.function_tool
    async def check_weather(self, location: str = "London"):
        """Gets current weather information."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://wttr.in/{location}?format=3")
                return resp.text.strip()
        except Exception:
            return f"I couldn't fetch the weather for {location}, sir."

    @llm.function_tool
    async def check_time(self):
        """Gets the current system time."""
        from datetime import datetime
        now = datetime.now().strftime("%I:%M %p")
        return f"It is currently {now}, my lord."

async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    await ctx.connect()

    # Create the standard ChatContext with the system prompt
    chat_ctx = llm.ChatContext().append(
        role="system",
        text=SHADOW_SYSTEM_PROMPT
    )

    # Initialize the LLM (Using OpenAI gpt-4o-mini for maximum stability as requested)
    llm_plugin = openai.LLM(
        model="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # Initialize STT and TTS (OpenAI)
    stt_plugin = openai.STT(api_key=os.getenv("OPENAI_API_KEY"))
    tts_plugin = openai.TTS(
        voice="alloy", 
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # Initialize the Voice Agent
    agent = voice.Agent(
        vad=silero.VAD.load(),
        stt=stt_plugin,
        llm=llm_plugin,
        tts=tts_plugin,
        chat_ctx=chat_ctx,
        fnc_ctx=ShadowActions(),
    )

    # Start the agent in the room
    agent.start(ctx.room)

    # --- Debug Logging ---
    @agent.on("user_started_speaking")
    def on_user_speech_start():
        logger.info("🎤 User started speaking...")

    @agent.on("user_speech_committed")
    def on_user_speech_committed(msg: llm.ChatMessage):
        logger.info(f"🔥 USER SAID: {msg.content}")
        # Broadcast transcript to frontend
        asyncio.create_task(ctx.room.local_participant.publish_data(
            json.dumps({"type": "transcript", "text": msg.content, "role": "user"}),
            reliable=True
        ))

    @agent.on("agent_speech_started")
    def on_agent_speech_start():
        logger.info("🔊 Agent started speaking...")

    @agent.on("agent_speech_committed")
    def on_agent_speech_committed(msg: llm.ChatMessage):
        logger.info(f"🤖 AGENT RESPONDED: {msg.content}")
        # Broadcast transcript to frontend
        asyncio.create_task(ctx.room.local_participant.publish_data(
            json.dumps({"type": "transcript", "text": msg.content, "role": "assistant"}),
            reliable=True
        ))
    # ---------------------

    # Initial greeting
    await agent.say("I am online and ready to serve, my lord.", allow_interruptions=True)

    # Handle incoming chat messages (DataPackets) from the HUD text input
    @ctx.room.on("data_received")
    def on_data_received(data: rtc.DataPacket):
        if data.participant:
            try:
                payload = json.loads(data.data.decode())
                if payload.get("type") == "transcript":
                    text = payload.get("text")
                    logger.info(f"Received chat transcript: {text}")
                    # Append user message to chat context and trigger response
                    chat_ctx.append(role="user", text=text)
                    
                    # Generate response from LLM and speak it
                    async def respond():
                        try:
                            # Use the agent's internal LLM to generate a response
                            # We can just use agent.say() with a stream if we want, 
                            # but for text input, we trigger the agent's generation logic.
                            stream = agent.llm.chat(chat_ctx=chat_ctx, fnc_ctx=agent.fnc_ctx)
                            await agent.say(stream, allow_interruptions=True)
                        except Exception as e:
                            logger.error(f"Error in text-to-response: {e}")
                    
                    asyncio.create_task(respond())
            except Exception as e:
                logger.error(f"Error handling data packet: {e}")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
