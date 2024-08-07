from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
import time
from dotenv import load_dotenv
import os
from gpt4all import GPT4All
import httpx 

# Load environment variables from a .env file in the current directory
load_dotenv()
mistral_key = os.getenv("MISTRAL_API_KEY")

def get_text_embedding(input):
    client = MistralClient(api_key = mistral_key)

    embeddings_batch_response = client.embeddings(
        model="mistral-embed",
        input=input
    )
    return embeddings_batch_response.data[0].embedding


def prompt_llm(prompt):
    try:
       
        api_key = mistral_key
        model = "mistral-medium-latest"
        client = MistralClient(api_key=api_key)

        messages = [
            ChatMessage(role="user", content=prompt)
        ]

     
        chat_response = client.chat(
                model=model,
                messages=messages,
        )

        return chat_response.choices[0].message.content
    

    except Exception as e:
        print(str(e))
        return "n/a"

# def prompt_slm(prompt):
#     model = GPT4All("Phi-3-mini-4k-instruct.Q4_0.gguf")
#     with model.chat_session():
#         return(model.generate(prompt))


if __name__ == '__main__':
    data = """ I observe, gentlemen, that when I would lead you on a new venture you no longer follow me with your old spirit. I have asked you to meet me that we may come to a decision together: are we, upon my advice, to go forward, or, upon yours, to turn back?

    If you have any complaint to make about the results of your efforts hitherto, or about myself as your commander, there is no more to say. But let me remind you: through your courage and endurance you have gained possession of Ionia, the Hellespont, both Phrygias, Cappadocia, Paphlagonia, Lydia, Caria, Lycia, Pamphylia, Phoenicia, and Egypt; the Greek part of Libya is now yours, together with much of Arabia, lowland Syria, Mesopotamia, Babylon, and Susia; Persia and Media with all the territories either formerly controlled by them or not are in your hands; you have made yourselves masters of the lands beyond the Caspian Gates, beyond the Caucasus, beyond the Tanais, of Bactria, Hyrcania, and the Hyrcanian sea; we have driven the Scythians back into the desert; and Indus and Hydaspes, Acesines and Hydraotes flow now through country which is ours. With all that accomplished, why do you hesitate to extend the power of Macedon–yourpower–to the Hyphasis and the tribes on the other side ? Are you afraid that a few natives who may still be left will offer opposition? Come, come! These natives either surrender without a blow or are caught on the run–or leave their country undefended for your taking; and when we take it, we make a present of it to those who have joined us of their own free will and fight on our side.

    For a man who is a man, work, in my belief, if it is directed to noble ends, has no object beyond itself; none the less, if any of you wish to know what limit may be set to this particular camapaign, let me tell you that the area of country still ahead of us, from here to the Ganges and the Eastern ocean, is comparatively small. You will undoubtedly find that this ocean is connected with the Hyrcanian Sea, for the great Stream of Ocean encircles the earth. Moreover I shall prove to you, my friends, that the Indian and Persian Gulfs and the Hyrcanian Sea are all three connected and continuous. Our ships will sail round from the Persian Gulf to Libya as far as the Pillars of Hercules, whence all Libya to the eastward will soon be ours, and all Asia too, and to this empire there will be no boundaries but what God Himself has made for the whole world.
 """
    data_prompt = (
            f"Based on the following text: {data}\n"
            f"Generate questions and answers.\n"
            f"Your response should be related to {data}.\n"
            f"Question 1: What is a relevant question?\n"
            f"Answer 1: A corresponding answer.\n"
            f"Question 2: What is another relevant question?\n"
            f"Answer 2: Another corresponding answer.\n"
        )
    resp_prompt = f"Answer the following question based on the provided text:{data}\n\n"
    resp_prompt += f"Question:  What reasons does the speaker give for continuing the campaign and expanding Macedonian power?\n\n"  
    ans  = prompt_slm(resp_prompt)        
    print(ans)