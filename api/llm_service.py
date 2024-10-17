import os
from dotenv import load_dotenv
from mistralai.client import MistralClient
from mistralai.models import chat_completion
# Load environment variables
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

# Initialize MistralAI client
mistral_client = MistralClient(api_key=mistral_key)

def get_text_embedding(input_text):
    """Get text embeddings from the Mistral API."""
    model = "mistral-embed"
    response = mistral_client.embeddings(model = model,input_text=input_text)
    return response.data[0].embedding

def prompt_llm(prompt):
    """Generate a chat completion using the Mistral API."""
    model = "mistral-large-latest"
    
     # Create a chat completion request object
    messages = [
        chat_completion.ChatMessage(role="user", content=prompt)  # Use the correct model structure
    ]

    # Call the chat method with properly structured messages
    response = mistral_client.chat(
        model=model,
        messages=messages
    )

    return response

def extract_image_text(base64_image):
    """Extract text from a base64-encoded image using Pixtral."""
    import requests
    url = "https://api.mistral.ai/v1/chat/completions"

    # Prepare the headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {mistral_key}",
    }

    # Prepare the data payload
    data = {
        "model": "pixtral-12b-2409",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What’s in this image?"},
                    {"type": "image_url","image_url": f"data:image/jpeg;base64,{base64_image}"}
                ]
            }
        ],
        "max_tokens": 1500
    }

    # Make the request
    response = requests.post(url, headers=headers, json=data)

    image_text = ''
    # Check the response
    if response.status_code == 200:
        response_data = response.json()
        # Extract the text content
        extracted_text = response_data['choices'][0]['message']['content']
        print("Extracted Text:", extracted_text)  # Print th
        image_text = extracted_text
    else:
        print("Error:", response.status_code, response.text)
    return image_text

# Example usage:
if __name__ == '__main__':
    # Example: Chat completion
    # text_prompt = "What is the meaning of life according to Stoic philosophy?"
    # response = prompt_llm(text_prompt)
    # print(response)

    # # Example: Text embedding
    # embedding = get_text_embedding("Learn and grow every day.")
    # print(embedding)

    # Example: Image text extraction (base64 encoded image input)
    pdf_path = "//Users//osas//Downloads//test.jpeg"
    # Open and read image data
    with open(pdf_path, "rb") as pdf_file:
        image_data = pdf_file.read()
    import base64
    base64_image = base64.b64encode(image_data).decode('utf-8')
    extracted_text = extract_image_text(base64_image)
    
