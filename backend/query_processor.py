from llm_service import prompt_llm
import re
import json

def process(query, top_k_results):
    best_answer_data = choose_best_answer(query, top_k_results)
    return best_answer_data
    
def choose_best_answer(query, top_k_results):
    # Constructing the prompt string
    prompt = "Here are some chunks of text along with their details:\n"
    for result in top_k_results:
        prompt += f"Chunk: {result['chunk']} with id: {result['chunk_id']}\n"
    prompt += f"Which of the above is the best match for thie provided query: '{query}'?\n"
    prompt += "Return only the id of the best matching chunk."

    print(prompt)  # Debug print to see the constructed prompt
    best_rank = prompt_llm(prompt)  # This should return the full chunk data with properties
    print(best_rank)
    pattern = r'id:\s*(\d+)'
    
    # Search for the pattern in the response_text
    match = re.search(pattern, best_rank)
    
    # If a match is found, return the id, else return None
    if match:
        chunk_id = match.group(1)
        
        # Ensure chunk_id is compared as integer
        chunk_id = int(chunk_id)
        
        for result in top_k_results:
            print(chunk_id, result['chunk_id'])
            if result['chunk_id'] == chunk_id:
                resp_prompt = f"Answer the following question based on the provided text:{result['data']}\n\n"
                resp_prompt += f"Question: {query}\n\n"
                print(resp_prompt)
                ans = prompt_llm(resp_prompt)
                file_name = result['file_url'].split('/')[-1]
                if (result['page_number'] > 1):
                    file_name += ' page ' + str(result['page_number'])
                file_url = f"{result['file_url']}?page={result['page_number']}"
                
                # Construct the JSON response with the formatted file link
                references = [
                    f"[{file_name}]({file_url})"
                ]
                reference_links = "\n".join(references)
                return ans + "\n\n" + reference_links
                
    else:
        result = top_k_results[0]
        resp_prompt = f"Answer the following question based on the provided text:{result['data']}\n\n"
        resp_prompt += f"Question: {query}\n\n"
        print(resp_prompt)
        ans = prompt_llm(resp_prompt)
        file_name = result['file_url'].split('/')[-1]
        if (result['page_number'] > 1):
            file_name += ' page ' + str(result['page_number'])
            file_url = f"{result['file_url']}?page={result['page_number']}"
                
            # Construct the JSON response with the formatted file link
            references = [
                    f"[{file_name}]({file_url})"
            ]
            reference_links = "\n".join(references)
            return ans + "\n\n" + reference_links


    return best_rank

   

def generate_response(query, best_answer_data):
    # response_prompt = f"Generate response for query: '{query}' using best answer data: '{best_answer_data}'"
    # response = prompt_llm(response_prompt)
    # return response
    return best_answer_data

