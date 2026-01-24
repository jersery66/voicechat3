# LLM Service - Ollama Integration

import os
import sys
from typing import Generator, List, Dict, Any
import ollama

# Add parent directory to path for config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OLLAMA_MODEL, OLLAMA_HOST, SYSTEM_PROMPT


class LLMService:
    """LLM service using Ollama for text generation."""
    
    def __init__(self, model: str = None, host: str = OLLAMA_HOST):
        self.host = host
        self.client = ollama.Client(host=host)
        self.conversation_history: List[Dict[str, str]] = []
        self.system_prompt = SYSTEM_PROMPT
        
        # Auto-detect model if not specified
        if model is None:
            available = self.get_available_models()
            self.model = available[0] if available else OLLAMA_MODEL
        else:
            self.model = model
        
    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []
        
    def set_system_prompt(self, prompt: str):
        """Set the system prompt."""
        self.system_prompt = prompt
        
        # Build messages list with system prompt
        current_system_prompt = self.system_prompt
        # Check for system suffix in options (passed via kwargs or we can add explicit arg)
        # But we need to change signature. Let's start with just modifying chat.
        
    def chat(self, user_message: str, system_suffix: str = None) -> Generator[str, None, None]:
        """
        Send a message and yield streamed response chunks.
        
        Args:
            user_message: The user's input message
            system_suffix: Optional temporary instruction appended to system prompt for this turn only
            
        Yields:
            Response text chunks as they arrive
        """
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })
        
        # Build messages list with system prompt
        current_system_prompt = self.system_prompt
        if system_suffix:
            current_system_prompt += "\n" + system_suffix
            
        messages = [{"role": "system", "content": current_system_prompt}]
        messages.extend(self.conversation_history)
        
        # Stream response
        full_response = ""
        try:
            stream = self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={
                    "stop": ["User:", "Visitor:", "用户:", "来访者:", "Human:", "Assistant:", "\n\n", "心医生:", "心医生："]
                }
            )
            
            for chunk in stream:
                if "message" in chunk and "content" in chunk["message"]:
                    content = chunk["message"]["content"]
                    full_response += content
                    yield content
                    
        except Exception as e:
            # Log error to console but DO NOT yield it as chat text
            print(f"[ERROR] LLM Generation Failed: {e}")
            # Raise exception so main pipeline can handle it (or ignore)
            raise e
            
        # Add assistant response to history
        self.conversation_history.append({
            "role": "assistant",
            "content": full_response
        })
        
    def chat_sync(self, user_message: str) -> str:
        """
        Send a message and return the complete response.
        
        Args:
            user_message: The user's input message
            
        Returns:
            Complete response text
        """
        return "".join(self.chat(user_message))
    
    def get_available_models(self) -> List[str]:
        """Get list of available Ollama models."""
        try:
            models = self.client.list()
            return [m["name"] for m in models.get("models", [])]
        except Exception:
            return []
        
    def test_connection(self) -> bool:
        """Test if Ollama is reachable."""
        try:
            self.client.list()
            return True
        except Exception:
            return False

    def warmup(self) -> bool:
        """Warmup the model by sending a dummy request."""
        try:
            # Send a short request to force model load
            self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                stream=False
            )
            return True
        except Exception as e:
            print(f"LLM Warmup failed: {e}")
            return False


# Singleton instance
_llm_service = None

def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
