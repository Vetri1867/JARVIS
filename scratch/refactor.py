import os

def refactor():
    path = "server.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Replace aider commands
    content = content.replace("gemini/gemini-2.5-flash", "openai/gpt-4o")

    # 2. Add OpenAI wrapper
    wrapper_code = """
from openai import AsyncOpenAI
import os

class _Models:
    def __init__(self, client):
        self.client = client
    
    async def generate_content(self, model, contents, config=None):
        messages = []
        if config and "system_instruction" in config:
            # Handle dictionary case or direct string
            sys_prompt = config["system_instruction"]
            if isinstance(sys_prompt, dict) and "parts" in sys_prompt:
                sys_prompt = sys_prompt["parts"][0]["text"]
            messages.append({"role": "system", "content": sys_prompt})
        
        # Add user message
        if isinstance(contents, list):
            # Complex multipart
            text_content = ""
            for part in contents:
                if hasattr(part, "text"):
                    text_content += part.text
                elif isinstance(part, dict) and "text" in part:
                    text_content += part["text"]
                else:
                    text_content += str(part)
            messages.append({"role": "user", "content": text_content})
        else:
            messages.append({"role": "user", "content": str(contents)})
            
        max_tokens = config.get("max_output_tokens") if config else None
        
        try:
            resp = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=max_tokens
            )
        except Exception as e:
            print(f"OpenAI API Error: {e}")
            class ErrorResp:
                text = f"API Error: {e}"
            return ErrorResp()
            
        class ResponseStub:
            def __init__(self, text):
                self.text = text
                
        return ResponseStub(resp.choices[0].message.content)

class _Aio:
    def __init__(self, client):
        self.models = _Models(client)

class OpenAIGenAIWrapper:
    def __init__(self, api_key):
        self._openai = AsyncOpenAI(api_key=api_key)
        self.aio = _Aio(self._openai)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
gemini_client = OpenAIGenAIWrapper(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
"""

    old_init = """GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None"""

    content = content.replace(old_init, wrapper_code.strip())

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    
    print("Refactored server.py successfully.")

if __name__ == "__main__":
    refactor()
