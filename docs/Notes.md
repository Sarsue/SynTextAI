
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



Document

1. Document Extraction high accuracy here is half the battle
PDF extract to text
Video using fastwhisper to convert audio to text.
save chunks and Page / Segment summary 





2. CHUNK TEXT AND STORE IN DB
Same DB store for vectors and app state aka  no specialty vectorDB. sqlite supports vectors if we could figure out the litestream docker replication and backup to google cloud storage I'd use it.


We need to retrieve documents  chunk alongside the page / segment summary they belong to  
evaluate document chunk retrieved with query for relevance
if scores are high > 0.7 generate response from document
if scores less than 0.3 generate response from web search
else concatenate document and web search

IMPROVEMENTS
- Use SqlAlchemy as ORM so there isnt sql specific code for Postgres or Sqllite in code.  Use Alembic for handing migrations. I abandoned using litestream and sqllite for replication and back up for using a managed instance on Digital Ocean.

OUTSTANDING
- PDF to Markdown FOR BETTER CAPTURING OF  FIGURES AND TABLES EXTRACTS*
Later using a Multi Modal Model (Pixtral / Qwen / GPT Vision), we ingest information from these type of documents visually let the LLM extract information for us visually
https://github.com/kkaarrss/pdf-to-markup/blob/main/pdf_reader.py

- Web Search Improvements for better answers 


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

scp deploy.sh docker-compose-prod.yml root@147.182.150.68:/root/ 
scp /Users/osas/Documents/dev/docsynth/deploy.sh root@146.190.246.13:/root/
scp  /Users/osas/Documents/dev/app/deploy.sh  /Users/osas/Documents/dev/app/.env root@178.128.236.126:/home/root/

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


