from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from dotenv import load_dotenv
import os
# Load environment variables from a .env file in the current directory
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")


def test_bot(final_prompt):

    api_key = mistral_key
    model = "mistral-medium"
    client = MistralClient(api_key=api_key)

    messages = [
                ChatMessage(role="user", content=final_prompt)
            ]
    # No streaming
    chat_response = client.chat(
                model=model,
                messages=messages,
            )

    mistral_summary = chat_response.choices[0].message.content
    return mistral_summary



if __name__ == '__main__':  
    pass