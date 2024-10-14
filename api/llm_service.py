import time
from dotenv import load_dotenv
import os
from gpt4all import GPT4All
import requests 

def prompt_slm(prompt):
    model = GPT4All("Phi-3-mini-4k-instruct.Q4_0.gguf")
    with model.chat_session():
        return(model.generate(prompt))



# Load environment variables from a .env file in the current directory
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

def get_mistral_response(url , api_key, model_name, user_message):
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    if ('embeddings' in url):
        data = {
        "input": user_message,
        "model": model_name,
        "encoding_format": "float"
        }
    else:
        if ('pixtral' in model_name):
                    data = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extract the text from this image"
                            },
                            {
                                "type": "image_url",
                "image_url": f"data:image/jpeg;base64,{user_message}"
                            }
                        ]
                    }
                ],
                "max_tokens": 2056
            }
        else:

            data = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": user_message
                    }
                ]
            }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()  # Check if the request was successful
         # Convert the response to JSON format
        chat_response = response.json()     
        # Accessing the message content from the first choice
        if ('embeddings' in url):
            return chat_response['data'][0]['embedding']
        else:
            return chat_response['choices'][0]['message']['content']
     # Return the API response as a Python dictionary
    except requests.exceptions.HTTPError as err:
        return {"error": str(err)}

def get_text_embedding(input):
    # Example usage:
    url = "https://api.mistral.ai/v1/embeddings"
    api_key = mistral_key  # Replace with your actual API key
    model_name ="mistral-embed"  # Model name can be passed dynamically
    user_message = input 
    embeddings_batch_response = get_mistral_response(url, api_key, model_name, user_message)
    return embeddings_batch_response

def prompt_llm(prompt):
    try:
        url = "https://api.mistral.ai/v1/chat/completions"
        api_key = mistral_key
        model_name = "mistral-medium-latest"
        user_message = prompt 
        chat_response = get_mistral_response(url, api_key, model_name, user_message)
        return chat_response 
    except Exception as e:
        print(str(e))
        return "n/a"

def extract_image_text(base64_image):
    try:
        url = "https://api.mistral.ai/v1/chat/completions"
        api_key = mistral_key
        model_name = "pixtral-12b-2409"
        user_message = base64_image 
        chat_response = get_mistral_response(url, api_key, model_name, user_message)
        return chat_response

    except Exception as e:
        print(str(e))
        return "n/a"




