
BUSINESS

COMPANY NAME, DEFINED OFFERING , DEFINED AUDIENCE , PROBLEM COMPANY IS SOLVING
WHAT WE DO
Syntext AI is a conversational search engine providing concise, cited answers to user (knowledge worker) queries by retrieving information from the web and uploaded documents.
Syntext leverages advanced language models (LLMs) to perform tasks via a natural language interface. Users interact with these tools using natural language, making them intuitive and user-friendly. The problem we solve is efficiency of information retrieval and/or processing using contextual understanding to deliver relevant results or outputs.

What's our secret sauce?
Lean Organization of domain experts...

DEFINED MARKET, DEFINED VALUE and Competitors (2)
This is a growing market with free PRODUCTS LIKE Google NotebookLLM and paid offerings PerplexityAI.  it validates the idea, last year Perplexity made ...

DIFFERENTIATOR
What's our differentiator?

CURRENT STATE OF AFFAIRS (PRODUCT, TEAM OR COMPANY)
Currently I am the only builder with AI agents. 

ASK
I need 10K a month for runway 

WHAT I'LL DO WITH THE ASK
hire marketers and pay influencers for product Ads
hire front end designers and pay for Infrastructure (VM + LLM)


TECHNICAL


RAG Pipeline for queries on documents and web 
https://github.com/NirDiamant/RAG_Techniques
https://github.com/NirDiamant/RAG_Techniques/blob/main/all_rag_techniques/crag.ipynb




CHUNK TEXT AND STORE IN DB
Same DB store for vectors and app state aka  no specialty vectorDB. sqlite supports vectors if we could figure out the litestream docker replication and backup to google cloud storage I'd use it.

multilingual embedding*




IDEAS
- PDF to Markdown FOR BETTER CAPTURING OF  FIGURES AND TABLES EXTRACTS*
Later using a Multi Modal Model (Pixtral / Qwen / GPT Vision), we ingest information from these type of documents visually let the LLM extract information for us visually
https://github.com/kkaarrss/pdf-to-markup/blob/main/pdf_reader.py

- resolve intent, gather context (chat history, web, documents), generate response


Deploying
1 BUILD IMAGE

Build docker build --no-cache -t syntextaiapp .

RUN docker run --rm -p 3000:3000 --env-file .env --memory 2g syntextaiapp

TAG docker tag syntextaiapp:latest osasdeeon/syntextai:latest

PUSH docker push osasdeeon/syntextai:latest

APP PLATFORM CONNECTS TO DOCKER HUB AND DEPLOYS PUSHED IMAGE

2 DEPLOY DROPLET 

3 Copy env file and deploy scripts

copy .env.prod, docker-compose-prod.yml and deploy.sh files


scp  /Users/osas/Documents/dev/app/deploy.sh  /Users/osas/Documents/dev/app/.env.prod root@178.128.236.126:/home/root/

ssh root@178.128.236.126

4 Deploy script
on server chmod +x deploy.sh
 ./deploy.sh

rename files

Troubleshooting scp may fail cos of key gen 
Open the file: nano ~/.ssh/known_hosts 
Go to a specific line: Ctrl + _ (underscore), 
enter the line number, 
press Enter 
Delete the line: Ctrl + K
Save and exit: Ctrl + X, Y, Enter


how to override searxng settings
docker run -d -p 8888:8080 --name searxng \
  -v $(pwd)/settings.yml:/etc/searxng/settings.yml \
  searxng/searxng:latest
